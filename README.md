# google-search

[![Release](https://img.shields.io/github/v/release/longlannet/google-search?label=release)](https://github.com/longlannet/google-search/releases)
[![License](https://img.shields.io/github/license/longlannet/google-search)](./LICENSE)
[![Python](https://img.shields.io/badge/python-3.x-blue)](https://www.python.org/)
[![OpenClaw Skill](https://img.shields.io/badge/OpenClaw-Skill-7c3aed)](https://github.com/longlannet/google-search)

> 面向 OpenClaw 的 Serper.dev 实时 Google 搜索 skill。

`google-search` 是一个项目型 OpenClaw skill 仓库，把 [Serper.dev](https://serper.dev) 封装成可重复使用的本地工作流，支持网页、新闻、图片、地图、评论、网页提取与 Lens 风格反查等能力。

## 特性概览

- 支持网页、新闻、图片、视频、购物、学术、专利、地点、地图、评论、网页提取、Lens 反查等能力
- 支持 pretty / json / raw / compact 输出
- 支持 `maps-reviews` 地图到评论工作流
- 提供 `smoke_test.py` 轻量健康检查与 `selfcheck.py` 分组自检
- 提供轻量本地测试与最小 GitHub Actions 校验
- 提供适合 agent 按需读取的参考文档
- 提供面向自动化/CI 的 JSON 输出、保存文件、静默模式与分类退出码
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
│   ├── automation.md
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
- 当前 Python 依赖很轻，核心主要是 `requests`

---

## 安装

直接运行安装脚本即可。默认逻辑是：

- 当前 `python3` 已满足运行时依赖 → 直接复用
- 当前 `python3` 不满足 → 回退到本地 `.venv`
- 安装阶段默认跑轻量 `smoke_test.py`
- 如需更完整检查，可显式启用 full selfcheck

```bash
bash scripts/install.sh
bash scripts/install.sh --venv
bash scripts/install.sh --save-json /tmp/google-search-install.json --quiet
```

前提是你已经准备好了运行时配置：

```bash
cp config/serper.env.example config/serper.env
```

然后把你的 Serper API key 写进 `config/serper.env`。

---

## 快速开始

1. 复制配置文件
2. 写入你的 Serper API key
3. 运行安装脚本
4. 跑一条测试命令

例如：

```bash
cp config/serper.env.example config/serper.env
bash scripts/install.sh
python3 scripts/search.py web "OpenAI"
```

---

## 配置方法

先复制本地配置模板：

```bash
cp config/serper.env.example config/serper.env
```

然后编辑 `config/serper.env`，填入你的真实 key：

```env
SERPER_API_KEY=your_real_key_here
```

配置加载器支持多 key，并会按轮转方式使用。

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

更多可直接复制的示例见：[`references/examples.md`](./references/examples.md)

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

更多 maps / reviews / workflow 示例见：[`references/examples.md`](./references/examples.md)

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

更多 machine-readable 示例见：[`references/examples.md`](./references/examples.md)

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

## 健康检查与自检

最轻量的健康检查：

```bash
python3 scripts/smoke_test.py
```

基础分组自检：

```bash
python3 scripts/selfcheck.py --basic
python3 scripts/selfcheck.py --group network
python3 scripts/selfcheck.py --group parsing --save /tmp/google-search-parsing.json --quiet
```

更完整的联网检查：

```bash
python3 scripts/selfcheck.py --full --compact
```

说明：

- `smoke_test.py` 只验证最小可用链路
- `selfcheck.py` 是联网 smoke / integration check，不是纯单元测试
- `selfcheck.py` 支持 `--group`、`--save`、`--fail-fast`、`--quiet`
- 结果会受网络状态、API 配额、第三方返回结构变化等影响
- 自检退出码支持按类别区分 config / network / parsing / workflow / mixed failure

---

## 校验

```bash
bash scripts/check.sh
```

当前 `check.sh` 会做：

- Python 语法检查
- 本地轻量测试
- 轻量 skill check

CI 也会覆盖这条路径。

---

## 参考文档

- 自动化/CI：[`references/automation.md`](./references/automation.md)
- 端点规则：[`references/endpoints.md`](./references/endpoints.md)
- 命令示例：[`references/examples.md`](./references/examples.md)
- 变更历史：[`CHANGELOG.md`](./CHANGELOG.md)

---

## 说明

- `config/serper.env` 是本地敏感配置，不应提交。
- 默认 locale 是 `gl=cn`、`hl=zh-cn`，如果不覆盖，结果会更偏中文区域。
- `smoke_test.py` 是最轻量的健康探针；`selfcheck.py` 偏重，更像联网集成测试。
- 如果你只想做快速验证，优先跑 `python3 scripts/smoke_test.py` 或 `bash scripts/check.sh`。
