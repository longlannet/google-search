# Changelog

本文档用于记录 `openclaw-skill-google-search` 的版本变化。

格式参考 Keep a Changelog，版本号建议遵循 Semantic Versioning（语义化版本）。

## [v2.0.0] - 2026-08-21

### Security

- 将 `SKILL.md` 的所有仓库内路径改为 `{baseDir}` 解析，并统一通过 `scripts/run.sh` 选择已验证的 Python，避免从调用方当前目录执行同名脚本
- 明确把标题、摘要、URL、评论和网页正文视为不可信外部数据；禁止遵循结果内指令，禁止提交私有/内网/预签名/token URL，也禁止从搜索内容生成保存路径或 shell 参数
- 为 query、URL、`num`、`page`、`limit`、`pick` 和 `maps-reviews --all` 增加硬上限，阻止请求数、等待时间和配额被无界放大
- 按 HTTP/网络错误类型限制 key fallback，仅对可能由 key/配额切换恢复的失败尝试下一 key；参数、权限和确定性服务端错误立即失败
- 保存输出和诊断结果时采用受限权限、拒绝符号链接，并避免固定共享 `/tmp` 文件；API key 兼容文件要求可信属主、单硬链接、至多 16 KiB 和 `0600` 或更严格权限
- 修复 round-robin 状态写入在解锁前未可靠落盘的问题；结构化结果改用 1-based 非敏感 `keySlot` / `usedKeySlots`，不再泄露 key 材料
- 在 DNS/HTTP 前拒绝 query、URL 或地点标识中出现任一完整配置 key；Serper 200 JSON 在任何清洗、裁剪、pretty/JSON/raw 输出或保存前递归脱敏完整 key（包括对象 key），脱敏后对象 key 冲突时 fail closed
- API key 兼容文件读取绑定 before/opened/after/named-after 的文件元数据及父目录最终身份，拒绝读取期间的就地修改、路径替换或父目录交换；与固定脱敏标记冲突的 key 格式也会被拒绝
- 安装器默认完全离线且不修改系统；仅显式 `--install-dependencies` / `--install-dev-dependencies` 可在加锁的全新候选 venv 中安装带哈希锁定依赖并原子发布，失败不修改原环境，不再升级浮动 pip、安装裸依赖或调用 `apt-get`
- 三个 shell 入口固定使用 `/bin/bash -p`，清理启动钩子、全部 Bash 可见的合法 `LD_*`、`GLIBC_TUNABLES`、`GCONV_PATH`，并停止向子进程导出 `SHELLOPTS`；这些清理发生在 Bash 获得控制后，不能回溯撤销外层 shell 启动时的 loader 影响
- 在任何 Python 启动前静态验证 3.10–3.14 解释器、stdlib/zip、`pyvenv.cfg` 与 `python*._pth` 布局；stdlib 目录符号链接一律拒绝，安全的普通文件符号链接绑定 link、解析路径和目标的稳定元数据，zip 对象另含内容 SHA-256，兼容 Debian 跨设备 `sitecustomize.py` 布局
- `run.sh --runtime-info` 只返回终端安全的诊断路径和 64 位 source/runtime snapshot token；安装器与检查器不执行展示路径，只能用 token 调用白名单固定 task，并在每次 task 前重新验证完整源码树、祖先链和 runtime 绑定快照；目录 runtime 摘要以元数据为主，不宣称通用内容哈希
- 依赖锁首次可信读取后转入 Linux sealed memfd，候选解释器直接执行隔离 bootstrap pip；候选依赖进程不继承 Serper key 或安装锁 FD，并对 pip 进程组设置超时回收；候选版本探针另有 10 秒上限，超时或非零退出只返回固定错误
- 安装发布在原子交换后保留旧 inode 直至 live/lock/source 复核完成；失败时回滚，状态不明确时保留 recovery tree，避免 cleanup 删除唯一旧环境
- 候选 venv 的事务锁、source snapshot 和 candidate inode 在创建、安装、验证、原子发布与回滚间持续绑定；根目录未知发布候选、同尺寸源码替换、孤立 bytecode、目录 symlink 和 task 参数注入均 fail closed
- 发布配方不再直接启动未经入口验证的 `/usr/bin/python3`；首次执行工作树 runner 前新增独立目录/Git clean gate，验证 checkout 祖先和 tracked 父目录并逐个以 `hash-object --no-filters` 绑定 index/worktree blob，且不调用可执行仓库本地 clean/process filter 的 `git diff-files`；后续完整 blob 校验迁入固定 runner task，并清理 loader/Python 注入环境、稳定读取私有 manifest、逐字节绑定明确 commit 的 tar
- 正式发布 shell 对仓库本地 Git 配置执行精确白名单并在每次 Git 调用前重检，同时固定 hooks、fsmonitor、alternate refs、维护、GPG 和 HTTPS transport；它还拒绝 `commondir`、worktree config、legacy `info/grafts`、object alternates、symlink、hardlink、特殊文件和不安全的 Git 元数据权限，并核对 absolute git/common dir；push 显式禁止 follow-tags、push options、push certificate 与 submodule 递归，避免配置或元数据漂移执行命令、伪造 ancestry、重定向 ref/object 写入或越界上传其他 ref
- commit、tag 与 checksum 的验签统一绑定到远端写入前从可信公钥建立的 fresh `GNUPGHOME`，只接受唯一 primary fingerprint 和 SHA-256 `VALIDSIG`，并拒绝错误、过期或撤销状态；清理会有界停止该 home 的私有 `gpg-agent` 并删除其精确 hashed socket 目录，不影响默认 keyring/agent

### Changed

- OpenClaw metadata 现在限定 Linux，并完整声明日常入口所需的 `bash`、`python3`、GNU 文件工具、`dirname`、`printf`、`sha256sum`、`sort` 与 `SERPER_API_KEY`；`SERPER_API_KEY` 同时声明为 `primaryEnv`，文件多 key 仅保留为直接 CLI 兼容
- 支持线明确为 Python 3.10–3.14；运行时依赖与开发依赖分别由 `requirements.txt` 和 `requirements-dev.txt` 的版本/哈希锁维护
- 默认 `install.sh` 与 `check.sh` 不联系 Serper；真实 `--smoke-test` 和 `--full-check` 必须显式选择，后者确实运行 full selfcheck
- `maps-reviews --all` 只处理前 `--num` 个地点（1–10）并报告截断计数，遇到首个评论失败即停止并返回整体失败；`reviews` 要求恰好一个地点标识
- `README.md` 和 `references/` 已按实际安装、runtime 选择、端点、输出、退出码和配额语义重写

### Added

- 新增 `scripts/run.sh`，透明转发搜索参数，并通过只读 `--runtime-info` 记录和 64 位 snapshot token 将内部调用绑定到固定 task；不再向调用方返回可直接执行的解释器接口
- 新增 `install.sh --install-dev-dependencies`，为完整离线 `check.sh` 安装独立的哈希锁定测试工具链
- 新增安全文件输出公共实现及针对参数边界、HTTP fallback、payload、workflow、保存路径、installer、wrapper、selfcheck 与退出码的回归测试
- 新增 Dependabot 配置，只跟踪可保持完整 SHA pin 的 GitHub Actions 更新；Python 双锁必须继续使用固定 uv/cutoff 配方受控重建
- 文档新增可执行发布 runbook：未来 release 需在任何远端写入前完成 commit-bound 归档与真实安装 canary，并具备 signed commit/tag、SHA-256 签名校验和、绑定作者/标题/正文的 immutable draft、精确资产回验、GitHub release attestation 与匿名公网字节验证；历史缺失证据不能回溯补成已验证发布
- 固定并记录 `uv 0.11.6` 与依赖解析 cutoff，新增有界归档卫生策略：拒绝已列明的敏感路径、非占位 Serper 赋值与已识别私钥头，以及 runtime、输出、虚拟环境、安装锁和缓存；它不充当通用 secret scanner
- runtime 选择现会核对完整五包版本和关键 API；搜索 pretty/JSON/raw 输出与保存统一执行整次 16 MiB 上限

### Fixed

- `webpage` 改用官方 `https://scrape.serper.dev` 主机；maps/reviews/autocomplete/webpage/Lens 会拒绝显式不支持的字段，reviews 当前明确为不带 `nextPageToken` 的首批查询
- `maps-reviews` 的异常现在统一经过结构化错误输出并返回非零；批量部分失败不再以 `ok=true` 或进程 0 假成功
- selfcheck 严格拒绝未知参数，检查 workflow 的 `allSucceeded`/`failedCount`，并让完整检查覆盖所有声明的 endpoint/workflow 分组
- 安装、运行与检查使用一致的 runtime 选择规则；离线检查不再强制要求 `.venv`、key 或联网自检
- selfcheck 现在要求各 endpoint 的非空结构证据、webpage 非空文本，并逐项验证 `maps-reviews --all` 的评论列表形态；离线测试子进程不再继承 Serper key
- quiet 在线 smoke/full 在结果协议失败或信号退出时同样清理私有临时结果，不再只覆盖 worker 失败和成功路径
- 安装器在 online worker 与依赖安装 helper 的 fork/PID 登记窗口内记录并定向处理 HUP/INT/TERM，退出前等待原 Bash job，避免信号竞态留下继续联网或修改候选 venv 的孤儿进程
- tag 阶段在推送前重检 main，并将 main 与 tag 放入同一 atomic push；runbook 明确这不是跨 ref compare-and-swap，并要求从第二次 tag/release precheck 到 immutable 终验完成的整个 publication window 排除其他 publisher/admin。等待 tag CI 后还会在 immutable 设置、draft 上传和正式发布前重复核对 publisher/admin、干净工作树、main/tag 与两次精确 commit CI
- CI 将每个矩阵 Python 建成本地 `.venv` 后只运行完整 `check.sh --venv` 门禁，避免 PATH 净化后误用 Ubuntu 系统 Python
- 发布归档门禁可把实际 tar 逐字节绑定到明确的 40 位 commit，并直接检查该资产而不是只检查工作树重建物
- Lens 完整自检改用稳定的公开图片，避免站点 favicon 合法返回空视觉结果造成误报，并绑定协议校验中的探针参数

### CI

- GitHub Actions 固定 `ubuntu-24.04`、最小只读权限、job timeout、关闭 checkout credential 持久化，并将 checkout/setup-python 固定到已注明版本的完整提交 SHA
- CI 覆盖 Python 3.10–3.14，使用 `--require-hashes --only-binary=:all:` 安装开发锁，执行完整 pytest、全文件 AST、`/bin/bash -p -n`、ShellCheck 与离线 `check.sh`
- CI job 与正式候选配方保留明确的 90 分钟上限，使实测接近 49 分钟且仍在增长的完整对抗套件、逐 task runtime 重验和环境准备不会被原 10/30 分钟边界误杀

## [v1.3.0] - 2026-04-10

### Changed

- 将安装脚本调整为优先复用当前 `python3`，仅在运行时依赖不满足时才回退到本地 `.venv`
- 将安装阶段默认检查收敛为轻量 `smoke_test.py`，不再把重型 full selfcheck 作为安装成功的默认硬门槛
- 收敛并对齐 `SKILL.md`、`README.md`、`references/examples.md`、`scripts/helptext.py` 等文档与帮助入口，使其与当前 CLI 行为一致

### Added

- 新增 `scripts/smoke_test.py`，用于最小可用链路验证
- 为 `scripts/install.sh` 新增 `--system`、`--venv`、`--json`、`--save-json <file>`、`--quiet` 等自动化友好能力
- 为 `scripts/install.sh` 新增分类退出码与结构化 JSON 结果字段（含 result path / exit kind / exit code）
- 为 `scripts/selfcheck.py` 新增 `--basic`、`--group network|parsing|workflows`、`--save <file>`、`--fail-fast`、`--quiet` / `--no-stdout`
- 为 `scripts/selfcheck.py` 新增分类退出码与结果中的 `failureKinds` / `exitCode` 等字段
- 新增 `references/automation.md`，集中说明 CI / 自动化接入方式

### Fixed

- 修复安装脚本在 JSON 输出路径下的残留输出与帮助细节问题
- 收平 examples / README / helptext 中遗留的旧版 `.venv` / selfcheck 用法，减少文档误导

## [v1.2.0] - 2026-03-10

### Changed

- 收紧 `args.py` 中 legacy positional 识别逻辑，避免将不完整或不典型输入误判为旧版搜索形式
- 改进 CLI 参数错误输出，保留更具体的 argparse 报错信息并继续附带帮助文本
- 隐藏 pretty 输出中的 API key suffix，使默认终端输出更偏向正式用户界面而非调试信息
- 为 `client.py` 中的 round-robin key 轮转 fallback 增加更明确的注释与可选调试输出（`SERPER_DEBUG_RR=1`）
- 收敛 `helptext.py`、`README.md` 与 `references/examples.md` 中的示例分工，减少重复命令示例
- 调整 `selfcheck.py` 内部命名与结果记录逻辑，使代码语义更清晰、结果聚合更稳

### Added

- 为 `tests/test_args.py` 补充更严格的 legacy 识别、`cid`/`fid` 参数解析与额外边界路径测试
- 为 `tests/test_workflows.py` 补充 `cid`/`fid` only 场景、`organic` reviews 渲染路径与 RR debug 开关测试
- 在 GitHub Actions 工作流中增加关键文件存在检查与 `py_compile` 语法检查

### Fixed

- 修复/改善 `renderers_pretty.py` 中部分空值与空白字符串的 pretty 输出细节
- 改进 `search.py` 中错误输出逻辑的复用方式，统一 `json` / `raw` / pretty 模式下的错误发射行为
- 改善 workflow pretty 输出对 `organic` reviews 形态的兼容性与输出一致性
- 清理 `.pytest_cache` 并补充 `.gitignore` 忽略规则，改善仓库发布卫生

## [v0.1.3] - 2026-03-10

### Changed

- 将 `search.py` 与 `selfcheck.py` 改为直接依赖真实模块，减少对 `utils.py` 兼容层的依赖
- 将 `utils.py` 收敛为兼容导出层，并在文件头明确其定位
- 为 `workflows.py` 的批量失败条目增加 `errorType` / `errorMessage` 结构化字段
- 清理 `renderers_pretty.py` 中部分 `or ''` / falsy 数值语义问题，并补充轻量 helper 降低重复逻辑
- 在 README 中新增“结构冻结说明”，明确后续优先做功能、文档和增量测试，不再继续进行大规模结构重构

### Added

- 新增轻量 workflow 测试 `tests/test_workflows.py`

## [v0.1.2] - 2026-03-10

### Changed

- 将 `client.py` 中的 User-Agent 调整为不绑定具体版本号
- 在 `selfcheck.py` 中明确说明自检固定使用 `us/en` 的原因
- 在 `helptext.py` 中补充 legacy positional 示例说明
- 在 `workflows.py` 中补充 `ok` / `allSucceeded` / `failedCount` 的语义提示
- 略微收紧 API key 文本格式校验
- 将渲染相关脚本进一步重构为 `io_common.py`、`renderers_pretty.py`、`renderers_json.py`、`response_shapes.py` 四层结构
- 将 `renderers.py` 调整为兼容聚合层，保留旧导入路径的同时收紧内部职责边界
- 统一一部分 CLI 输出文案为中文表述，例如“无标题”“图片识别结果”“配额消耗”等

### Added

- 新增轻量参数测试 `tests/test_args.py`
- 新增最小 GitHub Actions 工作流 `.github/workflows/test.yml`
- 新增 `scripts/io_common.py`
- 新增 `scripts/renderers_pretty.py`
- 新增 `scripts/renderers_json.py`
- 新增 `scripts/response_shapes.py`

### Fixed

- 修复 `client.py` 中 API key 轮转索引持续增长的问题，改为按 key 数量回绕
- 改进 200 响应但非 JSON 返回时的错误处理
- 统一 `client.py` 对非 200 HTTP 响应的错误摘要行为
- 增强 `selfcheck.py` 的参数错误路径验证
- 为 `maps-reviews --all` 增加 `allSucceeded` 与 `failedCount` 状态字段
- 在 `references/examples.md` 中补充 `{baseDir}` 含义说明
- 恢复 `args.py` 默认区域设置为 `gl=cn`、`hl=zh-cn`
- 修复 `search.py` 中 raw 错误输出未处理 `--save` 的行为不一致问题

## [v0.1.1] - 2026-03-10

### Changed

- 恢复 `SKILL.md` 中的 `homepage` 与 `metadata.openclaw` 字段
- 优化 README、示例说明与许可证文案

## [v0.1.0] - 2026-03-10

### Added

- 首个公开版本发布
- 支持基于 Serper.dev 的 Google 搜索 skill
- 支持 `web` / `search`、`images`、`news`、`videos`、`shopping`、`scholar`、`patents`
- 支持 `places`、`maps`、`reviews`、`autocomplete`、`webpage`、`lens`
- 提供 `maps-reviews` 工作流，用于从地图搜索结果继续拉取评论
- 提供 pretty / json / raw / compact 等输出模式
- 提供 `scripts/selfcheck.py` 自检脚本
- 提供中文 README、安装说明和使用示例
- 提供 `references/endpoints.md` 与 `references/examples.md` 参考文档

### Notes

- 当前默认 Python 依赖较轻，主要为 `requests`
- 本仓库默认忽略 `config/serper.env`、`runtime/`、`venv/` 和 Python 缓存文件
