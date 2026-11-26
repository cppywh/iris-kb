from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

from filelock import FileLock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT))

from kb_lib import batch_state, dedup, dupscan, indexer, ledger, markdown_util, scaffold, search as search_lib, treequery, validator  # noqa: E402
from kb_lib.config import Config, load as load_config  # noqa: E402
from kb_lib.embedder import Embedder, EmbeddingUnavailable, unpack_vector  # noqa: E402
from kb_lib.frontmatter import as_list, as_text, dump_frontmatter, split_frontmatter  # noqa: E402


def _json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _today() -> str:
    return date.today().isoformat()


def _kb_lock(cfg: Config) -> FileLock:
    cfg.data.mkdir(parents=True, exist_ok=True)
    return FileLock(str(cfg.data / ".kb.lock"), timeout=300)


def _queue_lock(cfg: Config) -> FileLock:
    (cfg.ingest / "state").mkdir(parents=True, exist_ok=True)
    return FileLock(str(cfg.ingest / "state" / ".queue.lock"), timeout=300)


def cmd_init(args: argparse.Namespace, cfg: Config) -> int:
    created = scaffold.ensure_tree(cfg)
    stats = indexer.build_index(cfg, embed=args.embed)
    print(f"scaffold: created {len(created)} README files")
    print(f"index: {stats['cards']} cards, {stats['cves']} CVEs")
    if stats.get("embedding_error"):
        print(f"embedding warning: {stats['embedding_error']}", file=sys.stderr)
    return 0


def cmd_index(args: argparse.Namespace, cfg: Config) -> int:
    with _kb_lock(cfg):
        return _cmd_index(args, cfg)


def _cmd_index(args: argparse.Namespace, cfg: Config) -> int:
    stats = indexer.build_index(cfg, embed=args.embed)
    if args.json:
        _json(stats)
    else:
        print(f"indexed {stats['cards']} cards, {stats['cves']} CVEs")
        if stats["errors"]:
            print(f"parse errors: {len(stats['errors'])}", file=sys.stderr)
            for item in stats["errors"]:
                print(f"  {item['file']}: {item['error']}", file=sys.stderr)
        if stats.get("embedding_error"):
            print(f"embedding warning: {stats['embedding_error']}", file=sys.stderr)
        if stats.get("embedded"):
            print(f"embedded {stats['embedded']} cards")
    return 0 if not stats["errors"] else 1


def cmd_embed(args: argparse.Namespace, cfg: Config) -> int:
    with _kb_lock(cfg):
        return _cmd_embed(args, cfg)


def _cmd_embed(args: argparse.Namespace, cfg: Config) -> int:
    result = indexer.embed_cards(cfg)
    if args.json:
        _json(result)
    else:
        if result.get("embedding_error"):
            print(f"embedding unavailable: {result['embedding_error']}", file=sys.stderr)
        print(f"embedded {result['embedded']} cards")
    return 0 if not result.get("embedding_error") else 2


def cmd_validate(args: argparse.Namespace, cfg: Config) -> int:
    result = validator.validate(cfg, strict_paths=args.strict_paths)
    if args.json:
        _json(result)
    else:
        print(f"validated {result['cards']} cards: {len(result['errors'])} errors, {len(result['warnings'])} warnings")
        for item in result["errors"]:
            print(f"ERROR   {item['file']}: {item['error']}")
        for item in result["warnings"]:
            print(f"WARNING {item['file']}: {item['error']}")
    return 0 if not result["errors"] else 1


def cmd_search(args: argparse.Namespace, cfg: Config) -> int:
    engine = search_lib.SearchEngine(cfg)
    queries = [args.query, *(args.queries or [])]
    if len([q for q in queries if (q or "").strip()]) > 1:
        results = engine.search_multi(
            queries,
            mode=args.mode,
            domain=args.domain,
            tags=args.tag or None,
            status=args.status,
            limit=args.limit,
        )
    else:
        results = engine.search(
            args.query,
            mode=args.mode,
            domain=args.domain,
            tags=args.tag or None,
            status=args.status,
            limit=args.limit,
        )
    if args.json:
        _json(results)
    else:
        for result in results:
            print(f"[{result['score']:.4f}] {result['title']}  ({result['status']}/{result['confidence']})")
            print(f"  {result['path']}/{result['filename']}  [{result['match_reason']}]")
            if result.get("snippet"):
                print(f"  {result['snippet']}")
    return 0


def _draft_from_file(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8")
    meta, body = split_frontmatter(text)
    return meta, body


def _dedup_cache_path(cfg: Config, draft_path: Path) -> Path:
    return cfg.ingest / "reports" / f"{draft_path.parent.name}-{draft_path.stem}-dedup.json"


def _draft_obj(meta: dict[str, Any], body: str) -> dedup.Draft:
    return dedup.Draft(
        title=as_text(meta.get("title")),
        body=body,
        tags=as_list(meta.get("tags")),
        aliases=as_list(meta.get("aliases")),
        technique=as_text(meta.get("technique")),
    )


def _dedup_report(cfg: Config, meta: dict[str, Any], body: str, limit: int, semantic: bool = True) -> dict[str, Any]:
    draft = _draft_obj(meta, body)
    engine = search_lib.SearchEngine(cfg)
    query = " ".join([draft.title, draft.technique, " ".join(draft.tags)]).strip()
    candidates = engine.search(query, mode="hybrid" if semantic else "keyword", limit=max(limit, 20)) if query else []
    if not candidates:
        candidates = [
            {
                "id": card["id"],
                "title": card["title"],
                "path": card["path"],
                "filename": card["filename"],
                "status": card["status"],
                "confidence": card["confidence"],
            }
            for card in indexer.all_cards(cfg)
        ][:limit]

    # Optional vectors.
    draft_vector = None
    candidate_vectors: dict[str, Any] = {}
    embedding_error: str | None = None
    if semantic:
        embedder = Embedder(cache_dir=cfg.data / "models")
        try:
            import sqlite3

            conn = sqlite3.connect(cfg.db_path)
            try:
                rows = conn.execute("SELECT card_id, vector, dim FROM embeddings").fetchall()
                if rows:
                    draft_vector = embedder.encode_one("\n".join([draft.title, " ".join(draft.tags), body[:2400]]))
                    candidate_vectors = {cid: unpack_vector(blob, dim) for cid, blob, dim in rows}
            finally:
                conn.close()
        except EmbeddingUnavailable as exc:
            embedding_error = str(exc)
        except Exception as exc:  # noqa: BLE001 - report rather than hide
            embedding_error = f"{type(exc).__name__}: {exc}"

    scored = []
    for candidate in candidates:
        card = indexer.get_card(cfg, candidate["id"])
        if card is None:
            continue
        cand = dedup.Draft(
            title=card["title"],
            body=card["body"],
            tags=card["tags"],
            aliases=card["aliases"],
            technique=card.get("technique") or "",
        )
        scores = dedup.compare(
            draft,
            cand,
            candidate_vector=candidate_vectors.get(candidate["id"]),
            draft_vector=draft_vector,
        )
        scored.append(
            {
                "id": card["id"],
                "title": card["title"],
                "path": card["path"],
                "filename": card["filename"],
                "status": card["status"],
                "confidence": card["confidence"],
                "excerpt": dedup.excerpt(card["body"]),
                "scores": {
                    "exact": scores.exact,
                    "trigram": scores.trigram,
                    "title_trigram": scores.title_trigram,
                    "body_containment": scores.body_containment,
                    "simhash_distance": scores.simhash_distance,
                    "tag_overlap": scores.tag_overlap,
                    "cosine": scores.cosine,
                },
                "verdict": scores.verdict,
                "action": scores.action,
            }
        )
    order = {"duplicate": 0, "likely_duplicate": 1, "related": 2, "new": 3}
    scored.sort(key=lambda item: (order[item["verdict"]], -item["scores"]["trigram"]))
    top = scored[0] if scored else None
    recommendation = top["action"] if top and top["verdict"] != "new" else "new"
    return {
        "draft": {
            "title": draft.title,
            "technique": draft.technique,
            "tags": draft.tags,
            "aliases": draft.aliases,
        },
        "candidates": scored[:limit],
        "recommendation": recommendation,
        "embedding_error": embedding_error,
        "summary": {
            "duplicate": sum(1 for item in scored if item["verdict"] == "duplicate"),
            "likely_duplicate": sum(1 for item in scored if item["verdict"] == "likely_duplicate"),
            "related": sum(1 for item in scored if item["verdict"] == "related"),
            "new": sum(1 for item in scored if item["verdict"] == "new"),
        },
    }


def cmd_dedup(args: argparse.Namespace, cfg: Config) -> int:
    return _cmd_dedup(args, cfg)


def _cmd_dedup(args: argparse.Namespace, cfg: Config) -> int:
    draft_path = Path(args.draft)
    draft_text = draft_path.read_text(encoding="utf-8")
    draft_hash = hashlib.sha256(draft_text.encode("utf-8")).hexdigest()
    meta, body = _draft_from_file(draft_path)
    report = _dedup_report(cfg, meta, body, args.limit)
    report["draft_hash"] = draft_hash
    cache_path = _dedup_cache_path(cfg, draft_path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.json:
        _json(report)
    else:
        print(f"recommendation: {report['recommendation']}")
        for item in report["candidates"]:
            print(
                f"- {item['verdict']:16s} {item['title']}  "
                f"(title={item['scores']['title_trigram']}, body={item['scores']['body_containment']}, "
                f"trigram={item['scores']['trigram']}, simhash={item['scores']['simhash_distance']}, "
                f"tag={item['scores']['tag_overlap']}, cosine={item['scores']['cosine']})"
            )
            conclusion = (item.get("excerpt") or {}).get("conclusion")
            if conclusion:
                print(f"    {conclusion}")
    if args.fail_on_duplicate and report["recommendation"] in {"merge", "append_scenario"}:
        return 2
    return 0


def cmd_dup_scan(args: argparse.Namespace, cfg: Config) -> int:
    report = dupscan.scan(cfg, min_verdict=args.min_verdict, limit=args.limit)
    out_path = cfg.ingest / "reports" / "dup-scan.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.json:
        _json(report)
    else:
        print(
            f"scanned {report['cards']} staging cards from {report['articles']} articles; "
            f"{report['total_pairs']} candidate pair(s)"
        )
        for pair in report["pairs"]:
            left, right = pair["a"], pair["b"]
            scores = pair["scores"]
            print(
                f"- {pair['verdict']:16s} cos={scores['cosine']} body={scores['body_containment']} "
                f"trigram={scores['trigram']}"
            )
            print(f"    A [{left['article']}] {left['title']}  ({left['path']})")
            print(f"    B [{right['article']}] {right['title']}  ({right['path']})")
        print(f"report: {out_path}")
    return 0


def cmd_precheck(args: argparse.Namespace, cfg: Config) -> int:
    meta = {
        "title": args.title,
        "technique": args.technique or "",
        "tags": [x.strip() for x in (args.tags or "").split(",") if x.strip()],
        "aliases": [x.strip() for x in (args.aliases or "").split(",") if x.strip()],
    }
    report = _dedup_report(cfg, meta, args.summary or "", args.limit, semantic=args.semantic)
    report["path_suggestions"] = treequery.query_paths(
        cfg,
        " ".join([args.title, args.technique or "", args.tags or ""]),
        limit=3,
    )
    if args.json:
        _json(report)
    else:
        print(f"recommendation: {report['recommendation']}")
        for item in report["candidates"][:5]:
            print(f"- {item['verdict']:16s} {item['title']}  ({item['scores']})")
        for item in report["path_suggestions"]:
            print(f"  path: {item['path']} ({item['title']}) score={item['score']}")
    return 0


def cmd_add(args: argparse.Namespace, cfg: Config) -> int:
    return _cmd_add(args, cfg)


def _cmd_add(args: argparse.Namespace, cfg: Config) -> int:
    draft_path = Path(args.draft)
    draft_text = draft_path.read_text(encoding="utf-8")
    draft_hash = hashlib.sha256(draft_text.encode("utf-8")).hexdigest()
    meta, body = _draft_from_file(draft_path)
    for field in ("id", "title", "path", "technique", "tags", "confidence", "status"):
        if not meta.get(field):
            print(f"error: draft is missing required field '{field}'", file=sys.stderr)
            return 1
    if meta.get("sources") or meta.get("source"):
        print("error: frontmatter must not contain sources/source", file=sys.stderr)
        return 1

    cache_path = _dedup_cache_path(cfg, draft_path)
    cached = None
    if cache_path.exists():
        try:
            candidate = json.loads(cache_path.read_text(encoding="utf-8"))
            if candidate.get("draft_hash") == draft_hash:
                cached = candidate
        except Exception:
            cached = None
    report = cached if cached is not None else _dedup_report(cfg, meta, body, args.limit)
    report["draft_hash"] = draft_hash
    path_check = treequery.check_path(
        cfg,
        as_text(meta.get("path")),
        title=as_text(meta.get("title")),
        description=as_text(meta.get("description")),
    )
    report["path_check"] = path_check
    duplicate_paths = [item for item in path_check["candidates"] if item["verdict"] == "duplicate"]
    if duplicate_paths:
        print(
            "warning: path-check found duplicate/similar taxonomy path(s): "
            + ", ".join(f"{item['path']} ({item['title']})" for item in duplicate_paths[:3]),
            file=sys.stderr,
        )
        if args.strict_path:
            print("error: --strict-path rejected this card", file=sys.stderr)
            return 2

    if report["recommendation"] in {"merge", "append_scenario"} and not args.force:
        print(
            f"error: dedup recommends '{report['recommendation']}'. "
            "Review the report or pass --force.",
            file=sys.stderr,
        )
        if args.json:
            _json(report)
        return 2

    target_dir = cfg.knowledge / as_text(meta.get("path"))
    if not target_dir.exists():
        print(f"error: target directory does not exist: {target_dir}", file=sys.stderr)
        return 1
    if not (target_dir / "README.md").exists():
        print(f"error: target directory is missing README.md: {target_dir}", file=sys.stderr)
        return 1

    filename = args.filename or (markdown_util.slug_from_id(as_text(meta.get("id")), "card") + ".md")
    target = target_dir / filename
    if target.exists() and not args.force:
        print(f"error: target already exists: {target}", file=sys.stderr)
        return 1

    meta.setdefault("created", _today())
    meta["updated"] = _today()
    report_path = cfg.ingest / "reports" / f"{markdown_util.slug_from_id(as_text(meta.get('id')), 'card')}-dedup.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with _kb_lock(cfg):
        if target.exists() and not args.force:
            print(f"error: target already exists: {target}", file=sys.stderr)
            return 1
        target.write_text(dump_frontmatter(meta, body), encoding="utf-8")
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        card = indexer.parse_card(target, cfg)
        indexer.upsert_card(cfg, card)
        stats = {"cards": indexer.count_cards(cfg)}
    if args.json:
        _json({"written": str(target), "dedup_report": str(report_path), "index": stats})
    else:
        print(f"wrote {target}")
        print(f"dedup report {report_path}")
        print(f"indexed {stats['cards']} cards")
    return 0


def cmd_verify(args: argparse.Namespace, cfg: Config) -> int:
    with _kb_lock(cfg):
        return _cmd_verify(args, cfg)


def _cmd_verify(args: argparse.Namespace, cfg: Config) -> int:
    card = indexer.get_card(cfg, args.id)
    if card is None:
        print(f"card not found: {args.id}", file=sys.stderr)
        return 1
    path = Path(card["file_path"])
    meta, body = split_frontmatter(path.read_text(encoding="utf-8"))
    meta["status"] = "draft" if args.fail else "verified"
    meta["updated"] = _today()
    verification = meta.get("verification") or {}
    verification.update(
        {
            "verified_by": args.by,
            "verified_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "notes": args.notes or "",
        }
    )
    if args.unverified:
        verification["unverified"] = args.unverified
    meta["verification"] = verification
    path.write_text(dump_frontmatter(meta, body), encoding="utf-8")
    indexer.upsert_card(cfg, indexer.parse_card(path, cfg))
    result = {"id": args.id, "status": meta["status"], "file": str(path), "cards": indexer.count_cards(cfg)}
    if args.json:
        _json(result)
    else:
        print(f"{args.id}: {meta['status']} ({path})")
    return 0


def cmd_set_versions(args: argparse.Namespace, cfg: Config) -> int:
    with _kb_lock(cfg):
        return _cmd_set_versions(args, cfg)


def _cmd_set_versions(args: argparse.Namespace, cfg: Config) -> int:
    card = indexer.get_card(cfg, args.id)
    if card is None:
        print(f"card not found: {args.id}", file=sys.stderr)
        return 1
    path = Path(card["file_path"])
    meta, body = split_frontmatter(path.read_text(encoding="utf-8"))
    if args.clear:
        meta.pop("affected_versions", None)
    else:
        if not args.versions.strip():
            print("error: --versions is empty; use --clear to remove the field", file=sys.stderr)
            return 1
        meta["affected_versions"] = args.versions.strip()
    meta["updated"] = _today()
    path.write_text(dump_frontmatter(meta, body), encoding="utf-8")
    indexer.upsert_card(cfg, indexer.parse_card(path, cfg))
    result = {
        "id": args.id,
        "affected_versions": meta.get("affected_versions"),
        "file": str(path),
        "cards": indexer.count_cards(cfg),
    }
    _json(result) if args.json else print(json.dumps(result, ensure_ascii=False))
    return 0


def cmd_reject(args: argparse.Namespace, cfg: Config) -> int:
    path = Path(args.file)
    if not path.exists():
        print(f"file not found: {path}", file=sys.stderr)
        return 1
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = cfg.ingest / "rejected" / "drafts" / f"{stamp}-{path.name}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(dest))
    log = cfg.ingest / "rejected" / "index.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {"file": str(path), "recycle": str(dest), "reason": args.reason or "", "at": datetime.now().isoformat()},
                ensure_ascii=False,
            )
            + "\n"
        )
    result = {"rejected": str(path), "recycle": str(dest), "reason": args.reason or ""}
    _json(result) if args.json else print(json.dumps(result, ensure_ascii=False))
    return 0


def cmd_delete_card(args: argparse.Namespace, cfg: Config) -> int:
    with _kb_lock(cfg):
        card = indexer.get_card(cfg, args.id)
        if card is None:
            print(f"card not found: {args.id}", file=sys.stderr)
            return 1
        path = Path(card["file_path"])
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        recycle = cfg.ingest / "rejected" / "cards" / f"{stamp}-{path.name}"
        recycle.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            shutil.move(str(path), str(recycle))
        indexer.delete_card_index(cfg, args.id, path.parent)
        result = {"deleted": args.id, "file": str(path), "recycle": str(recycle), "cards": indexer.count_cards(cfg)}
        _json(result) if args.json else print(json.dumps(result, ensure_ascii=False))
        return 0


def cmd_tree(args: argparse.Namespace, cfg: Config) -> int:
    if args.query:
        results = treequery.query_paths(cfg, args.query, domain=args.domain, limit=args.limit)
    else:
        results = [
            {
                "path": node["path"],
                "title": node["title"],
                "description": node["description"],
                "cards": 0,
            }
            for node in treequery._nodes(cfg)
            if not args.domain or node["path"] == args.domain or node["path"].startswith(args.domain + "/")
        ]
    if args.json:
        _json(results)
    else:
        for item in results:
            cards = f" cards={item['cards']}" if "cards" in item else ""
            print(f"{item['path']}\t{item['title']}{cards}\t{item['description'][:80]}")
    return 0


def cmd_path_check(args: argparse.Namespace, cfg: Config) -> int:
    result = treequery.check_path(cfg, args.path, title=args.title, description=args.description, limit=args.limit)
    if args.json:
        _json(result)
    else:
        print(f"verdict: {result['verdict']}")
        for item in result["candidates"]:
            print(f"- [{item['verdict']}] {item['path']} — {item['title']} (score={item['score']})")
    return 0


def cmd_refetch(args: argparse.Namespace, cfg: Config) -> int:
    from crawler import prepare_article

    manifest = {}
    path = cfg.ingest / "state" / "manifest.jsonl"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            manifest[str(item.get("id"))] = item
    article = manifest.get(str(args.id))
    if article is None:
        print(f"article not found in manifest: {args.id}", file=sys.stderr)
        return 1
    result = prepare_article(cfg, article)
    _json(result)
    return 0


def cmd_batch_status(args: argparse.Namespace, cfg: Config) -> int:
    result = batch_state.status(cfg, mark_notified=args.mark_notified, set_interval=args.set_interval)
    _json(result) if args.json else print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_similar(args: argparse.Namespace, cfg: Config) -> int:
    engine = search_lib.SearchEngine(cfg)
    results = engine.similar(args.id, limit=args.limit)
    if args.json:
        _json(results)
    else:
        for result in results:
            print(f"[{result['score']:.4f}] {result['title']}  ({result['status']})  {result['path']}")
    return 0


def cmd_related(args: argparse.Namespace, cfg: Config) -> int:
    engine = search_lib.SearchEngine(cfg)
    results = engine.related(args.id, limit=args.limit)
    if args.json:
        _json(results)
    else:
        for result in results:
            print(f"[{result['score']:.2f}] {result['title']}  ({result['status']})  {result['path']}")
    return 0


def cmd_show(args: argparse.Namespace, cfg: Config) -> int:
    card = indexer.get_card(cfg, args.id)
    if card is None:
        print(f"card not found: {args.id}", file=sys.stderr)
        return 1
    if args.json:
        _json(card)
    else:
        print(card["title"])
        print(f"{card['path']}/{card['filename']}  ({card['status']}/{card['confidence']})")
        print()
        print(card["body"])
    return 0


def cmd_stats(args: argparse.Namespace, cfg: Config) -> int:
    import sqlite3

    conn = sqlite3.connect(cfg.db_path)
    try:
        def count(sql: str) -> list[tuple]:
            return conn.execute(sql).fetchall()

        data = {
            "cards": count("SELECT count(*) FROM cards")[0][0],
            "cves": count("SELECT count(*) FROM cves")[0][0],
            "embeddings": count("SELECT count(*) FROM embeddings")[0][0],
            "by_status": dict(count("SELECT status, count(*) FROM cards GROUP BY status")),
            "by_confidence": dict(count("SELECT confidence, count(*) FROM cards GROUP BY confidence")),
            "by_domain": dict(count("SELECT domain, count(*) FROM cards GROUP BY domain ORDER BY count(*) DESC")),
        }
    finally:
        conn.close()
    if args.json:
        _json(data)
    else:
        print(f"cards={data['cards']} cves={data['cves']} embeddings={data['embeddings']}")
        print(f"status: {data['by_status']}")
        print(f"confidence: {data['by_confidence']}")
        print(f"domains: {data['by_domain']}")
    return 0


def cmd_hints(args: argparse.Namespace, cfg: Config) -> int:
    if args.facts:
        facts = json.loads(Path(args.facts).read_text(encoding="utf-8"))
    else:
        facts = json.loads(args.facts_json or "{}")
    results = search_lib.reverse_hints(cfg, facts, limit=args.limit)
    if args.json:
        _json(results)
    else:
        for result in results:
            print(f"[{result['score']:.4f}] {result['title']}  ({result['status']}/{result['confidence']})")
            print(f"  {result['path']}/{result['filename']}  {result.get('match_reason', '')}")
    return 0


def cmd_cve(args: argparse.Namespace, cfg: Config) -> int:
    engine = search_lib.SearchEngine(cfg)
    results = engine.cves(component=args.component, cve=args.cve)
    if args.json:
        _json(results)
    else:
        for row in results:
            print(f"{row['component']}\t{row['cve']}\t{row['versions']}\t{row['summary']}\t{row['poc']}")
    return 0


def cmd_serve(args: argparse.Namespace, cfg: Config) -> int:
    from web.app import create_app

    app = create_app(cfg)
    app.run(host=args.host, port=args.port, debug=args.debug)
    return 0


def cmd_mcp(args: argparse.Namespace, cfg: Config) -> int:
    import mcp_server

    mcp_server.run(profile=args.profile)
    return 0


def cmd_crawl(args: argparse.Namespace, cfg: Config) -> int:
    try:
        from crawler import crawl_xz
    except Exception as exc:  # noqa: BLE001
        print(f"crawler unavailable: {exc}", file=sys.stderr)
        return 2
    stats = crawl_xz(cfg, limit=args.limit, category=args.category, since=args.since, page_size=args.page_size)
    _json(stats) if args.json else print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


def cmd_export_skillhub(args: argparse.Namespace, cfg: Config) -> int:
    from export_skillhub import export_bundle

    result = export_bundle(
        cfg,
        args.domain,
        Path(args.output) if args.output else None,
        version=args.version,
        max_files=args.max_files,
    )
    _json(result)
    return 0


def cmd_next(args: argparse.Namespace, cfg: Config) -> int:
    from crawler import claim_next, prepare_claimed

    with _queue_lock(cfg):
        claimed = claim_next(cfg, args.worker, lease_minutes=args.lease)
    if claimed is None:
        print(json.dumps({"status": "empty"}, ensure_ascii=False))
        return 1
    _json(prepare_claimed(cfg, claimed))
    return 0


def cmd_claim(args: argparse.Namespace, cfg: Config) -> int:
    from crawler import claim_article

    with _queue_lock(cfg):
        prepared = claim_article(cfg, args.worker, args.article, lease_minutes=args.lease)
    if prepared is None:
        print(json.dumps({"status": "unavailable", "article": args.article}, ensure_ascii=False))
        return 1
    _json(prepared)
    return 0


def cmd_commit(args: argparse.Namespace, cfg: Config) -> int:
    from crawler import commit_article

    record = commit_article(cfg, args.article, args.worker, args.card)
    data = ledger.build(cfg)
    record["ledger"] = {key: value for key, value in data.items() if key != "records"}
    _json(record)
    return 0


def cmd_sync(args: argparse.Namespace, cfg: Config) -> int:
    from crawler import sync_xz

    with _queue_lock(cfg):
        stats = sync_xz(cfg, limit=args.limit, category=args.category, since=args.since, page_size=args.page_size)
        data = ledger.build(cfg)
    stats["ledger"] = {key: value for key, value in data.items() if key != "records"}
    _json(stats)
    return 0


def cmd_ledger(args: argparse.Namespace, cfg: Config) -> int:
    data = ledger.build(cfg)
    if args.json:
        _json(data)
    else:
        print(f"total={data['total']} counts={data['counts']} cards={data['cards']}")
        for row in data["records"][: args.limit]:
            print(
                f"{row['status']:26s} {row['article_id']:>6s}  {row['title'][:60]}  "
                f"cards={row['card_count']}"
            )
        print(f"written: {cfg.ingest / 'state' / 'distilled.md'}")
    return 0


def cmd_release(args: argparse.Namespace, cfg: Config) -> int:
    from crawler import release_article

    release_article(cfg, args.article)
    _json({"released": args.article})
    return 0


def cmd_queue(args: argparse.Namespace, cfg: Config) -> int:
    from crawler import _claim_ids, _staging_ids, read_processed, read_queue

    queue = read_queue(cfg)
    processed = read_processed(cfg)
    staged = _staging_ids(cfg)
    claims = _claim_ids(cfg)
    done = processed | staged | claims
    pending_ids = [str(row["id"]) for row in queue if str(row["id"]) not in done]
    data = {
        "queued": len(queue),
        "processed": len(processed),
        "staged": len(staged),
        "active_claims": len(claims),
        "pending": len(pending_ids),
        "next": pending_ids[:10],
    }
    _json(data) if args.json else print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kb", description="vuln-masterhub CLI")
    parser.add_argument("--root", help="repository root (default: auto-detect)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="scaffold directories and build the index")
    p.add_argument("--embed", action="store_true")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("index", help="rebuild SQLite FTS index and README indexes")
    p.add_argument("--embed", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_index)

    p = sub.add_parser("embed", help="compute missing card embeddings")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_embed)

    p = sub.add_parser("validate", help="validate cards, paths, READMEs and CVE files")
    p.add_argument("--strict-paths", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("search", help="hybrid keyword/semantic search")
    p.add_argument("query")
    p.add_argument("--queries", nargs="+", help="additional query rewrites merged with RRF")
    p.add_argument("--mode", choices=["hybrid", "keyword", "semantic"], default="hybrid")
    p.add_argument("--domain")
    p.add_argument("--tag", action="append")
    p.add_argument("--status", choices=["draft", "verified", "deprecated"])
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("dedup", help="check a draft against existing cards")
    p.add_argument("--draft", required=True)
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--fail-on-duplicate", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_dedup)

    p = sub.add_parser("dup-scan", help="cluster duplicate candidates across staging cards")
    p.add_argument(
        "--min-verdict",
        choices=["duplicate", "likely_duplicate", "related"],
        default="related",
    )
    p.add_argument("--limit", type=int, default=200)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_dup_scan)

    p = sub.add_parser("precheck", help="check a scenario idea before writing a draft card")
    p.add_argument("--title", required=True)
    p.add_argument("--technique", default="")
    p.add_argument("--tags", default="")
    p.add_argument("--aliases", default="")
    p.add_argument("--summary", default="")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--semantic", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_precheck)

    p = sub.add_parser("add", help="add a validated draft card")
    p.add_argument("--draft", required=True)
    p.add_argument("--filename")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--force", action="store_true")
    p.add_argument("--strict-path", dest="strict_path", action="store_true", default=True)
    p.add_argument("--allow-path-warning", dest="strict_path", action="store_false")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("verify", help="mark a card verified or failed")
    p.add_argument("id")
    p.add_argument("--by", required=True)
    p.add_argument("--notes", default="")
    p.add_argument("--unverified", action="append")
    p.add_argument("--fail", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("set-versions", help="set or clear affected_versions on a card")
    p.add_argument("id")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--versions", help="e.g. '<= 1.2.80' or 'ThinkPHP 5.0.x'")
    group.add_argument("--clear", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_set_versions)

    p = sub.add_parser("delete-card", help="delete a card file and rebuild the index")
    p.add_argument("id")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_delete_card)

    p = sub.add_parser("reject", help="move a discarded draft/card into ingest/rejected")
    p.add_argument("--file", required=True)
    p.add_argument("--reason", default="")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_reject)

    p = sub.add_parser("refetch", help="re-fetch a processed article into ingest/tmp for manual review")
    p.add_argument("id")
    p.set_defaults(func=cmd_refetch)

    p = sub.add_parser("batch-status", help="show batch progress and the notification checkpoint")
    p.add_argument("--mark-notified", action="store_true")
    p.add_argument("--set-interval", type=int, help="notification interval in processed articles (default 1000)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_batch_status)

    p = sub.add_parser("similar", help="find semantically similar cards")
    p.add_argument("id")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_similar)

    p = sub.add_parser("related", help="find cards related by technique/tags/CVE")
    p.add_argument("id")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_related)

    p = sub.add_parser("show", help="show a card")
    p.add_argument("id")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("stats", help="show index statistics")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("hints", help="reverse-engineering hints from artifact facts")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--facts", help="JSON file with artifact facts")
    group.add_argument("--facts-json", help="inline JSON facts")
    p.add_argument("--limit", type=int, default=8)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_hints)

    p = sub.add_parser("cve", help="look up CVEs")
    p.add_argument("--component")
    p.add_argument("--cve")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_cve)

    p = sub.add_parser("serve", help="start the Flask admin UI")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=5000)
    p.add_argument("--debug", action="store_true")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("mcp", help="start the reverse knowledge MCP server")
    p.add_argument("--profile", default="reverse")
    p.set_defaults(func=cmd_mcp)

    p = sub.add_parser("crawl", help="crawl articles from a supported source")
    p.add_argument("source", choices=["xz"])
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--category", type=int)
    p.add_argument("--since")
    p.add_argument("--page-size", type=int, default=50)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_crawl)

    p = sub.add_parser("export-skillhub", help="export a domain bundle for SkillHub")
    p.add_argument("--domain", required=True, help="e.g. web or web/java")
    p.add_argument("--version", default="0.1.0")
    p.add_argument("--output")
    p.add_argument("--max-files", type=int, default=80)
    p.set_defaults(func=cmd_export_skillhub)

    p = sub.add_parser("next", help="claim the next queued article and prepare its workspace")
    p.add_argument("--worker", type=int, required=True)
    p.add_argument("--lease", type=int, default=240, help="lease minutes")
    p.add_argument("--json", action="store_true", help="accepted for compatibility; output is always JSON")
    p.set_defaults(func=cmd_next)

    p = sub.add_parser("claim", help="claim a specific article by id for an assigned task list")
    p.add_argument("--article", required=True)
    p.add_argument("--worker", type=int, required=True)
    p.add_argument("--lease", type=int, default=43200, help="lease minutes (default 30 days)")
    p.add_argument("--json", action="store_true", help="accepted for compatibility; output is always JSON")
    p.set_defaults(func=cmd_claim)

    p = sub.add_parser("commit", help="mark an article processed and clean its workspace")
    p.add_argument("--worker", type=int, required=True)
    p.add_argument("--article", required=True)
    p.add_argument("--card", action="append", help="card id produced by this article")
    p.set_defaults(func=cmd_commit)

    p = sub.add_parser("release", help="release an article claim")
    p.add_argument("--article", required=True)
    p.set_defaults(func=cmd_release)

    p = sub.add_parser("queue", help="show ingestion queue status")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_queue)

    p = sub.add_parser("sync", help="queue only newly published source articles")
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--category", type=int)
    p.add_argument("--since")
    p.add_argument("--page-size", type=int, default=50)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("ledger", help="show the distilled-article ledger")
    p.add_argument("--json", action="store_true")
    p.add_argument("--limit", type=int, default=30)
    p.set_defaults(func=cmd_ledger)

    p = sub.add_parser("tree", help="list or query the current taxonomy tree")
    p.add_argument("--query")
    p.add_argument("--domain")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_tree)

    p = sub.add_parser("path-check", help="check a proposed path for duplicate/similar taxonomy nodes")
    p.add_argument("--path", required=True)
    p.add_argument("--title")
    p.add_argument("--description")
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_path_check)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cfg = load_config(args.root)
    return args.func(args, cfg)


if __name__ == "__main__":
    raise SystemExit(main())
