# App Store regional IAP pricing lookups

Use this when comparing Claude/ChatGPT/other app subscription prices across Apple App Store regions.

## Reliable source order
1. **Official Apple regional App Store pages** for verification of specific regions:
   - `https://apps.apple.com/{cc}/app/{slug}/id{app_id}?l=en`
   - Example: `https://apps.apple.com/jp/app/claude-by-anthropic/id6473753684?l=en`
   - Fetch with a browser-like User-Agent and `Accept-Language`; some direct curl calls can return empty while Python `urllib.request` with headers succeeds.
2. **Search snippets** for quick discovery, especially exact app name + region + plan.
3. **AppStorePrice-style aggregators** for broad ranking, but verify top candidates on official Apple pages.

## Parsing official Apple pages
Apple pages often expose in-app purchases in simple text pairs:

```python
import urllib.request, re, html
cc = "jp"
url = f"https://apps.apple.com/{cc}/app/claude-by-anthropic/id6473753684?l=en"
req = urllib.request.Request(url, headers={
    "User-Agent": "Mozilla/5.0",
    "Accept-Language": "en-US,en;q=0.9",
})
s = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore")
for m in re.finditer(r"<span>(Claude[^<]+)</span>\s*<span>([^<]+)</span>", s):
    print(html.unescape(m.group(1)), "=>", html.unescape(m.group(2)).replace("\xa0", " "))
```

For Claude as of the observed session:
- Japan official page: `Claude Max 5x - Monthly => ¥20,000`
- Indonesia official page: `Claude Max 5x - Monthly => Rp 1,999juta` (means Rp 1,999,000)
- Turkey official page: `Claude Max 5x - Monthly => ₺6.999,99`

## Parsing broad aggregator pages
Some Next.js aggregator pages embed escaped JSON rather than clean HTML. Search the raw HTML for an escaped key such as `\"subscriptions\":[{`, decode with `unicode_escape`, then JSON-parse the balanced array. Use this only to rank candidates, not as the final authority.

## Reporting checklist
- State the query timestamp/timezone.
- Show original local price and approximate CNY/USD conversion.
- Separate **Apple App Store IAP price** from **vendor web official price**; Apple IAP often includes regional/channel markup.
- Warn that exchange rates are reference/non-trading-grade and card/Apple balance fees may differ.
- Do not rely on old forum posts after a recent price change; prefer current Apple pages/snippets.