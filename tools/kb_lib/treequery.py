from __future__ import annotations

from typing import Any

from . import dedup, indexer, taxonomy
from .config import Config


def _score(query: str, text: str) -> float:
    q = dedup.normalize(query)
    t = dedup.normalize(text)
    if not q or not t:
        return 0.0
    if q in t:
        return 1.0
    return dedup.jaccard(dedup.trigrams(q), dedup.trigrams(t))


def _nodes(cfg: Config) -> list[dict[str, Any]]:
    data = taxonomy.load(cfg.taxonomy)
    return [
        {
            "path": path,
            "title": str(node.get("title") or path.rsplit("/", 1)[-1]),
            "description": str(node.get("description") or ""),
        }
        for path, node in taxonomy.walk(data["domains"])
    ]


def _card_counts(cfg: Config) -> dict[str, int]:
    counts: dict[str, int] = {}
    for card in indexer.all_cards(cfg):
        counts[card["path"]] = counts.get(card["path"], 0) + 1
    return counts


def query_paths(cfg: Config, query: str, domain: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
    counts = _card_counts(cfg)
    cards_by_path: dict[str, list[dict[str, Any]]] = {}
    for card in indexer.all_cards(cfg):
        cards_by_path.setdefault(card["path"], []).append(card)
    results = []
    for node in _nodes(cfg):
        path = node["path"]
        if domain and not (path == domain or path.startswith(domain + "/")):
            continue
        card_text = " ".join(
            f"{card['title']} {' '.join(card['tags'])}" for card in cards_by_path.get(path, [])[:30]
        )
        score = max(
            _score(query, node["title"]) * 1.0,
            _score(query, node["description"]) * 0.6,
            _score(query, card_text) * 0.5,
        )
        if score <= 0:
            continue
        results.append(
            {
                **node,
                "cards": counts.get(path, 0),
                "score": round(score, 4),
                "readme": f"knowledge/{path}/README.md",
            }
        )
    results.sort(key=lambda item: (item["score"], item["cards"]), reverse=True)
    return results[:limit]


def check_path(
    cfg: Config,
    path: str,
    title: str | None = None,
    description: str | None = None,
    limit: int = 5,
) -> dict[str, Any]:
    nodes = _nodes(cfg)
    proposed_title = title or path.rsplit("/", 1)[-1]
    proposed_desc = description or ""
    exact = next((node for node in nodes if node["path"] == path), None)
    scored = []
    for node in nodes:
        if node["path"] == path:
            continue
        title_score = _score(proposed_title, node["title"])
        desc_score = _score(proposed_desc, node["description"]) if proposed_desc else 0.0
        score = max(title_score, desc_score * 0.6)
        if score <= 0:
            continue
        verdict = "similar"
        if dedup.normalize(proposed_title) == dedup.normalize(node["title"]) or title_score >= 0.85:
            verdict = "duplicate"
        elif score < 0.6:
            verdict = "related"
        scored.append(
            {
                "path": node["path"],
                "title": node["title"],
                "description": node["description"],
                "score": round(score, 4),
                "verdict": verdict,
            }
        )
    scored.sort(key=lambda item: item["score"], reverse=True)
    if exact is not None:
        verdict = "exists"
    elif scored and scored[0]["verdict"] == "duplicate":
        verdict = "duplicate"
    elif scored and scored[0]["verdict"] == "similar":
        verdict = "similar"
    else:
        verdict = "new"
    return {
        "path": path,
        "title": proposed_title,
        "description": proposed_desc,
        "verdict": verdict,
        "exact": exact,
        "candidates": scored[:limit],
    }
