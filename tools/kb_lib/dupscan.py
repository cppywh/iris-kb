from __future__ import annotations

import json
import sqlite3
from typing import Any

from . import dedup, indexer
from .config import Config
from .embedder import Embedder, EmbeddingUnavailable, unpack_vector

VERDICT_ORDER = {"duplicate": 0, "likely_duplicate": 1, "related": 2, "new": 3}


def _load_vectors(cfg: Config) -> dict[str, list[float]]:
    try:
        conn = sqlite3.connect(cfg.db_path)
        try:
            rows = conn.execute("SELECT card_id, vector, dim FROM embeddings").fetchall()
        finally:
            conn.close()
    except Exception:
        return {}
    return {card_id: unpack_vector(blob, dim) for card_id, blob, dim in rows}


def _augment_vectors(cfg: Config, entries: list[dict[str, Any]], vectors: dict[str, list[float]]) -> dict[str, list[float]]:
    """Embed staging cards that were added after the last embed run."""
    missing = [entry for entry in entries if entry["card"]["id"] not in vectors]
    if not missing:
        return vectors
    try:
        embedder = Embedder(cache_dir=cfg.data / "models")
    except Exception:
        return vectors
    for entry in missing:
        card = entry["card"]
        text = "\n".join([card["title"], " ".join(card["tags"]), card["body"][:2400]])
        try:
            vectors[card["id"]] = embedder.encode_one(text)
        except EmbeddingUnavailable:
            return vectors
        except Exception:
            continue
    return vectors


def staging_cards(cfg: Config) -> list[dict[str, Any]]:
    """Collect every card referenced by the current staging batch."""
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for cards_json in sorted((cfg.ingest / "staging").glob("*/cards.json")):
        article = cards_json.parent.name
        try:
            data = json.loads(cards_json.read_text(encoding="utf-8"))
        except Exception:
            continue
        for item in data.get("cards") or []:
            card_id = item.get("id")
            if not card_id or card_id in seen:
                continue
            card = indexer.get_card(cfg, card_id)
            if card is None:
                continue
            seen.add(card_id)
            entries.append({"article": article, "card": card})
    return entries


def scan(cfg: Config, min_verdict: str = "related", limit: int = 200) -> dict[str, Any]:
    """Pairwise-cluster staging cards so cross-worker duplicates surface before merge."""
    entries = staging_cards(cfg)
    vectors = _augment_vectors(cfg, entries, _load_vectors(cfg))
    fingerprints = [
        dedup.fingerprint(
            dedup.Draft(
                title=entry["card"]["title"],
                body=entry["card"]["body"],
                tags=entry["card"]["tags"],
                aliases=entry["card"]["aliases"],
                technique=entry["card"].get("technique") or "",
            )
        )
        for entry in entries
    ]
    max_rank = VERDICT_ORDER.get(min_verdict, VERDICT_ORDER["related"])
    pairs: list[dict[str, Any]] = []
    for i in range(len(entries)):
        for j in range(i + 1, len(entries)):
            left, right = entries[i], entries[j]
            left_id = left["card"]["id"]
            right_id = right["card"]["id"]
            scores = dedup.compare_fp(
                fingerprints[i],
                fingerprints[j],
                candidate_vector=vectors.get(right_id),
                draft_vector=vectors.get(left_id),
            )
            if VERDICT_ORDER[scores.verdict] > max_rank:
                continue
            pairs.append(
                {
                    "a": {
                        "id": left_id,
                        "title": left["card"]["title"],
                        "path": left["card"]["path"],
                        "status": left["card"]["status"],
                        "article": left["article"],
                    },
                    "b": {
                        "id": right_id,
                        "title": right["card"]["title"],
                        "path": right["card"]["path"],
                        "status": right["card"]["status"],
                        "article": right["article"],
                    },
                    "verdict": scores.verdict,
                    "scores": {
                        "trigram": scores.trigram,
                        "title_trigram": scores.title_trigram,
                        "body_containment": scores.body_containment,
                        "simhash_distance": scores.simhash_distance,
                        "tag_overlap": scores.tag_overlap,
                        "cosine": scores.cosine,
                    },
                    "excerpt_a": dedup.excerpt(left["card"]["body"]),
                    "excerpt_b": dedup.excerpt(right["card"]["body"]),
                }
            )
    pairs.sort(
        key=lambda pair: (
            VERDICT_ORDER[pair["verdict"]],
            -(pair["scores"]["cosine"] or 0.0),
            -pair["scores"]["body_containment"],
        )
    )
    return {
        "articles": len({entry["article"] for entry in entries}),
        "cards": len(entries),
        "total_pairs": len(pairs),
        "shown_pairs": min(len(pairs), limit),
        "pairs": pairs[:limit],
    }
