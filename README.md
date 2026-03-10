# openclaw-skill-google-search

[![Release](https://img.shields.io/github/v/release/longlannet/openclaw-skill-google-search?label=release)](https://github.com/longlannet/openclaw-skill-google-search/releases)
[![License](https://img.shields.io/github/license/longlannet/openclaw-skill-google-search)](./LICENSE)
[![Python](https://img.shields.io/badge/python-3.x-blue)](https://www.python.org/)
[![OpenClaw Skill](https://img.shields.io/badge/OpenClaw-Skill-7c3aed)](https://github.com/longlannet/openclaw-skill-google-search)

> 基于 Serper.dev 的 OpenClaw Google 搜索技能，支持网页、新闻、图片、地图、评论、网页提取、Lens 反查等能力。

`openclaw-skill-google-search` 是一个面向 OpenClaw / AgentSkills 的技能仓库，用来把 [Serper.dev](https://serper.dev) 封装成一个结构清晰、可复用、便于维护的 Google 搜索 skill。

它适合这些场景：

- 给 OpenClaw 增加实时 Google 搜索能力
- 把 Serper 搜索接口整理成可复用技能
- 在私有仓库或公开仓库中长期维护一个独立 skill
- 同时兼顾人工阅读输出与机器可处理输出

## 特性概览

- 支持网页、新闻、图片、视频、购物、学术、专利、地点、地图、评论、网页提取、Lens 反查等能力
- 支持 pretty / json / raw / compact 输出
- 支持 `maps-reviews` 地图到评论工作流
- 提供 `selfcheck.py` 自检脚本
- 提供适合 agent 按需读取的参考文档
- 提供中文 README、安装说明和 changelog

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
├── CHANGELOG.md
├── requirements.txt
├── .gitignore
├── config/
│   └── serper.env.example
├── references/
│   ├── endpoints.md
│   └── examples.md
├── tests/
│   ├── test_args.py
│   └── test_workflows.py
└── scripts/
    ├── args.py
    ├── client.py
    ├── helptext.py
    ├── io_common.py
    ├── renderers.py
    ├── renderers_json.py
    ├── renderers_pretty.py
    ├── response_shapes.py
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

## 快速开始

1. 安装依赖
2. 复制配置文件
3. 写入你的 Serper API key
4. 运行一条测试命令

示例：

```bash
cp config/serper.env.example config/serper.env
python3 scripts/search.py web "OpenAI"
```

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

## OpenClaw 集成步骤

如果你要把它作为 OpenClaw 本地 skill 使用，推荐按下面步骤操作：

1. 把仓库放到 OpenClaw 的 skills 目录，例如：

```bash
cd ~/.openclaw/workspace/skills
git clone https://github.com/longlannet/openclaw-skill-google-search.git google-search
```

2. 安装依赖：

```bash
cd google-search
pip install -r requirements.txt
```

3. 复制配置文件并写入真实 API key：

```bash
cp config/serper.env.example config/serper.env
```

4. 确认目录中存在这些关键文件：

- `SKILL.md`
- `scripts/search.py`
- `references/endpoints.md`
- `config/serper.env`

5. 让 OpenClaw 重新加载 skills（具体方式取决于你的运行方式；如果当前实例不会自动发现新 skill，可重启一次 OpenClaw）。

这个仓库按 OpenClaw skill 的方式组织：

- `SKILL.md` 用于触发与导航
- `references/` 保存详细端点说明与示例
- `scripts/` 保存稳定的执行逻辑

真实 API key 请放到：

```text
config/serper.env
```

**不要提交这个文件。**

---

## 限制与注意事项

- 默认区域参数目前偏中文环境：`gl=cn`、`hl=zh-cn`
- `reviews` 不能只传普通文本查询，必须提供 `--place-id`、`--cid` 或 `--fid` 之一
- `maps-reviews --all` 会对多个地点逐个抓评论，耗时和 credits 消耗都会更高
- `lens` 返回空结果不一定代表请求失败，有时只是没有匹配项
- `selfcheck.py` 更接近联网健康检查（smoke test），不是完全离线、可重复的单元测试

---

## 安全与仓库卫生

这个仓库默认忽略：

- `config/serper.env`
- `runtime/`
- `venv/`
- Python 缓存文件

这样可以在上传到 GitHub 时避免把真实 API key 和运行产物一起提交。

---

## 版本发布

- 版本变更记录见 [CHANGELOG.md](./CHANGELOG.md)
- 当前最新发布版本为 `v0.1.3`
- GitHub Releases 可用于查看阶段性发布说明

---

## 结构冻结说明

当前仓库的脚本结构已基本定型，后续维护建议优先：

- 增加功能
- 补充文档
- 增量补测试

除非出现明显维护成本问题，否则**不再继续进行大规模结构重构**。

当前推荐结构如下：

```text
scripts/
├── args.py
├── client.py
├── helptext.py
├── io_common.py
├── renderers.py
├── renderers_json.py
├── renderers_pretty.py
├── response_shapes.py
├── search.py
├── selfcheck.py
├── utils.py
└── workflows.py
```

---

## 许可

本项目使用 [MIT License](./LICENSE) 发布。
