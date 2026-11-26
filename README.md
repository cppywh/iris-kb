# Iris 安全知识库

目录树 + 场景卡片 + CVE 索引，检索优先面向 AI 读者。

- 个人站：<https://cppywh.github.io/>
- 在线检索：<https://cppywh.github.io/iris-kb/>
- 作者：<https://github.com/cppywh>

## 来源与边界

- 基于 [godownio/vuln-masterhub](https://github.com/godownio/vuln-masterhub)（commit `30d3e440`）改写，继承其 7,060 张卡片与 13 域 taxonomy。
- **上游仓库没有 LICENSE。** 本仓库对其内容不主张任何授权，仅作个人学习与检索用途。若原作者提出异议，应立即下线相关内容。
- 卡片以攻防与利用视角为主，仅可用于授权测试、教学与比赛。任何人不得据此对未授权目标实施攻击。

## 本地使用

```bash
python tools/kb.py index        # 建关键词索引（生成 data/kb.sqlite）
python tools/kb.py validate     # 校验卡片、路径与 README
python tools/kb.py search "comparator rhino" --json --mode keyword
python site/serve.py 8000       # 打开 http://127.0.0.1:8000/site/
```

依赖：`filelock`，其余为 Python 标准库。索引数据库写在 `data/`，不进版本库。

## 在线版

静态前端，无需构建：

```bash
python site/build.py            # 由 data/index.json 生成 site/data/cards.json
```

推送 `main` 后由 GitHub Pages 直接托管仓库根目录，入口 `index.html` 跳转到 `site/`。

## 目录

| 路径 | 说明 |
|---|---|
| `taxonomy.yaml` | 分类树定义，13 域 / 168 目录 |
| `knowledge/` | 场景卡片，一卡一场景，固定七段 |
| `cve/` | CVE 索引 |
| `tools/kb.py` | 36 个子命令：索引、检索、查重、校验、入库 |
| `tools/kb_lib/` | 上述命令的实现模块 |
| `site/` | 独立静态前端（HTML/CSS/JS，无框架、无 CDN） |
| `docs/` | 上游部署与验收记录，保留作溯源 |

## 远端

| 名称 | 地址 | 用途 |
|---|---|---|
| `origin` | `git@github.com:cppywh/iris-kb.git` | 本仓库 |
| `upstream` | `godownio/vuln-masterhub` | 原始项目，只读参考，不推送 |
