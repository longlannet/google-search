# Changelog

本文档用于记录 `openclaw-skill-google-search` 的版本变化。

格式参考 Keep a Changelog，版本号建议遵循 Semantic Versioning（语义化版本）。

## [v0.1.1] - 2026-03-10

### Changed

- 回滚 `SKILL.md` frontmatter 收紧调整，恢复 `homepage` 与 `metadata.openclaw` 字段
- 保留 `client.py` 中 API key 轮转索引回绕修复
- 保留非 JSON 响应的更明确错误处理
- 保留 `selfcheck.py` 的异常路径验证增强
- 保留 `maps-reviews --all` 的 `allSucceeded` / `failedCount` 状态字段
- 在 `references/examples.md` 中补充 `{baseDir}` 含义说明
- README 删除部分冗余内容，并明确 MIT License 说明

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
