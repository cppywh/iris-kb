# Iris 前端

独立静态前端，不复用上游 Hugo 主题，也不与其视觉或路由冲突。

## 启动

```bash
python site/serve.py 8000
# http://127.0.0.1:8000/site/
```

服务根目录是 `iris-kb/`，因此前端能直接读取 `../knowledge/` 下的卡片原文。

必须通过 HTTP 打开。`file://` 下浏览器会拦截 `fetch`，卡片正文加载不出来。

## 数据

`site/data/cards.json` 由 `data/index.json` 精简生成：

```bash
python tools/kb.py index        # 先更新源索引
python site/build.py            # 再生成前端数据
```

只保留检索需要的字段，去掉本机绝对路径。

## 与上游的差异

| 上游 | 这里 |
|---|---|
| 手绘树形画布 + 缩放入场动画 | 扁平检索列表 + 右侧阅读区 |
| 猫、景物彩蛋、随机叶子 | 仅保留「随机一张」 |
| GitHub 登录、API Key 控制台、收藏云同步 | 无账号，叶签存 localStorage |
| Hugo + Hextra 构建 | 单页 HTML/CSS/JS，无构建、无 CDN、无框架 |
| 米黄木色手绘风 | 中性灰 + 青绿，浅/深色跟随系统 |

## 文件

| 文件 | 作用 |
|---|---|
| `index.html` | 页面骨架 |
| `styles.css` | 主题与布局 |
| `app.js` | 检索、筛选、Markdown 渲染 |
| `build.py` | 生成 `data/cards.json` |
| `serve.py` | 本地 HTTP 服务 |
