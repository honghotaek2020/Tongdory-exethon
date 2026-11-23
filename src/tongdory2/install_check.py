"""실행본의 웹 파일과 로컬 파일 처리 동작을 임시 폴더에서 점검한다."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient
from uvicorn import Config

from tongdory2.config import Settings
from tongdory2.fsx.roots import Role
from tongdory2.fsx.scan import scan_collect
from tongdory2.plan.executor import execute, save_plan, undo
from tongdory2.plan.planner import build_plan
from tongdory2.plan.safety import GateContext
from tongdory2.web.app import create_app


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(f"[배포 점검] {message}")


def check_installation() -> None:
    # Windows의 기본 임시 폴더는 보호 대상인 AppData 아래에 있다.
    work_parent = Path.home() if os.name == "nt" else None
    with TemporaryDirectory(prefix="tongdory-check-", dir=work_parent) as tmp:
        home = Path(tmp).resolve() / "home"
        source = home / "Downloads" / "하위폴더" / "회의록.txt"
        destination = home / "Documents" / "정리"
        source.parent.mkdir(parents=True)
        destination.mkdir(parents=True)
        original = "배포 검증용 회의록".encode()
        source.write_bytes(original)
        old = time.time() - 86400
        os.utime(source, (old, old))
        settings = Settings(_env_file=None, LINER_API_KEY="", TONGDORY_HOME=str(home),
                            TONGDORY_DATA_DIR=str(Path(tmp) / "data"), TONGDORY_PORT=8765, RECENT_HOURS=2)
        app = create_app(settings)
        state = app.state.t
        try:
            Config(app, log_config=None).load()
            with TestClient(app, base_url="http://127.0.0.1:8765", follow_redirects=False) as client:
                for url in ("/", "/setup/structure", "/static/app.css", "/static/setup.js"):
                    _require(client.get(url).status_code == 200, f"웹 화면 또는 리소스 로드 실패 ({url})")
                for step, data, location in (
                    ("structure", {"paths": "회의록"}, "/setup/sources"),
                    ("sources", {"paths": "~/Downloads"}, "/setup/destination"),
                    ("destination", {"path": "~/Documents/정리"}, "/"),
                ):
                    response = client.post(f"/setup/{step}", data=data)
                    _require(response.status_code == 303 and response.headers.get("location") == location,
                             f"폴더 설정 실패 ({step})")

            scope = state.scope()
            files = scan_collect(scope.roots, scope, include_current=False)
            _require(any(item.path.samefile(source) for item in files if not item.is_dir and not item.is_link),
                     "등록한 하위 폴더 파일을 찾지 못함")
            nodes = [{"node_id": "meeting", "status": "ADOPTED", "display_path": "회의록"}]
            entries = [{"entry_id": "test", "path": str(source), "name": source.name,
                        "verdict": "SPEC_NODE", "node_path": "회의록", "sensitivity": "L1"}]
            storage = next(root.path for root in scope.roots if root.role == Role.STORAGE)
            plan = build_plan(entries, {"test"}, nodes, destination, storage, home, False)
            plan_id = save_plan(state.db.conn, "check", plan.to_dict())
            execution = execute(state.db.conn, plan_id, GateContext(scope, destination, storage, set()))
            target = destination / "회의록" / source.name
            _require(execution.moved == 1 and not source.exists() and target.read_bytes() == original,
                     "임시 파일 이동 실패")
            recovery = undo(state.db.conn, execution.batch_id, scope)
            _require(not recovery.stopped and not target.exists() and source.read_bytes() == original,
                     "임시 파일 되돌리기 실패")
        finally:
            state.db.conn.close()
    logging.getLogger(__name__).info("[배포 점검] 웹 화면·폴더 등록·하위 파일 조사·이동·되돌리기 통과")
