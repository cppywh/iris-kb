# Iris Knowledge Tree

个人安全知识库：目录树 + 场景卡片 + CVE 库，检索优先面向 AI 读者。

## 来源与边界

- 基于 [godownio/vuln-masterhub](https://github.com/godownio/vuln-masterhub)（commit `30d3e440`）改写，继承其 7,060 张卡片与 13 域 taxonomy。
- 上游仓库**无 LICENSE**。本仓库仅作个人学习与授权研究使用，不对外分发、不公开部署。
- 卡片内容以攻防与利用视角为主，仅用于授权测试、教学与比赛。

## 本地使用

```bash
python tools/kb.py index      # 建关键词索引
python tools/kb.py validate   # 校验卡片与目录
python tools/kb.py search "comparator rhino" --json --mode hybrid
python tools/kb.py serve      # 本地管理台 http://127.0.0.1:5000
```

## 目录

| 路径 | 说明 |
|---|---|
| `taxonomy.yaml` | 分类树定义，13 域 / 168 目录 |
| `knowledge/` | 场景卡片，一卡一场景，固定七段 |
| `cve/` | CVE 索引 |
| `tools/` | `kb.py` CLI 与 `kb_lib/` 实现 |
| `hugo-site/` | 静态站点源码（Hextra 主题） |
| `docs/` | 上游部署与验收记录，保留作溯源 |

## 远端

- `upstream` → 原始项目，只读参考，不推送。
