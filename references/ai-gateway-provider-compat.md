# AI gateway / provider compatibility lookup notes

Use this when searching whether an AI gateway (sub2api, one-api variants, OpenClaw/Hermes provider configs, router projects) can connect to a model provider or subscription plan.

## Search sequence
1. Search targeted combinations: `<gateway> <provider> <plan/product>`, plus protocol terms (`Anthropic API`, `OpenAI Compatible`, `base_url`, `chat/completions`, `responses`).
2. Prefer official provider docs for Base URL/API protocol. If docs are JS-heavy, fetch the page HTML and extract embedded route data/Markdown/JSON rather than relying only on visible text extraction.
3. Cross-check the gateway repo: README, open issues, and source code for supported platforms/account types, custom `base_url`, and protocol fallbacks.
4. Distinguish: native first-class integration vs. compatibility-mode integration through an existing platform (Anthropic/OpenAI/Gemini).
5. Report concrete config fields and hazards (wrong endpoint, special API key, model mapping, forced endpoint mode), not just “supported”.

## sub2api × 火山方舟 Coding Plan pattern
Evidence found in session:
- sub2api issue `Wei-Shaw/sub2api#774` asks for 阿里云/火山云 Coding Plan account distribution; still open. Comment says a Tencent Coding Plan worked by using the Claude/Anthropic platform and notes similar Coding Plans generally expose Anthropic API-style access.
- Fire/Volcengine official docs for Coding Plan state it is compatible with Anthropic and OpenAI interface protocols.
- Coding Plan Base URLs:
  - Anthropic-compatible: `https://ark.cn-beijing.volces.com/api/coding`
  - OpenAI-compatible: `https://ark.cn-beijing.volces.com/api/coding/v3`
- Official warning: do **not** use `https://ark.cn-beijing.volces.com/api/v3` for Coding Plan; it is ordinary API billing and may not consume Coding Plan quota.
- Typical models include `ark-code-latest`, `doubao-seed-2.0-code`, `doubao-seed-code`, `deepseek-v4-pro`, `kimi-k2.6`, `glm-5.1`, `minimax-m3`.

## How to answer for sub2api
- Say: “can connect via compatibility mode, not necessarily native built-in Volcengine platform.”
- Claude Code route: add an Anthropic API Key account in sub2api, set Base URL to `/api/coding`, use Fire/Volcengine API Key, map client Claude model names to `ark-code-latest` or a supported Coding Plan model if needed.
- OpenAI route: add an OpenAI API Key account, set Base URL to `/api/coding/v3`, use Fire/Volcengine API Key, and prefer forcing Chat Completions when the gateway offers a Responses API mode switch (`force_chat_completions`) because many OpenAI-compatible providers only expose `/chat/completions`.
- If testing fails, inspect the actual upstream URL being called: `/api/coding/v3/chat/completions` is expected for OpenAI-compatible chat completions; `/responses` may indicate the wrong mode.
