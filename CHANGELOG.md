# Changelog

本文档用于记录 `openclaw-skill-google-search` 的版本变化。

格式参考 Keep a Changelog，版本号建议遵循 Semantic Versioning（语义化版本）。

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
