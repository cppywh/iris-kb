"""把 data/index.json 精简成前端用的静态数据。只读源索引，不改卡片。"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "index.json"
OUT = Path(__file__).resolve().parent / "data"

FIELDS = (
    "id", "title", "path", "filename", "domain", "technique",
    "tags", "aliases", "cve", "severity", "confidence", "status",
    "updated", "affected_versions",
)


def main() -> None:
    raw = json.loads(SRC.read_text(encoding="utf-8"))
    cards = []
    for c in raw["cards"]:
        item = {k: c.get(k) for k in FIELDS if c.get(k) not in (None, "", [], {})}
        item["url"] = f"../knowledge/{c['path']}/{c['filename']}"
        cards.append(item)

    domains = Counter(c["domain"] for c in cards if c.get("domain"))
    paths = Counter(c["path"] for c in cards if c.get("path"))
    status = Counter(c.get("status", "unknown") for c in cards)

    OUT.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": raw.get("generated_at"),
        "stats": {
            "cards": len(cards),
            "domains": dict(domains.most_common()),
            "status": dict(status),
        },
        "paths": dict(sorted(paths.items())),
        "cards": cards,
    }
    target = OUT / "cards.json"
    target.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"cards={len(cards)} domains={len(domains)} paths={len(paths)} "
          f"size={target.stat().st_size / 1048576:.1f}MB -> {target}")


if __name__ == "__main__":
    main()
