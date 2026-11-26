from __future__ import annotations

import json
import math
import re
import sqlite3
from dataclasses import dataclass
from typing import Any, Iterable

from . import dedup
from .config import Config
from .embedder import Embedder, EmbeddingUnavailable, unpack_vector

RRF_K = 60
REVERSE_DOMAINS = {
    "code-analysis",
    "binary",
    "mobile",
    "iot",
    "tools",
    "defense",
}


def _quote(term: str) -> str:
    return '"' + term.replace('"', '""') + '"'


def _trigram_query(text: str) -> str | None:
    terms = [t for t in re.split(r"[\s,，、;；/|]+", text.strip()) if len(dedup.normalize(t)) >= 3]
    if not terms:
        norm = dedup.normalize(text)
        if len(norm) >= 3:
            terms = [norm]
    if not terms:
        return None
    return " OR ".join(_quote(t) for t in terms)


def _token_query(text: str) -> str | None:
    toks = [t for t in dedup.tokens(text) if len(t) >= 1]
    if not toks:
        return None
    return " OR ".join(_quote(t) for t in toks)


def _cosine_rows(query_vector, rows: Iterable[tuple[str, bytes, int]]) -> list[tuple[str, float]]:
    scored: list[tuple[str, float]] = []
    qv = list(query_vector)
    for card_id, blob, dim in rows:
        vector = unpack_vector(blob, dim)
        if len(vector) != len(qv):
            continue
        dot = float((vector * qv).sum())
        scored.append((card_id, dot))
    scored.sort(key=lambda item: item[1], reverse=True)
    return scored


@dataclass
class SearchResult:
    card: dict[str, Any]
    score: float
    snippet: str
    sources: list[str]
    match_reason: str = ""


class SearchEngine:
    def __init__(self, cfg: Config, embedder: Embedder | None = None):
        self.cfg = cfg
        self.embedder = embedder or Embedder(cache_dir=cfg.data / "models")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.cfg.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _card_map(self, conn: sqlite3.Connection, ids: Iterable[str]) -> dict[str, dict[str, Any]]:
        ids = list(dict.fromkeys(ids))
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(f"SELECT * FROM cards WHERE id IN ({placeholders})", ids).fetchall()
        out: dict[str, dict[str, Any]] = {}
        for row in rows:
            data = dict(row)
            for key in ("tags", "aliases", "cve"):
                data[key] = json.loads(data.get(key) or "[]")
            out[data["id"]] = data
        return out

    def _exact_ids(self, conn: sqlite3.Connection, query: str) -> list[str]:
        q = query.strip().lower()
        if not q:
            return []
        rows = conn.execute(
            """
            SELECT id FROM cards
            WHERE lower(title) = ? OR lower(id) = ? OR lower(replace(id, '-', ' ')) = ?
            UNION
            SELECT cards.id FROM cards, json_each(cards.aliases) AS a
            WHERE lower(a.value) = ?
            """,
            (q, q, q, q),
        ).fetchall()
        return [row[0] for row in rows]

    def _fts(self, conn: sqlite3.Connection, table: str, match: str, limit: int = 50) -> list[tuple[str, float, str]]:
        try:
            rows = conn.execute(
                f"""
                SELECT id, bm25({table}) AS rank,
                       snippet({table}, 4, '…', '…', '…', 14) AS snip
                FROM {table}
                WHERE {table} MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (match, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        return [(row[0], float(row[1]), str(row[2] or "")) for row in rows]

    def _vector(self, conn: sqlite3.Connection, query: str, limit: int = 50) -> list[tuple[str, float]]:
        try:
            query_vector = self.embedder.encode_one(query)
        except EmbeddingUnavailable:
            return []
        rows = conn.execute("SELECT card_id, vector, dim FROM embeddings").fetchall()
        return _cosine_rows(query_vector, [(r[0], r[1], r[2]) for r in rows])[:limit]

    def search(
        self,
        query: str,
        mode: str = "hybrid",
        domain: str | None = None,
        tags: list[str] | None = None,
        status: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        query = (query or "").strip()
        if not query:
            return []
        conn = self._connect()
        try:
            exact = self._exact_ids(conn, query)
            trigram: list[tuple[str, float, str]] = []
            tokens: list[tuple[str, float, str]] = []
            vector: list[tuple[str, float]] = []

            if mode in ("keyword", "hybrid"):
                tq = _trigram_query(query)
                if tq:
                    trigram = self._fts(conn, "cards_fts_trigram", tq)
                tokq = _token_query(query)
                if tokq:
                    tokens = self._fts(conn, "cards_fts_tokens", tokq)
            if mode in ("semantic", "hybrid"):
                vector = self._vector(conn, query)

            rank_lists: list[tuple[str, list[str]]] = []
            if exact:
                rank_lists.append(("exact", exact))
            if trigram:
                rank_lists.append(("trigram", [row[0] for row in trigram]))
            if tokens:
                rank_lists.append(("tokens", [row[0] for row in tokens]))
            if vector:
                rank_lists.append(("vector", [row[0] for row in vector]))

            if not rank_lists:
                like = f"%{query}%"
                rows = conn.execute(
                    "SELECT id FROM cards WHERE title LIKE ? OR body LIKE ? LIMIT ?",
                    (like, like, limit * 5),
                ).fetchall()
                rank_lists.append(("like", [row[0] for row in rows]))

            scores: dict[str, float] = {}
            reasons: dict[str, list[str]] = {}
            snippets: dict[str, str] = {}
            for source, ids in rank_lists:
                for rank, card_id in enumerate(ids, start=1):
                    scores[card_id] = scores.get(card_id, 0.0) + 1.0 / (RRF_K + rank)
                    reasons.setdefault(card_id, []).append(source)
            for card_id in exact:
                scores[card_id] = scores.get(card_id, 0.0) + 0.25
            for card_id, _rank, snip in trigram + tokens:
                if snip and card_id not in snippets:
                    snippets[card_id] = snip

            cards = self._card_map(conn, scores.keys())
            max_score = max(scores.values()) if scores else 1.0
            status_weight = {"verified": 1.02, "draft": 1.0, "deprecated": 0.98}
            confidence_weight = {"high": 1.01, "medium": 1.0, "low": 0.99}
            filtered: list[tuple[float, dict[str, Any], list[str]]] = []
            for card_id, score in scores.items():
                card = cards.get(card_id)
                if card is None:
                    continue
                if domain and not (card["domain"] == domain or card["path"].startswith(domain + "/") or card["path"] == domain):
                    continue
                if tags and not set(tags).issubset(set(card["tags"])):
                    continue
                if status and card["status"] != status:
                    continue
                # Normalize first so status/confidence weights nudge rather than
                # completely reorder the retrieval ranking.
                normalized = score / max_score if max_score else 0.0
                normalized *= status_weight.get(card["status"], 1.0)
                normalized *= confidence_weight.get(card["confidence"], 1.0)
                filtered.append((normalized, card, reasons.get(card_id, [])))

            filtered.sort(key=lambda item: item[0], reverse=True)
            results = []
            for score, card, reason in filtered[:limit]:
                snippet = snippets.get(card["id"]) or self._fallback_snippet(card, query)
                results.append(
                    {
                        "id": card["id"],
                        "path": card["path"],
                        "file_path": card["file_path"],
                        "filename": card["filename"],
                        "title": card["title"],
                        "score": round(score, 6),
                        "snippet": snippet,
                        "status": card["status"],
                        "confidence": card["confidence"],
                        "tags": card["tags"],
                        "aliases": card["aliases"],
                        "cve": card["cve"],
                        "match_reason": "+".join(reason),
                    }
                )
            return results
        finally:
            conn.close()

    @staticmethod
    def _fallback_snippet(card: dict[str, Any], query: str) -> str:
        body = str(card.get("body") or "")
        if not body:
            return ""
        positions = [body.lower().find(term.lower()) for term in query.split() if term]
        positions = [p for p in positions if p >= 0]
        start = max(0, min(positions) - 40) if positions else 0
        return body[start : start + 180].replace("\n", " ")

    def search_multi(
        self,
        queries: Iterable[str],
        mode: str = "hybrid",
        domain: str | None = None,
        tags: list[str] | None = None,
        status: str | None = None,
        limit: int = 10,
        per_query: int = 30,
    ) -> list[dict[str, Any]]:
        """Run several query rewrites and merge their rankings with RRF."""
        cleaned: list[str] = []
        for query in queries:
            query = (query or "").strip()
            if query and query not in cleaned:
                cleaned.append(query)
        if not cleaned:
            return []
        if len(cleaned) == 1:
            return self.search(cleaned[0], mode=mode, domain=domain, tags=tags, status=status, limit=limit)
        merged: dict[str, dict[str, Any]] = {}
        for query in cleaned:
            results = self.search(
                query, mode=mode, domain=domain, tags=tags, status=status, limit=per_query
            )
            for rank, result in enumerate(results, start=1):
                entry = merged.setdefault(result["id"], {"result": result, "score": 0.0, "hits": []})
                entry["score"] += 1.0 / (RRF_K + rank)
                entry["hits"].append(query)
        ordered = sorted(merged.values(), key=lambda item: item["score"], reverse=True)[:limit]
        top_score = ordered[0]["score"] if ordered else 1.0
        out: list[dict[str, Any]] = []
        for entry in ordered:
            result = dict(entry["result"])
            result["score"] = round(entry["score"] / top_score, 6) if top_score else 0.0
            reason = result.get("match_reason", "")
            result["match_reason"] = (reason + "+" if reason else "") + f"multi-query:{len(entry['hits'])}"
            out.append(result)
        return out

    def similar(self, card_id: str, limit: int = 10) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM embeddings WHERE card_id = ?", (card_id,)).fetchone()
            cards = self._card_map(conn, [card_id])
            if row is not None:
                qv = unpack_vector(row["vector"], row["dim"])
                rows = conn.execute("SELECT card_id, vector, dim FROM embeddings WHERE card_id != ?", (card_id,)).fetchall()
                ranked = _cosine_rows(qv, [(r[0], r[1], r[2]) for r in rows])[:limit]
                card_map = self._card_map(conn, [r[0] for r in ranked])
                return [
                    {
                        "id": cid,
                        "title": card_map[cid]["title"],
                        "path": card_map[cid]["path"],
                        "score": round(score, 6),
                        "status": card_map[cid]["status"],
                    }
                    for cid, score in ranked
                    if cid in card_map
                ]
            # Fallback: same technique or overlapping tags.
            card = cards.get(card_id)
            if card is None:
                return []
            terms = " ".join([card["technique"], *card["tags"]])
            if not terms.strip():
                return []
            results = self.search(terms, mode="keyword", limit=limit + 1)
            return [r for r in results if r["id"] != card_id][:limit]
        finally:
            conn.close()

    def related(self, card_id: str, limit: int = 10) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            cards = self._card_map(conn, [card_id])
            card = cards.get(card_id)
            if card is None:
                return []
            rows = conn.execute("SELECT * FROM cards WHERE id != ?", (card_id,)).fetchall()
            scored: list[tuple[float, dict[str, Any]]] = []
            tags = set(card["tags"])
            cves = set(card["cve"])
            for row in rows:
                data = dict(row)
                for key in ("tags", "aliases", "cve"):
                    data[key] = json.loads(data.get(key) or "[]")
                score = 0.0
                if card["technique"] and data["technique"] == card["technique"]:
                    score += 2.0
                score += len(tags & set(data["tags"])) * 0.5
                score += len(cves & set(data["cve"])) * 1.0
                if score > 0:
                    scored.append((score, data))
            scored.sort(key=lambda item: item[0], reverse=True)
            return [
                {
                    "id": data["id"],
                    "title": data["title"],
                    "path": data["path"],
                    "score": score,
                    "status": data["status"],
                }
                for score, data in scored[:limit]
            ]
        finally:
            conn.close()

    def cves(self, component: str | None = None, cve: str | None = None) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            clauses = []
            params: list[str] = []
            if component:
                clauses.append("component = ?")
                params.append(component)
            if cve:
                clauses.append("upper(cve) = ?")
                params.append(cve.upper())
            where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
            rows = conn.execute(f"SELECT * FROM cves{where} ORDER BY component, cve", params).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()


def reverse_hints(cfg: Config, facts: dict[str, Any], limit: int = 8) -> list[dict[str, Any]]:
    parts: list[str] = []
    for key in ("format", "arch", "compiler", "framework", "notes"):
        value = facts.get(key)
        if value:
            parts.append(str(value))
    for key in ("packer", "sections", "imports", "strings", "anti_debug"):
        value = facts.get(key)
        if isinstance(value, (list, tuple)):
            parts.extend(str(item) for item in value)
        elif value:
            parts.append(str(value))
    query = " ".join(parts).strip()
    if not query:
        return []
    engine = SearchEngine(cfg)
    results = engine.search(query, mode="hybrid", limit=limit * 3)
    filtered = [r for r in results if r["path"].split("/", 1)[0] in REVERSE_DOMAINS]
    if not filtered:
        filtered = results
    out = []
    keywords = {p.lower() for p in parts if len(p) >= 2}
    for result in filtered[:limit]:
        haystack = " ".join([result["title"], " ".join(result["tags"]), " ".join(result["aliases"])]).lower()
        matched = sorted({k for k in keywords if k in haystack})
        result["match_reason"] = "facts: " + (", ".join(matched[:6]) if matched else result.get("match_reason", ""))
        out.append(result)
    return out
