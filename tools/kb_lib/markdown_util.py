from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

AUTO_START = "<!-- AUTO-INDEX:START -->"
AUTO_END = "<!-- AUTO-INDEX:END -->"

AUTO_BLOCK_RE = re.compile(
    re.escape(AUTO_START) + r".*?" + re.escape(AUTO_END),
    re.DOTALL,
)


def slugify(value: str, fallback: str = "card") -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-")
    return value or fallback


def slug_from_id(card_id: str, fallback: str = "card") -> str:
    tail = card_id.rsplit("/", 1)[-1].strip().lower()
    return slugify(tail, fallback)


def render_index_table(cards: Iterable[dict[str, Any]]) -> str:
    rows = [
        "| 卡片 | 状态 | 置信度 | 更新时间 |",
        "|---|---|---|---|",
    ]
    for card in cards:
        title = str(card.get("title") or card.get("id"))
        filename = str(card.get("filename") or "")
        status = str(card.get("status") or "")
        confidence = str(card.get("confidence") or "")
        updated = str(card.get("updated") or "")
        rows.append(f"| [{title}]({filename}) | {status} | {confidence} | {updated} |")
    if len(rows) == 2:
        rows.append("| （暂无卡片） | | | |")
    return "\n".join(rows)


def update_auto_index(readme_path: Path, table: str) -> bool:
    """Replace the auto-index block. Returns True when the file changed."""
    if not readme_path.exists():
        return False
    text = readme_path.read_text(encoding="utf-8")
    block = f"{AUTO_START}\n{table}\n{AUTO_END}"
    if AUTO_BLOCK_RE.search(text):
        new_text = AUTO_BLOCK_RE.sub(lambda match: block, text, count=1)
    else:
        new_text = text.rstrip() + "\n\n## 场景索引\n\n" + block + "\n"
    if new_text == text:
        return False
    readme_path.write_text(new_text, encoding="utf-8")
    return True


def extract_auto_index(text: str) -> str | None:
    match = AUTO_BLOCK_RE.search(text)
    return match.group(0) if match else None


def iter_markdown_files(root: Path, skip_readme: bool = True) -> Iterable[Path]:
    if not root.exists():
        return
    for path in sorted(root.rglob("*.md")):
        if skip_readme and path.name == "README.md":
            continue
        yield path


def card_paths_by_parent(cfg_knowledge: Path) -> dict[Path, list[Path]]:
    grouped: dict[Path, list[Path]] = {}
    for path in iter_markdown_files(cfg_knowledge):
        grouped.setdefault(path.parent, []).append(path)
    return grouped
