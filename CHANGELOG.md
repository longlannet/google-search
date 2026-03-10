# Changelog

本文档用于记录 `openclaw-skill-google-search` 的版本变化。

格式参考 Keep a Changelog，版本号建议遵循 Semantic Versioning（语义化版本）。

## [v0.1.1] - 2026-03-10

### Changed

- 恢复 `SKILL.md` 中的 `homepage` 与 `metadata.openclaw` 字段
- 优化 README、示例说明与许可证文案
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

### Changed

- 将 `client.py` 中的 User-Agent 调整为不绑定具体版本号
- 在 `selfcheck.py` 中明确说明自检固定使用 `us/en` 的原因
- 在 `helptext.py` 中补充 legacy positional 示例说明
- 在 `workflows.py` 中补充 `ok` / `allSucceeded` / `failedCount` 的语义提示
- 略微收紧 API key 文本格式校验

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
