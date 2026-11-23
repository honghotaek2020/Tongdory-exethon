"""확정한 폴더 구조와 원본 파일명·상대 경로를 로컬에서 대조한다."""

import re
from collections import defaultdict
from pathlib import PurePath

from tongdory2.spec.normalize import clean, compare_key, parse


def _words(value: str) -> tuple[str, ...]:
    return tuple(re.findall(r"[^\W_]+", clean(value).casefold()))


class LocalFolderRules:
    def __init__(self, nodes: list[dict]) -> None:
        adopted = [n for n in nodes if n["status"] == "ADOPTED"]
        parents = {n.get("parent_id") for n in adopted}
        self.nodes = {n["node_id"]: n for n in adopted if n["role"] == "DEST"
                      and n["profile"] in ("common", "build") and n["node_id"] not in parents}
        self.names = defaultdict(set)
        self.paths = defaultdict(set)
        self.width = 0
        for node_id, node in self.nodes.items():
            parts = PurePath(node["display_path"]).parts
            self.paths[tuple(parse(part).key for part in parts)].add(node_id)
            for name in [node["label"], *(parse(alias).name for alias in node.get("aliases", []))]:
                words = _words(name)
                if len("".join(words)) >= 3:
                    self.names[compare_key("".join(words))].add(node_id)
                    self.width = max(self.width, len(words))

    def candidates(self, relative_path: str) -> list[dict]:
        path = PurePath(relative_path)
        words = _words(path.stem)
        if set(words) & {"양식", "서식", "템플릿", "샘플", "참고용"}:
            return []
        found = set()
        for start in range(len(words)):
            for end in range(start + 1, min(start + self.width, len(words)) + 1):
                found.update(self.names.get(compare_key("".join(words[start:end])), ()))
        # 한 단계짜리 원본 폴더명은 흔하므로, 두 단계 이상 일치할 때 경로를 근거로 쓴다.
        if len(path.parent.parts) >= 2:
            found.update(self.paths.get(tuple(parse(part).key for part in path.parent.parts), ()))
        return [self.nodes[node_id] for node_id in sorted(found)]
