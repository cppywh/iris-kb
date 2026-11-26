from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from .config import Config
from . import indexer


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _staging_records(cfg: Config) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    root = cfg.ingest / "staging"
    if not root.exists():
        return records
    for path in root.glob("*/cards.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        article_id = str(record.get("article_id") or path.parent.name)
        records[article_id] = record
    return records


def _claims(cfg: Config) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    root = cfg.ingest / "state" / "claims"
    if not root.exists():
        return records
    for path in root.glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        records[str(record.get("id") or path.stem)] = record
    return records


def build(cfg: Config) -> dict[str, Any]:
    state = cfg.ingest / "state"
    manifest = {str(row["id"]): row for row in _read_jsonl(state / "manifest.jsonl")}
    processed = {str(row["id"]): row for row in _read_jsonl(state / "processed.jsonl")}
    queue = {str(row["id"]): row for row in _read_jsonl(state / "queue.jsonl")}
    staging = _staging_records(cfg)
    claims = _claims(cfg)
    existing_card_ids = {card.id for card in indexer.discover_cards(cfg).cards}

    ids = set(manifest) | set(processed) | set(queue) | set(staging) | set(claims)
    records: list[dict[str, Any]] = []
    for article_id in sorted(ids):
        meta = manifest.get(article_id) or queue.get(article_id) or {}
        if article_id in processed:
            status = "committed"
            distilled_at = processed[article_id].get("processed_at", "")
            cards = [cid for cid in processed[article_id].get("cards", []) if cid in existing_card_ids]
            worker = processed[article_id].get("worker")
        elif article_id in staging:
            status = "distilled_pending_commit"
            distilled_at = staging[article_id].get("distilled_at", "")
            cards = [
                card.get("id")
                for card in staging[article_id].get("cards", [])
                if card.get("id") in existing_card_ids
            ]
            worker = staging[article_id].get("worker")
        elif article_id in claims:
            status = "in_progress"
            distilled_at = ""
            cards = []
            worker = claims[article_id].get("worker")
        elif article_id in queue:
            status = "queued"
            distilled_at = ""
            cards = []
            worker = None
        else:
            status = "known"
            distilled_at = ""
            cards = []
            worker = None
        records.append(
            {
                "site": "xz",
                "article_id": article_id,
                "title": meta.get("title") or (queue.get(article_id) or {}).get("title", ""),
                "category": meta.get("category") or (queue.get(article_id) or {}).get("category", ""),
                "pub_at": meta.get("pub_at") or (queue.get(article_id) or {}).get("pub_at", ""),
                "url": meta.get("url") or (queue.get(article_id) or {}).get("url", ""),
                "status": status,
                "distilled_at": distilled_at,
                "worker": worker,
                "cards": cards,
                "card_count": len(cards),
            }
        )

    records.sort(key=lambda row: ({"committed": 0, "distilled_pending_commit": 1, "in_progress": 2, "queued": 3, "known": 4}.get(row["status"], 9), row["article_id"]))
    counts = Counter(row["status"] for row in records)
    output = {
        "site": "xz",
        "total": len(records),
        "counts": dict(counts),
        "cards": sum(row["card_count"] for row in records),
        "records": records,
    }

    (state / "distilled.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in records) + ("\n" if records else ""),
        encoding="utf-8",
    )
    (state / "distilled.md").write_text(render_markdown(output), encoding="utf-8")
    return output


def render_markdown(data: dict[str, Any]) -> str:
    lines = [
        "# 先知社区蒸馏台账",
        "",
        f"- 文章总数: {data['total']}",
        f"- 状态: {data['counts']}",
        f"- 已产出卡片引用: {data['cards']}",
        "",
        "| 状态 | 文章 | 分类 | 发布时间 | 蒸馏时间 | 卡片数 | 卡片 |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in data["records"]:
        title = str(row["title"]).replace("|", "\\|")
        cards = ", ".join(f"`{card}`" for card in row["cards"]) or ""
        lines.append(
            f"| {row['status']} | [{title}]({row['url']}) | {row['category']} | {row['pub_at'][:10]} | {row['distilled_at'][:19]} | {row['card_count']} | {cards} |"
        )
    return "\n".join(lines) + "\n"


def summary(cfg: Config) -> dict[str, Any]:
    data = build(cfg)
    return {key: value for key, value in data.items() if key != "records"}
