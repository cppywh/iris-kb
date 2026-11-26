from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from . import dedup, taxonomy
from .config import Config
from .indexer import CVE_RE, REQUIRED_FIELDS, discover_cards, parse_cves
from .markdown_util import extract_auto_index, render_index_table

ALLOWED_STATUS = {"draft", "verified", "deprecated"}
ALLOWED_CONFIDENCE = {"high", "medium", "low"}
ALLOWED_TYPES = {"scenario", "case", "technique"}
KEBAB_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def validate(cfg: Config, strict_paths: bool = False) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    try:
        tax = taxonomy.load(cfg.taxonomy)
        known_paths = set(taxonomy.flatten(tax))
    except Exception as exc:  # noqa: BLE001
        return {"errors": [{"file": str(cfg.taxonomy), "error": str(exc)}], "warnings": [], "cards": 0}

    nodes = list(taxonomy.flatten(tax).items())
    for index, (left_path, left_node) in enumerate(nodes):
        left_title = str(left_node.get("title") or left_path.rsplit("/", 1)[-1])
        for right_path, right_node in nodes[index + 1:]:
            right_title = str(right_node.get("title") or right_path.rsplit("/", 1)[-1])
            left_norm = dedup.normalize(left_title)
            right_norm = dedup.normalize(right_title)
            if left_norm and left_norm == right_norm:
                errors.append(
                    {
                        "file": str(cfg.taxonomy),
                        "error": f"duplicate taxonomy title '{left_title}': {left_path} vs {right_path}",
                    }
                )
            elif dedup.jaccard(dedup.trigrams(left_title), dedup.trigrams(right_title)) >= 0.85:
                errors.append(
                    {
                        "file": str(cfg.taxonomy),
                        "error": f"similar taxonomy titles '{left_title}' ({left_path}) vs '{right_title}' ({right_path})",
                    }
                )

    discovery = discover_cards(cfg)
    for item in discovery.errors:
        errors.append({"file": item["file"], "error": item["error"]})

    seen_ids: dict[str, str] = {}
    seen_titles: dict[str, str] = {}
    cards_by_parent: dict[Path, list[Any]] = {}

    for card in discovery.cards:
        where = str(card.file_path)
        for field in REQUIRED_FIELDS:
            value = card.meta.get(field)
            if value is None or value == "" or value == []:
                errors.append({"file": where, "error": f"missing required frontmatter field: {field}"})

        if card.meta.get("sources") or card.meta.get("source"):
            errors.append({"file": where, "error": "frontmatter must not contain sources/source fields"})
        if card.meta.get("source_url"):
            errors.append({"file": where, "error": "frontmatter must not contain source_url"})

        if card.id in seen_ids:
            errors.append({"file": where, "error": f"duplicate id '{card.id}' also in {seen_ids[card.id]}"})
        else:
            seen_ids[card.id] = where

        expected_parent = card.file_path.parent.relative_to(cfg.knowledge).as_posix()
        expected_parent = "" if expected_parent == "." else expected_parent
        if card.path != expected_parent:
            errors.append(
                {
                    "file": where,
                    "error": f"path '{card.path}' does not match file location '{expected_parent}'",
                }
            )

        if card.path and card.path not in known_paths:
            message = f"path '{card.path}' is not in taxonomy.yaml"
            (errors if strict_paths else warnings).append({"file": where, "error": message})

        if card.domain and card.path and card.domain != card.path.split("/", 1)[0]:
            errors.append({"file": where, "error": f"domain '{card.domain}' does not match path '{card.path}'"})

        if card.status not in ALLOWED_STATUS:
            errors.append({"file": where, "error": f"invalid status '{card.status}'"})
        if card.confidence not in ALLOWED_CONFIDENCE:
            errors.append({"file": where, "error": f"invalid confidence '{card.confidence}'"})
        if card.type not in ALLOWED_TYPES:
            warnings.append({"file": where, "error": f"unusual type '{card.type}'"})

        dos_tags = {tag.lower() for tag in card.tags}
        if (
            dos_tags & {"dos", "denial-of-service", "denial_of_service"}
            or card.technique.lower() in {"dos", "denial-of-service", "denial_of_service"}
            or "拒绝服务" in card.title
            or "denial of service" in card.title.lower()
        ):
            errors.append({"file": where, "error": "DoS/拒绝服务场景按规则排除"})

        for phrase in ("未打通", "理论利用链", "可触发与可利用差距"):
            if card.status != "verified" and phrase in card.body:
                warnings.append({"file": where, "error": f"card may be unproven ('{phrase}'); exclude unless verified"})
                break

        if card.status == "verified":
            verification = card.meta.get("verification") or {}
            if not verification.get("verified_by") or not verification.get("verified_at"):
                errors.append(
                    {
                        "file": where,
                        "error": "verified cards require verification.verified_by and verification.verified_at",
                    }
                )

        normalized_title = "".join(str(card.title).lower().split())
        if normalized_title in seen_titles:
            warnings.append(
                {
                    "file": where,
                    "error": f"duplicate normalized title also in {seen_titles[normalized_title]}",
                }
            )
        else:
            seen_titles[normalized_title] = where

        if card.cve:
            for cve in card.cve:
                if not CVE_RE.match(cve):
                    errors.append({"file": where, "error": f"invalid CVE id '{cve}'"})

        if "affected_versions" in card.meta:
            versions = card.meta["affected_versions"]
            if isinstance(versions, (list, tuple)):
                valid_versions = any(str(item).strip() for item in versions)
            elif isinstance(versions, str):
                valid_versions = bool(versions.strip())
            else:
                valid_versions = False
            if not valid_versions:
                errors.append(
                    {"file": where, "error": "affected_versions is present but empty/invalid; omit it instead"}
                )

        cards_by_parent.setdefault(card.file_path.parent, []).append(card)

    # Every directory under knowledge must have a README.
    directories = {cfg.knowledge} | {path for path in cfg.knowledge.rglob("*") if path.is_dir()}
    for directory in sorted(directories):
        readme = directory / "README.md"
        if not readme.exists():
            errors.append({"file": str(directory), "error": "directory is missing README.md"})
            continue
        items = sorted(cards_by_parent.get(directory, []), key=lambda c: c.title.lower())
        expected = render_index_table(
            [
                {
                    "title": card.title,
                    "filename": card.filename,
                    "status": card.status,
                    "confidence": card.confidence,
                    "updated": card.updated,
                }
                for card in items
            ]
        )
        actual = extract_auto_index(readme.read_text(encoding="utf-8"))
        if actual is None:
            warnings.append({"file": str(readme), "error": "missing AUTO-INDEX block; run `kb index`"})
        elif expected not in actual:
            warnings.append({"file": str(readme), "error": "AUTO-INDEX is stale; run `kb index`"})

    # CVE files.
    for file_path in sorted(cfg.cve.glob("*.md")):
        if file_path.name == "README.md":
            continue
        if not KEBAB_RE.match(file_path.stem):
            warnings.append({"file": str(file_path), "error": "CVE component filename should be kebab-case"})
        body = file_path.read_text(encoding="utf-8")
        for line_no, line in enumerate(body.splitlines(), start=1):
            line = line.strip()
            if not line.startswith("|"):
                continue
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if len(cells) < 4:
                errors.append({"file": str(file_path), "error": f"line {line_no}: expected 4 columns"})
                continue
            cve = cells[0].strip("` ")
            if cve.lower() == "cve" or set(cve) <= {"-", ":"}:
                continue
            if not CVE_RE.match(cve):
                warnings.append({"file": str(file_path), "error": f"line {line_no}: '{cve}' is not a CVE id"})
        parsed = [row for row in parse_cves(cfg) if row["component"] == file_path.stem]
        if not parsed:
            warnings.append({"file": str(file_path), "error": "no valid CVE table rows found"})

    return {
        "errors": errors,
        "warnings": warnings,
        "cards": len(discovery.cards),
    }
