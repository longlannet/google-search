# openclaw-skill-google-search

一个面向 OpenClaw 的生产可用技能仓库，用于通过 [Serper.dev](https://serper.dev) 调用 Google 搜索能力。

这个 skill 把 Serper 封装成一个可复用的 OpenClaw / AgentSkills 包，提供：

- 多种搜索端点
- 适合人类阅读的 pretty 输出
- 适合程序处理的 JSON / raw 输出
- 本地地图到评论的工作流支持
- 示例命令与自检脚本
- 拆分好的参考文档，方便 agent 按需读取

## 支持的能力

### 搜索端点

- `web` / `search`
- `images`
- `news`
- `videos`
- `shopping`
- `scholar`
- `patents`
- `places`
- `maps`
- `reviews`
- `autocomplete`
- `webpage`
- `lens`

### 工作流辅助

- `maps-reviews`
  - 先执行 `maps`
  - 自动解析 `placeId` / `cid` / `fid`
  - 再调用 `reviews`

### 帮助类入口

- `overview`
- `cheatsheet`
- `quickref`
- `help`
- `examples`

---

## 仓库结构

```text
google-search/
├── SKILL.md
├── README.md
├── requirements.txt
├── .gitignore
├── config/
│   └── serper.env.example
├── references/
│   ├── endpoints.md
│   └── examples.md
└── scripts/
    ├── args.py
    ├── client.py
    ├── renderers.py
    ├── search.py
    ├── selfcheck.py
    ├── utils.py
    └── workflows.py
```

---

## 运行前提

- Python 3
- 一个可用的 Serper API key
- 当前默认 Python 依赖很轻，主要是 `requests`

---

## 安装

### 方式一：直接克隆仓库

```bash
git clone https://github.com/longlannet/openclaw-skill-google-search.git
cd openclaw-skill-google-search
pip install -r requirements.txt
```

说明：当前 `requirements.txt` 很轻，默认主要安装 `requests`。

### 方式二：放到 OpenClaw 的 skills 目录中

如果你是给 OpenClaw 本地技能系统使用，可以把仓库放到你的 skills 目录，例如：

```bash
cd ~/.openclaw/workspace/skills
git clone https://github.com/longlannet/openclaw-skill-google-search.git google-search
cd google-search
pip install -r requirements.txt
```

说明：如果你的环境里已经有 `requests`，这一步通常会很快。

如果你已经有目录，也可以直接把本仓库内容复制进去，只要保留下面这些结构即可：

- `SKILL.md`
- `scripts/`
- `references/`
- `config/serper.env.example`
- `.gitignore`
- `requirements.txt`

---

## 配置方法

先从示例文件复制本地配置：

```bash
cp config/serper.env.example config/serper.env
```

然后编辑 `config/serper.env`，填入你的真实 key：

```env
SERPER_API_KEY=your_real_key_here
```

配置加载器也支持多 key，并会按轮转方式使用。

---

## 基本用法

### 标准网页搜索

```bash
python3 scripts/search.py web "OpenAI"
```

### 兼容旧版 positional 形式

```bash
python3 scripts/search.py "OpenAI" 3 1 us en
```

### 新闻搜索

```bash
python3 scripts/search.py news "OpenAI" --limit 5
```

### 图片搜索

```bash
python3 scripts/search.py images "cute cat" --json
```

### 网页正文提取

```bash
python3 scripts/search.py webpage "https://openclaw.ai"
```

### Lens 反查

```bash
python3 scripts/search.py lens "https://example.com/image.jpg" --json --compact
```

---

## 地图与评论

### 地图搜索

```bash
python3 scripts/search.py maps "coffee shanghai"
```

### 直接查评论

需要提供 `--place-id`、`--cid` 或 `--fid` 之一：

```bash
python3 scripts/search.py reviews --place-id ChIJ...
```

### 工作流：maps → reviews

选择指定地图结果：

```bash
python3 scripts/search.py maps-reviews "coffee shanghai" --pick 2 --limit 3
```

抓取所有返回地点的评论：

```bash
python3 scripts/search.py maps-reviews "coffee shanghai" --all --limit 2
```

说明：

- `maps-reviews --all` 不能与 `--pick` 同时使用
- `reviews` 不能只给普通文本查询，必须带地点标识

---

## 机器可读输出

### 包装后的 JSON

```bash
python3 scripts/search.py web "OpenAI" --json
```

### 原始 API JSON

```bash
python3 scripts/search.py news "OpenAI" --raw
```

### 紧凑 JSON

```bash
python3 scripts/search.py web "OpenAI" --json --compact
```

### 保存到文件

```bash
python3 scripts/search.py web "OpenAI" --json --save /tmp/serper.json
```

### `maps-reviews` 的输出行为

- `maps-reviews --json` 返回工作流包装结构
- `maps-reviews --raw` 只返回链式请求的原始 payload
- `maps-reviews --all --json` 返回批量包装结构
- `maps-reviews --all --raw` 返回 `{maps, results}`

---

## 自检

运行基础健康检查：

```bash
python3 scripts/selfcheck.py
```

输出紧凑 JSON：

```bash
python3 scripts/selfcheck.py --compact
```

运行完整检查：

```bash
python3 scripts/selfcheck.py --full
```

基础自检目前覆盖：

- `search`
- `images`
- `news`
- `autocomplete`
- `maps`
- `patents`
- `webpage`
- `lens`
- `maps-reviews`
- `maps-reviews-pick2`
- `maps-reviews-all`

---

## OpenClaw 使用说明

这个仓库按 OpenClaw skill 的方式组织：

- `SKILL.md` 保持精简，用于触发与导航
- `references/` 保存详细端点说明与示例
- `scripts/` 保存稳定的执行逻辑

如果你是在 OpenClaw 的 skills 目录中使用它，请把真实 API key 放到：

```text
config/serper.env
```

**不要提交这个文件。**

---

## 安全与仓库卫生

这个仓库默认忽略：

- `config/serper.env`
- `runtime/`
- `venv/`
- Python 缓存文件

这样可以在上传到 GitHub 时避免把真实 API key 和运行产物一起提交。

---

## 推荐仓库名

```text
openclaw-skill-google-search
```

---

## 许可与使用

你可以根据自己的 OpenClaw 工作区和私有技能管理方式，选择合适的许可证或私有仓库策略。
