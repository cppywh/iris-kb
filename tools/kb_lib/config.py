from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    root: Path
    knowledge: Path
    cve: Path
    data: Path
    taxonomy: Path
    registry: Path
    ingest: Path
    web: Path
    skills: Path
    db_path: Path
    index_json: Path

    def rel(self, path: Path | str) -> str:
        return Path(path).resolve().relative_to(self.root.resolve()).as_posix()


def load(root: str | Path | None = None) -> Config:
    if root is None:
        root = os.environ.get("IRIS_KB_HOME") or os.environ.get("VULN_MASTERHUB_HOME")
    if root is None:
        root = Path(__file__).resolve().parents[2]
    root = Path(root).expanduser().resolve()
    data = root / "data"
    return Config(
        root=root,
        knowledge=root / "knowledge",
        cve=root / "cve",
        data=data,
        taxonomy=root / "taxonomy.yaml",
        registry=root / "registry",
        ingest=root / "ingest",
        web=root / "web",
        skills=root / "skills",
        db_path=data / "kb.sqlite",
        index_json=data / "index.json",
    )
