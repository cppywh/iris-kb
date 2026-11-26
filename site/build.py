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

DOMAIN_TITLES = {
    "web": "Web",
    "pentest": "渗透",
    "ctf": "CTF",
    "code-analysis": "代码分析",
    "binary": "二进制",
    "ai-security": "AI 安全",
    "defense": "防御",
    "crypto": "密码学",
    "cloud": "云",
    "mobile": "移动",
    "iot": "工控物联",
    "tools": "工具",
    "blockchain": "区块链",
}


def build_tree(cards: list[dict]) -> dict:
    """按 path 前缀建树，节点只存结构，不存卡片正文。"""
    root: dict = {"name": "Iris", "path": "", "count": 0, "children": {}}

    for card in cards:
        path = card.get("path") or ""
        if not path:
            continue
        parts = path.split("/")
        node = root
        node["count"] += 1
        for i, part in enumerate(parts):
            child = node["children"].setdefault(
                part, {"name": part, "path": "/".join(parts[: i + 1]), "count": 0, "children": {}}
            )
            child["count"] += 1
            node = child

    def finalize(node: dict) -> dict:
        kids = [finalize(v) for v in node["children"].values()]
        kids.sort(key=lambda n: (-n["count"], n["name"]))
        out = {"name": node["name"], "path": node["path"], "count": node["count"]}
        if kids:
            out["children"] = kids
        if node["path"] in DOMAIN_TITLES:
            out["title"] = DOMAIN_TITLES[node["path"]]
        return out

    return finalize(root)


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

    tree = build_tree(cards)
    tree_target = OUT / "tree.json"
    tree_target.write_text(
        json.dumps(tree, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )

    print(
        f"cards={len(cards)} domains={len(domains)} paths={len(paths)} "
        f"size={target.stat().st_size / 1048576:.1f}MB -> {target}"
    )
    print(f"tree nodes={tree['count']} size={tree_target.stat().st_size / 1024:.1f}KB -> {tree_target}")


if __name__ == "__main__":
    main()
