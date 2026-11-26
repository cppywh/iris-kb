from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .config import Config


def _count_jsonl(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _state_path(cfg: Config) -> Path:
    return cfg.ingest / "state" / "batch_state.json"


def load_state(cfg: Config) -> dict:
    path = _state_path(cfg)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"last_notified": 0, "started_at": datetime.now(timezone.utc).isoformat()}


def save_state(cfg: Config, state: dict) -> None:
    path = _state_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def status(cfg: Config, mark_notified: bool = False, set_interval: int | None = None) -> dict:
    state = load_state(cfg)
    if set_interval:
        state["interval"] = int(set_interval)
        save_state(cfg, state)
    interval = int(state.get("interval", 1000))
    processed = _count_jsonl(cfg.ingest / "state" / "processed.jsonl")
    manifest = _count_jsonl(cfg.ingest / "state" / "manifest.jsonl")
    queue = _count_jsonl(cfg.ingest / "state" / "queue.jsonl")
    last = int(state.get("last_notified", 0))
    next_notify = (last // interval + 1) * interval
    due = processed >= next_notify
    if mark_notified and due:
        state["last_notified"] = next_notify
        state["notified_at"] = datetime.now(timezone.utc).isoformat()
        save_state(cfg, state)
        last = next_notify
        next_notify = (last // interval + 1) * interval
        due = False
    return {
        "processed": processed,
        "manifest": manifest,
        "queue": queue,
        "interval": interval,
        "last_notified": last,
        "next_notify": next_notify,
        "remaining_to_notify": max(0, next_notify - processed),
        "due": due,
    }
