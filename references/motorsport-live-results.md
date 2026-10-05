# Motorsport live results and standings research notes

Use for F1/MotoGP live-result, broadcast, calendar, and championship-standings questions.

## F1
- Official result pages on `formula1.com/en/results/<year>/races/<race_id>/<slug>/race-result` often contain a parseable HTML `<table>` even when the visible site is JS-heavy.
- Fetch the HTML and search for driver abbreviations or `<table>`; strip tags to recover columns such as Pos., No., Driver, Team, Laps, Time/Retired, Pts.
- For schedule questions, convert local event times to the user's explicitly requested timezone (for example, 北京时间) and label date rollovers.

## MotoGP
- The official MotoGP site is JS-heavy. The rendered page may expose API URLs in browser performance resources even when the accessibility snapshot says `LOADING`.
- Official 2026 standings API pattern observed:
  - `https://api.pulselive.motogp.com/motogp/v2/results/world-standings?type=rider&season=<season_uuid>&category=<category_uuid>`
  - Also supports `type=constructor` and `type=team`.
- Official category UUIDs observed for 2026:
  - MotoGP: `e8c110ad-64aa-4e8e-8a86-f2f152f6a942`
  - Moto2: `549640b8-fd9c-4245-acfd-60e4bc38b25c`
  - Moto3: `954f7e65-2ef2-4423-b949-4961cc603e45`
- Official 2026 season UUID observed: `e88b4e43-2209-47aa-8e83-0e0b1cedde6e`.
- If hardcoding season/category UUIDs, first confirm they still match the official page's resource calls for the requested year.
- The official calendar page may only show ticket/promotional cards in the static HTML and can omit some events in that widget. For full season counts, cross-check the official calendar article/list; the 2026 official article listed 22 rounds and gave the complete order.
- For full MotoGP race classification, official news confirms the podium/story, while third-party pages such as BikeSport News/Crash may expose full classification in JSON-LD `articleBody`. Cross-check top results with official news before presenting.

## Presentation
- For Chinese motorsport questions, answer in Chinese, translate rider/team/country context where useful, but keep original rider names in parentheses on first mention.
- Avoid unsupported certainty about TV availability; distinguish TV channel, web live page, and app-specific streams.
