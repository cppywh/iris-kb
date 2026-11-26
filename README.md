# Iris 安全知识库

径向星图 + 单场景卡片 + CVE 索引，检索优先面向 AI 读者。

**在线：<https://cppywh.github.io/iris-kb/>**

- 个人站：<https://cppywh.github.io/>
- 作者：<https://github.com/cppywh>

## 来源与边界

- 基于 [godownio/vuln-masterhub](https://github.com/godownio/vuln-masterhub)（commit `30d3e440`）改写，继承其 7,060 张卡片与 13 域 taxonomy。
- **上游仓库没有 LICENSE。** 本仓库对其内容不主张任何授权，仅作个人学习与检索用途。若原作者提出异议，应立即下线相关内容。
- 卡片以攻防与利用视角为主，仅可用于授权测试、教学与比赛。任何人不得据此对未授权目标实施攻击。

## 在线

仓库根目录即站点，GitHub Pages 直接托管，无需构建步骤：

| 路径 | 内容 |
|---|---|
| `/` | 知识库前端（星图浏览） |
| `/data/web/cards.json` | 卡片元数据（5.8 MB，gzip 后约 1.3 MB） |
| `/data/web/tree.json` | 目录树骨架，167 节点 |
| `/knowledge/<path>/<file>.md` | 卡片原文，前端按需拉取 |

## 本地

```bash
python tools/kb.py index        # 建关键词索引（写 data/kb.sqlite）
python tools/kb.py validate     # 校验卡片、路径与 README
python tools/kb.py search "comparator rhino" --json --mode keyword

python build.py                 # 由 data/index.json 生成 data/web/*.json
python serve.py 8000            # http://127.0.0.1:8000/
```

依赖：`filelock`，其余为标准库。`data/kb.sqlite` 与 `data/index.json` 是本地生成物，不进版本库。

## 交互

| 操作 | 行为 |
|---|---|
| 拖拽 | 平移 |
| 滚轮 / 双指 | 以指针为中心缩放 |
| 单击枝干 | 收窄检索范围，高亮子树 |
| 双击节点 | 放大到该区域 |
| 悬停星点 | 底部显示卡片标题与路径 |
| 点击星点 | 右侧打开卡片正文 |
| `/` | 聚焦搜索 |
| `Space` / `PgUp` `PgDn` | 阅读面板翻页 |
| `Esc` | 关闭阅读面板 |

URL 参数：`?q=` 预填搜索、`?scope=web/java` 预选范围、`?theme=light` 强制浅色、`#<card-id>` 直开卡片、`?still=1` 跳过入场动画。

## 目录

| 路径 | 说明 |
|---|---|
| `index.html` `styles.css` `app.js` `world.js` | 前端，无框架、无 CDN、无构建 |
| `data/web/` | 前端数据，由 `build.py` 生成 |
| `taxonomy.yaml` | 分类树定义，13 域 / 168 目录 |
| `knowledge/` | 场景卡片，一卡一场景，固定七段 |
| `tools/kb.py` | 36 个子命令：索引、检索、查重、校验、入库 |
| `tools/kb_lib/` | 上述命令的实现模块 |
| `FRONTEND.md` | 前端实现说明 |
| `docs/` | 上游部署与验收记录，保留作溯源 |

## 远端

| 名称 | 地址 | 用途 |
|---|---|---|
| `origin` | `git@github.com:cppywh/iris-kb.git` | 本仓库 |
| `upstream` | `godownio/vuln-masterhub` | 原始项目，只读参考，不推送 |
