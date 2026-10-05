# OpenClaw Google Search Skill

通过 [Serper.dev](https://serper.dev) 为 OpenClaw 提供实时 Google 搜索、图片、新闻、视频、地点、地图、评论、购物、Scholar、专利、网页提取和 Lens 查询。

正式入口是 `scripts/run.sh`。`scripts/` 下的 Python 模块属于内部实现，不承诺独立调用兼容性。

## 运行要求

- Linux
- Bash
- Python 3.10–3.14
- 可信的 `/usr/bin/python3`，用于在执行 skill venv 前检查源码和运行环境
- `flock`、GNU `stat`、`id`、`dirname`
- Serper API key

运行依赖由 `requirements.txt` 以版本和 artifact SHA-256 锁定。

## 安装

从仓库根目录执行：

```bash
/bin/bash -p scripts/install.sh
```

默认安装会联网访问 PyPI，在私有候选目录中创建 `.venv`，使用 `--require-hashes --only-binary=:all:` 安装锁定依赖，验证后再发布。安装器使用持久排他锁，两个安装进程不会同时修改 `.venv`；运行进程持共享锁，不会跨越 runtime 发布窗口。

只检查现有 runtime 和 `0600` 安装锁，不创建文件、修改权限或生成 bytecode；缺失或不安全时失败：

```bash
/bin/bash -p scripts/install.sh --check
```

安装提交前收到已处理的终止信号会回滚；新环境验证并提交后，旧备份清理失败或中断也不会删除新环境，遗留备份会报告路径。安装和发布不承诺抵御同 UID 主动篡改，也不宣称断电/内核崩溃级事务原子性。

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
- 原生端点的 `--json` 成功响应：包含 `ok`、`trust`、`endpoint`、`keySlot`、`request`、`response` 的包装 JSON。
- 原生端点的 `--sanitized-json`：只输出有界、清洗后的 API response，不额外生成 `ok`。
- `--raw`：`--sanitized-json` 的兼容别名，不是逐字节原始响应。
- `--compact`：单行 JSON。
- `--save PATH`：只用于 JSON 模式；输出以 `0600` 原子保存到允许根目录。

`maps-reviews` 保留独立 workflow 格式。`--json` 返回 `ok/trust/query/maps/usedKeySlots`，单地点结果包含 `pick/selectedPlace/reviews`，`--all` 包含 `results/allSucceeded/failedCount/attemptedCount/skippedCount`，不采用原生端点 wrapper。sanitized/raw 模式也返回聚合对象的精简版，不是单次 API 响应。

参数、配置或前置请求失败时，`--json` 返回 `{ok:false,trust,endpoint,error}`，sanitized/raw 返回 `{ok:false,error}`；已开始的 workflow 也可能以部分结果与计数报告失败。调用方先检查进程退出码，再检查该格式适用的失败字段，不能要求失败对象包含成功字段。完整契约见 `SKILL.md` 和 `references/endpoints.md`。所有外部响应仍是不可信数据。

## 检查

默认检查完全离线，不读取 Serper key，也不消耗额度：

```bash
/bin/bash -p scripts/check.sh
```

它逐个执行 Bash 语法检查、ShellCheck（可用时）、无 bytecode 写入的 Python 编译，并发现全部离线回归测试。安装测试在临时目录使用真实 venv 与本地依赖 stub，验证锁、信号、发布和回滚，不调用真实 pip 下载或修改正在使用的 runtime。跨 UID 回归仅在 root 且具备 `setpriv` 时运行，其他权限用例也可由普通用户运行。

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

CI 在 Python 3.10–3.14 上从 `requirements.txt` 创建隔离 `.venv`，执行同一离线门禁。一次性 runner 先仅对所选 toolcache Python 安装树及其必要父目录去除组/全局写权限，并创建 `0600` 安装锁；检查期间持有共享锁。真实 API smoke 应放在显式、受保护的独立任务中，不得在 pull request 上自动运行。

## 安全边界

Shell wrapper 使用 `/bin/bash -p` 并清理注入变量；由可信系统 Python 在执行 venv 前核验源码、缓存 bytecode、依赖文件、解释器目标与父目录的属主和写权限，拒绝其他 UID 拥有、组/全局可写、特殊文件和非预期符号链接。运行环境采用安装器生成的标准 Linux symlink venv，解释器目标须与 `pyvenv.cfg` 的 home 一致，禁止 `._pth` 重定向。主机 Python 及标准库属于受信基础。

Runner 以 `python -I -S` 启动并禁写 bytecode，先加入受信 `scripts/`，再加入固定 venv `site-packages`，不会执行 `.pth`、`sitecustomize` 或 `usercustomize` 启动钩子。安装器发布候选 runtime 前会核对锁定版本和 `requests` 导入来源。这不是针对 root 或同 UID 主动攻击者的沙箱。

输出保存、API key 文件和 round-robin 状态有各自的文件类型、属主、权限、链接及路径检查。发布边界见 [`references/releasing.md`](references/releasing.md)。

## License

[MIT](LICENSE)

## 搜索偏好与研究经验

搜索优先级、研究口径及保留的专题参考见 [SKILL.md 的 Notes](SKILL.md#notes-search-preference-and-research-practice)。旧的 Python 直调、smoke/selfcheck 脚本和旧 installer 参数已被安全入口与新离线测试替换；不复用未经检查的旧 `.venv`。
