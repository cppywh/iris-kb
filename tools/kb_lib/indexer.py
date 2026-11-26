from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from . import dedup, taxonomy
from .config import Config
from .embedder import Embedder, EmbeddingUnavailable, content_hash, pack_vector
from .frontmatter import FrontmatterError, as_list, as_text, dump_frontmatter, split_frontmatter
from .markdown_util import iter_markdown_files, render_index_table, update_auto_index

CVE_RE = re.compile(r"^CVE-\d{4}-\d{4,}$", re.IGNORECASE)
REQUIRED_FIELDS = ("id", "title", "type", "domain", "path", "technique", "tags", "confidence", "status")


@dataclass
class Card:
    id: str
    path: str
    file_path: Path
    filename: str
    title: str
    type: str
    domain: str
    technique: str
    tags: list[str]
    aliases: list[str]
    cve: list[str]
    severity: str
    confidence: str
    status: str
    updated: str
    body: str
    meta: dict[str, Any] = field(default_factory=dict)
    content_hash: str = ""

    def search_text(self) -> str:
        return "\n".join(
            part
            for part in [
                self.title,
                " ".join(self.aliases),
                " ".join(self.tags),
                self.technique,
                str(self.meta.get("affected_versions") or ""),
                self.body,
            ]
            if part
        )

    def embed_text(self) -> str:
        return "\n".join(
            part
            for part in [
                self.title,
                " ".join(self.aliases),
                " ".join(self.tags),
                self.technique,
                str(self.meta.get("affected_versions") or ""),
                self.body[:2400],
            ]
            if part
        )

    def to_public(self, include_body: bool = False) -> dict[str, Any]:
        data = {
            "id": self.id,
            "path": self.path,
            "file_path": str(self.file_path),
            "filename": self.filename,
            "title": self.title,
            "type": self.type,
            "domain": self.domain,
            "technique": self.technique,
            "tags": self.tags,
            "aliases": self.aliases,
            "cve": self.cve,
            "severity": self.severity,
            "confidence": self.confidence,
            "status": self.status,
            "updated": self.updated,
            "affected_versions": self.meta.get("affected_versions"),
        }
        if include_body:
            data["body"] = self.body
            data["meta"] = self.meta
        return data


@dataclass
class Discovery:
    cards: list[Card]
    errors: list[dict[str, str]]


def parse_card(file_path: Path, cfg: Config) -> Card:
    text = file_path.read_text(encoding="utf-8")
    meta, body = split_frontmatter(text)
    rel = file_path.resolve().relative_to(cfg.knowledge.resolve())
    rel_posix = rel.as_posix()
    parent = rel.parent.as_posix()
    card_id = as_text(meta.get("id")) or rel_posix[: -len(".md")].replace("/", "-")
    title = as_text(meta.get("title")) or file_path.stem
    path = as_text(meta.get("path")) or ("" if parent == "." else parent)
    domain = as_text(meta.get("domain")) or path.split("/", 1)[0]
    tags = as_list(meta.get("tags"))
    aliases = as_list(meta.get("aliases"))
    cve = [c.upper() for c in as_list(meta.get("cve"))]
    raw = json.dumps(meta, ensure_ascii=False, sort_keys=True) + "\n" + body
    return Card(
        id=card_id,
        path=path,
        file_path=file_path,
        filename=file_path.name,
        title=title,
        type=as_text(meta.get("type"), "scenario"),
        domain=domain,
        technique=as_text(meta.get("technique")),
        tags=tags,
        aliases=aliases,
        cve=cve,
        severity=as_text(meta.get("severity")),
        confidence=as_text(meta.get("confidence")),
        status=as_text(meta.get("status")),
        updated=as_text(meta.get("updated") or meta.get("created")),
        body=body,
        meta=meta,
        content_hash=content_hash(raw),
    )


def discover_cards(cfg: Config) -> Discovery:
    cards: list[Card] = []
    errors: list[dict[str, str]] = []
    for file_path in iter_markdown_files(cfg.knowledge):
        try:
            cards.append(parse_card(file_path, cfg))
        except FrontmatterError as exc:
            errors.append({"file": str(file_path), "error": str(exc)})
        except Exception as exc:  # noqa: BLE001 - surface file-level parse failures
            errors.append({"file": str(file_path), "error": f"{type(exc).__name__}: {exc}"})
    cards.sort(key=lambda card: (card.path, card.filename))
    return Discovery(cards=cards, errors=errors)


def parse_cves(cfg: Config) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    if not cfg.cve.exists():
        return rows
    for file_path in sorted(cfg.cve.glob("*.md")):
        if file_path.name == "README.md":
            continue
        component = file_path.stem
        for line in file_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line.startswith("|"):
                continue
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if len(cells) < 4:
                continue
            cve = cells[0].strip("` ")
            if not CVE_RE.match(cve):
                continue
            rows.append(
                {
                    "component": component,
                    "cve": cve.upper(),
                    "summary": cells[1],
                    "versions": cells[2],
                    "poc": cells[3],
                }
            )
    return rows


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS cards (
            id TEXT PRIMARY KEY,
            path TEXT NOT NULL,
            file_path TEXT NOT NULL,
            filename TEXT NOT NULL,
            title TEXT NOT NULL,
            type TEXT,
            domain TEXT,
            technique TEXT,
            tags TEXT,
            aliases TEXT,
            cve TEXT,
            severity TEXT,
            confidence TEXT,
            status TEXT,
            updated TEXT,
            content_hash TEXT,
            body TEXT
        );
        CREATE TABLE IF NOT EXISTS cves (
            component TEXT NOT NULL,
            cve TEXT NOT NULL,
            summary TEXT,
            versions TEXT,
            poc TEXT,
            PRIMARY KEY (component, cve)
        );
        CREATE TABLE IF NOT EXISTS embeddings (
            card_id TEXT PRIMARY KEY,
            model TEXT,
            dim INTEGER,
            vector BLOB,
            content_hash TEXT
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS cards_fts_trigram USING fts5(
            id UNINDEXED, title, aliases, tags, body, tokenize='trigram'
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS cards_fts_tokens USING fts5(
            id UNINDEXED, title, aliases, tags, body, tokenize='unicode61'
        );
        CREATE INDEX IF NOT EXISTS idx_cards_path ON cards(path);
        CREATE INDEX IF NOT EXISTS idx_cards_domain ON cards(domain);
        CREATE INDEX IF NOT EXISTS idx_cards_status ON cards(status);
        """
    )


def _join_tokens(text: str) -> str:
    return " ".join(dedup.tokens(text))


def _insert_card(conn: sqlite3.Connection, card: Card) -> None:
    conn.execute(
        """
        INSERT INTO cards (id, path, file_path, filename, title, type, domain, technique,
                           tags, aliases, cve, severity, confidence, status, updated,
                           content_hash, body)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            card.id,
            card.path,
            str(card.file_path),
            card.filename,
            card.title,
            card.type,
            card.domain,
            card.technique,
            json.dumps(card.tags, ensure_ascii=False),
            json.dumps(card.aliases, ensure_ascii=False),
            json.dumps(card.cve, ensure_ascii=False),
            card.severity,
            card.confidence,
            card.status,
            card.updated,
            card.content_hash,
            card.body,
        ),
    )
    aliases = " ".join(card.aliases)
    tags = " ".join(card.tags)
    conn.execute(
        "INSERT INTO cards_fts_trigram (id, title, aliases, tags, body) VALUES (?, ?, ?, ?, ?)",
        (card.id, card.title, aliases, tags, card.body),
    )
    conn.execute(
        "INSERT INTO cards_fts_tokens (id, title, aliases, tags, body) VALUES (?, ?, ?, ?, ?)",
        (
            card.id,
            _join_tokens(card.title),
            _join_tokens(aliases),
            _join_tokens(tags),
            _join_tokens(card.body),
        ),
    )


def update_readmes(cfg: Config, cards: Iterable[Card]) -> list[Path]:
    grouped: dict[Path, list[Card]] = {}
    for card in cards:
        grouped.setdefault(card.file_path.parent, []).append(card)
    changed: list[Path] = []
    for directory in sorted({path.parent for path in cfg.knowledge.rglob("README.md")}):
        readme = directory / "README.md"
        items = sorted(grouped.get(directory, []), key=lambda c: c.title.lower())
        rows = [
            {
                "title": card.title,
                "filename": card.filename,
                "status": card.status,
                "confidence": card.confidence,
                "updated": card.updated,
            }
            for card in items
        ]
        if update_auto_index(readme, render_index_table(rows)):
            changed.append(readme)
    return changed


def _refresh_readme_for_dir(cfg: Config, directory: Path) -> None:
    conn = sqlite3.connect(cfg.db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, title, filename, status, confidence, updated, file_path FROM cards"
        ).fetchall()
    finally:
        conn.close()
    items = []
    for row in rows:
        try:
            parent = Path(row["file_path"]).parent
        except Exception:
            continue
        if parent == directory:
            items.append(
                {
                    "title": row["title"],
                    "filename": row["filename"],
                    "status": row["status"],
                    "confidence": row["confidence"],
                    "updated": row["updated"],
                }
            )
    items.sort(key=lambda item: str(item["title"]).lower())
    update_auto_index(directory / "README.md", render_index_table(items))


def upsert_card(cfg: Config, card: Card) -> None:
    """Update one card's DB/FTS row without rebuilding the whole index."""
    conn = sqlite3.connect(cfg.db_path)
    try:
        ensure_schema(conn)
        conn.execute("DELETE FROM cards WHERE id = ?", (card.id,))
        conn.execute("DELETE FROM cards_fts_trigram WHERE id = ?", (card.id,))
        conn.execute("DELETE FROM cards_fts_tokens WHERE id = ?", (card.id,))
        _insert_card(conn, card)
        conn.execute(
            "DELETE FROM embeddings WHERE card_id = ? AND content_hash != ?",
            (card.id, card.content_hash),
        )
        conn.commit()
    finally:
        conn.close()
    _refresh_readme_for_dir(cfg, card.file_path.parent)


def delete_card_index(cfg: Config, card_id: str, directory: Path) -> None:
    conn = sqlite3.connect(cfg.db_path)
    try:
        ensure_schema(conn)
        conn.execute("DELETE FROM cards WHERE id = ?", (card_id,))
        conn.execute("DELETE FROM cards_fts_trigram WHERE id = ?", (card_id,))
        conn.execute("DELETE FROM cards_fts_tokens WHERE id = ?", (card_id,))
        conn.execute("DELETE FROM embeddings WHERE card_id = ?", (card_id,))
        conn.commit()
    finally:
        conn.close()
    _refresh_readme_for_dir(cfg, directory)


def count_cards(cfg: Config) -> int:
    conn = sqlite3.connect(cfg.db_path)
    try:
        return int(conn.execute("SELECT count(*) FROM cards").fetchone()[0])
    finally:
        conn.close()


def build_index(cfg: Config, embed: bool = False, embedder: Embedder | None = None) -> dict[str, Any]:
    discovery = discover_cards(cfg)
    cfg.data.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(cfg.db_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(
            "DROP TABLE IF EXISTS cards; DROP TABLE IF EXISTS cves;"
            "DROP TABLE IF EXISTS cards_fts_trigram; DROP TABLE IF EXISTS cards_fts_tokens;"
        )
        ensure_schema(conn)
        for card in discovery.cards:
            _insert_card(conn, card)
        cve_rows = parse_cves(cfg)
        conn.executemany(
            "INSERT OR REPLACE INTO cves (component, cve, summary, versions, poc) VALUES (?, ?, ?, ?, ?)",
            [
                (row["component"], row["cve"], row["summary"], row["versions"], row["poc"])
                for row in cve_rows
            ],
        )
        conn.execute("DELETE FROM embeddings WHERE card_id NOT IN (SELECT id FROM cards)")
        conn.commit()
    finally:
        conn.close()

    changed = update_readmes(cfg, discovery.cards)
    stats = {
        "cards": len(discovery.cards),
        "cves": len(cve_rows),
        "errors": discovery.errors,
        "readmes_changed": [str(path) for path in changed],
        "embedded": 0,
        "embedding_error": None,
    }
    if embed:
        stats.update(embed_cards(cfg, embedder=embedder))
    write_index_json(cfg, discovery.cards, cve_rows, stats)
    return stats


def embed_cards(cfg: Config, embedder: Embedder | None = None) -> dict[str, Any]:
    discovery = discover_cards(cfg)
    embedder = embedder or Embedder(cache_dir=cfg.data / "models")
    conn = sqlite3.connect(cfg.db_path)
    result: dict[str, Any] = {"embedded": 0, "embedding_error": None}
    try:
        ensure_schema(conn)
        conn.execute("DELETE FROM embeddings WHERE model != ?", (embedder.model_name,))
        existing = {
            row[0]: (row[1], row[2])
            for row in conn.execute("SELECT card_id, content_hash, model FROM embeddings")
        }
        pending = [
            card
            for card in discovery.cards
            if existing.get(card.id) != (card.content_hash, embedder.model_name)
        ]
        if not pending:
            conn.commit()
            return result
        try:
            vectors = embedder.encode([card.embed_text() for card in pending])
        except EmbeddingUnavailable as exc:
            result["embedding_error"] = str(exc)
            return result
        dim = int(vectors.shape[1])
        for card, vector in zip(pending, vectors):
            conn.execute(
                "INSERT OR REPLACE INTO embeddings (card_id, model, dim, vector, content_hash) VALUES (?, ?, ?, ?, ?)",
                (card.id, embedder.model_name, dim, pack_vector(vector), card.content_hash),
            )
        conn.commit()
        result["embedded"] = len(pending)
    finally:
        conn.close()
    return result


def write_index_json(
    cfg: Config,
    cards: Iterable[Card],
    cves: Iterable[dict[str, str]],
    stats: dict[str, Any] | None = None,
) -> None:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "stats": stats or {},
        "cards": [card.to_public() for card in cards],
        "cves": list(cves),
    }
    cfg.data.mkdir(parents=True, exist_ok=True)
    cfg.index_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def get_card(cfg: Config, card_id: str) -> dict[str, Any] | None:
    conn = sqlite3.connect(cfg.db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
        if row is None:
            return None
        data = dict(row)
        for key in ("tags", "aliases", "cve"):
            data[key] = json.loads(data.get(key) or "[]")
        try:
            data["meta"] = parse_card(Path(data["file_path"]), cfg).meta
        except Exception:
            data["meta"] = {}
        return data
    finally:
        conn.close()


def all_cards(cfg: Config) -> list[dict[str, Any]]:
    conn = sqlite3.connect(cfg.db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT * FROM cards ORDER BY path, filename").fetchall()
        out = []
        for row in rows:
            data = dict(row)
            for key in ("tags", "aliases", "cve"):
                data[key] = json.loads(data.get(key) or "[]")
            out.append(data)
        return out
    finally:
        conn.close()


def taxonomy_for(cfg: Config) -> dict[str, Any]:
    return taxonomy.load(cfg.taxonomy)
