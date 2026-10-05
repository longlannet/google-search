# Financial product limits research pattern

Use this when a user asks for limits on fintech wallets, cards, KYC tiers, withdrawals, deposits, or account verification levels.

## Workflow

1. Search broad first, then official-source variants:
   - `<product> <limit type> limit`
   - `site:<official-help-domain> <product> <limit type>`
   - exact-title searches from snippets, e.g. `"What are the spending and withdrawal limits" "<product>"`.
2. Prefer official support/terms/blog pages over SEO summaries. If official pages disagree, label them by date and product surface.
3. Separate limit dimensions instead of merging them:
   - account/KYC/verification level limits
   - card spending limits
   - card funding/load limits
   - deposits/withdrawals/transfers
   - rolling daily/weekly/monthly/yearly caps
   - count caps (e.g. transactions per day) vs amount caps
4. Explicitly answer whether the requested limit is actually tied to the requested tier. If the page lists only a general card limit and does not say `KYC2`, say so.
5. Watch for old vs new product surfaces. A legacy Freshdesk page can coexist with a newer product-specific help center. Present the newer/current surface first and mark older pages as legacy/older evidence.

## Paga USD / KYC example from session

Official Paga sources found two different surfaces:

- Paga NGN wallet KYC pages list KYC tiers:
  - KYC 2 daily transaction limit: `₦200,000/day`
  - KYC 2 balance limit: `₦500,000`
- Paga USD Account/Card help center lists USD card/card-account limits, but does **not** publish a KYC2-specific USD card spending table:
  - Virtual USD Card online transactions: `$5,000/day`
  - USD Card merchant/POS transactions: `$5,000/day` (10 times), `$35,000/week` (35 times), `$35,000/month` (450 times), `$420,000/year` (5400 times)
  - ATM cash withdrawals: `$500/day` (5 times), `$3,500/week`, `$15,000/month`, `$180,000/year`
- USD Account pages say daily transaction limits depend on account verification level and fully verified accounts have higher limits, but do not enumerate `KYC1/KYC2/KYC3` USD card limits publicly.

Answer pattern for this case: "Public docs do not show a dedicated KYC2 USD Card spending limit; the public card limit is $5,000/day, while KYC2 NGN wallet limits may affect account funding/primary wallet transactions. If the app shows a lower limit, it is likely account-specific verification/risk configuration not exposed in the public help center."