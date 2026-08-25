# OpenClaw Google Search Skill

通过 [Serper.dev](https://serper.dev) 为 OpenClaw 提供实时 Google 搜索、图片、新闻、视频、地点、地图、评论、购物、Scholar、专利、网页提取和 Lens 查询。

正式入口是 `scripts/run.sh`。`scripts/` 下的 Python 模块属于内部实现，不承诺独立调用兼容性。

## 运行要求

- Linux
- Bash
- Python 3.10–3.14
- `flock`、GNU `stat`、`id`、`dirname`
- Serper API key

运行依赖由 `requirements.txt` 以版本和 artifact SHA-256 锁定。

## 安装

从仓库根目录执行：

```bash
/bin/bash -p scripts/install.sh
```

默认安装会联网访问 PyPI，在私有候选目录中创建 `.venv`，使用 `--require-hashes --only-binary=:all:` 安装锁定依赖，验证后再发布。安装器使用持久排他锁，两个安装进程不会同时修改 `.venv`；运行进程持共享锁，不会跨越 runtime 发布窗口。

只检查现有 runtime，不安装依赖：

```bash
/bin/bash -p scripts/install.sh --check
```

安装和发布不承诺抵御同 UID 主动篡改，也不宣称断电/内核崩溃级事务原子性。

## API Key

OpenClaw 通常通过 `SERPER_API_KEY` 注入 key。直接命令行使用也支持：

```bash
export SERPER_API_KEY='your-key'
```

多 key 轮转可使用逗号或换行分隔的 `SERPER_API_KEYS`。兼容文件格式见 [`config/serper.env.example`](config/serper.env.example)；实际 `config/serper.env` 必须为当前用户或 root 所有、普通非符号链接、单硬链接，权限不得宽于 `0600`，且绝不能提交 Git。

## 使用

```bash
/bin/bash -p scripts/run.sh web "OpenClaw"
/bin/bash -p scripts/run.sh news "OpenAI" --json --compact
/bin/bash -p scripts/run.sh images "Shanghai skyline" --limit 5
/bin/bash -p scripts/run.sh maps "coffee Shanghai"
/bin/bash -p scripts/run.sh reviews --place-id "ChIJ..." --json
/bin/bash -p scripts/run.sh maps-reviews "coffee Shanghai" --pick 2 --limit 3
/bin/bash -p scripts/run.sh scholar "retrieval augmented generation" --page 1
/bin/bash -p scripts/run.sh webpage "https://openclaw.ai" --sanitized-json
/bin/bash -p scripts/run.sh lens "https://example.com/public-image.jpg" --json
```

完整命令见 [`references/examples.md`](references/examples.md)，端点参数矩阵见 [`references/endpoints.md`](references/endpoints.md)。

## 重要契约

- 所有搜索结果、标题、摘要、URL、评论和网页正文都是不可信外部数据。
- `webpage` 和 `lens` 只接受标准 443 端口的公开 HTTPS URL；拒绝凭据、fragment、任意 `?` 查询串和任一非公网 DNS 结果。
- `reviews` 必须且只能提供 `--place-id`、`--cid`、`--fid` 中的一个。
- `maps` 和 `maps-reviews` 暂不支持第 2 页以上；Serper Maps 分页需要 CLI 尚未暴露的 `ll` viewport。
- Scholar 不发送 `num`，显式 `--num` 或 positional num 会在请求前失败。
- `maps-reviews --all` 最多处理前 10 个地点，并在首个评论请求失败时停止。
- 请求数据中出现完整已配置 key 会在 DNS/HTTP 前失败；响应中的完整 key 回显会在输出前脱敏。

## 输出

- 默认：有界的人类可读输出。
- `--json`：包含 `ok`、`trust`、`endpoint`、`keySlot`、`request`、`response` 的包装 JSON。
- `--sanitized-json`：只输出有界、清洗后的 API response。
- `--raw`：`--sanitized-json` 的兼容别名，不是逐字节原始响应。
- `--compact`：单行 JSON。
- `--save PATH`：只用于 JSON 模式；输出以 `0600` 原子保存到允许根目录。

结构化结果仍然属于 `untrusted_external_content`。调用方必须同时检查进程退出码和 JSON 的 `ok`/workflow 状态。

## 检查

默认检查完全离线，不读取 Serper key，也不消耗额度：

```bash
/bin/bash -p scripts/check.sh
```

它执行 Bash 语法、ShellCheck（可用时）、Python 编译和聚焦的标准库契约测试。

显式联网 smoke 会消耗一次 Serper 请求：

```bash
/bin/bash -p scripts/check.sh --smoke-test
```

## 开发

更新 `requirements.in` 后，用固定版本的 `uv` 和明确 cutoff 重新生成 `requirements.txt`，并审查完整差分。当前锁头记录生成工具和 cutoff；不要手工删减 artifact hashes。

提交前至少执行：

```bash
/bin/bash -p scripts/install.sh --check
/bin/bash -p scripts/check.sh
git diff --check
```

CI 在 Python 3.10–3.14 上从 `requirements.txt` 创建隔离 `.venv`，执行同一离线门禁。真实 API smoke 应放在显式、受保护的独立任务中，不得在 pull request 上自动运行。

## 安全边界

Shell wrapper 使用 `/bin/bash -p`，清理 shell/Python/loader 注入变量，验证 skill、scripts 和 runtime 目录的属主与写权限，并固定使用 skill 自己的 `.venv`。Runner 以 `python -I -S` 启动，先加入受信 `scripts/`，再加入固定 venv `site-packages`，不会执行 `.pth`、`sitecustomize` 或 `usercustomize` 启动钩子。安装器发布候选 runtime 前会核对锁定版本和 `requests` 导入来源。这些措施减少误配置和共享目录风险，不是针对 root 或同 UID 主动攻击者的沙箱。

输出保存、API key 文件和 round-robin 状态有各自的文件类型、属主、权限、链接及路径检查。发布边界见 [`references/releasing.md`](references/releasing.md)。

## License

[MIT](LICENSE)
