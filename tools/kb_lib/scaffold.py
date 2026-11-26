from __future__ import annotations

from pathlib import Path
from typing import Any

from . import taxonomy
from .config import Config
from .markdown_util import AUTO_END, AUTO_START

README_TEMPLATE = """# {title}

## 这层是什么

{description}

## 允许放入

{allow}

## 不允许放入

{deny}

## 子目录

{children}

## 场景索引

{AUTO_START}
<!-- 该区块由 `python tools/kb.py index` 自动维护，请勿手工编辑 -->
{AUTO_END}

## 新增规范

- 一张卡只描述一个场景/技巧，文件名使用英文 kebab-case。
- frontmatter 必填字段见 `PLAN.md` 第 6 节。
- 新增前必须运行 `python tools/kb.py dedup --draft <file>`。
- 写完后运行 `python tools/kb.py validate`。
"""


def _bullets(items: list[str], fallback: str) -> str:
    if not items:
        return f"- {fallback}"
    return "\n".join(f"- {item}" for item in items)


def _child_links(children: list[tuple[str, dict[str, Any], str]]) -> str:
    if not children:
        return "- （暂无子目录；新增场景卡直接放在本目录）"
    lines = []
    for child_path, child_node, _slug in children:
        title = child_node.get("title", child_path.rsplit("/", 1)[-1])
        lines.append(f"- [{title}]({child_path.rsplit('/', 1)[-1]}/README.md) —— {child_node.get('description', '')}")
    return "\n".join(lines)


def render_readme(
    path: str,
    node: dict[str, Any],
    children: list[tuple[str, dict[str, Any], str]],
    default_allow: list[str],
    default_deny: list[str],
) -> str:
    allow = [str(x) for x in (node.get("allow") or default_allow)]
    deny = [str(x) for x in (node.get("deny") or default_deny)]
    title = str(node.get("title") or path.rsplit("/", 1)[-1])
    description = str(node.get("description") or "本目录尚未补充说明，请在 README 中完善。")
    return README_TEMPLATE.format(
        title=title,
        description=description,
        allow=_bullets(allow, "待补充"),
        deny=_bullets(deny, "待补充"),
        children=_child_links(children),
        AUTO_START=AUTO_START,
        AUTO_END=AUTO_END,
    )


def ensure_tree(cfg: Config) -> list[Path]:
    """Create every taxonomy directory and a README if missing.

    Existing README files are left untouched so hand-written guidance survives
    repeated scaffolding.
    """
    data = taxonomy.load(cfg.taxonomy)
    defaults = taxonomy.defaults(data)
    domains = data["domains"]
    created: list[Path] = []

    for path, node in taxonomy.walk(domains):
        directory = cfg.knowledge / path
        directory.mkdir(parents=True, exist_ok=True)
        readme = directory / "README.md"
        if not readme.exists():
            children = taxonomy.children_of(domains, path)
            readme.write_text(
                render_readme(path, node, children, defaults["allow"], defaults["deny"]),
                encoding="utf-8",
            )
            created.append(readme)

    root_readme = cfg.knowledge / "README.md"
    if not root_readme.exists():
        lines = [
            "# 安全知识库",
            "",
            "## 这层是什么",
            "",
            "按安全类型组织的场景卡片树。每个目录的 README 说明该层范围，卡片是原子拆解单位。",
            "",
            "## 顶层目录",
            "",
        ]
        for slug, node in domains.items():
            lines.append(f"- [{node.get('title', slug)}]({slug}/README.md) —— {node.get('description', '')}")
        lines += [
            "",
            "## 场景索引",
            "",
            AUTO_START,
            "<!-- 该区块由 `python tools/kb.py index` 自动维护 -->",
            AUTO_END,
            "",
        ]
        root_readme.write_text("\n".join(lines), encoding="utf-8")
        created.append(root_readme)

    cve_readme = cfg.cve / "README.md"
    if not cve_readme.exists():
        cve_readme.write_text(
            """# CVE 库

## 规则

- 一个组件/框架一个 md，文件名使用英文 kebab-case。
- 每个 CVE 一行：编号、一句话介绍、适用版本、一句话 PoC。
- 没有 PoC 写 `无替代`。
- 不写来源。
- 场景卡只引用 CVE 编号，详情统一查本目录。
""",
            encoding="utf-8",
        )
        created.append(cve_readme)

    return created
