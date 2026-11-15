# iGaming Bonus Monitor — project rules

Portfolio demo: daily monitoring of online-casino welcome-bonus terms for B2B users
(affiliates, operator marketing teams). Parsed terms go to Google Sheets, changes
trigger Telegram alerts. Runs on GitHub Actions. Infrastructure is adapted from
`../eu-price-monitor` (copied, never modified there).

## Workflow
- Work in phases (0–6). After EVERY phase: stop, show the result and wait for the
  user's "ok". Never start the next phase on your own.

## Stack
- Python 3.11 via uv (CI pins 3.11; no newer syntax).
- requests + BeautifulSoup; gspread; Telegram Bot API over plain HTTPS.

## Secrets
- Only from `.env` locally (python-dotenv) and GitHub Secrets in CI.
- Never commit `.env`, credential JSON files or tokens.

## Scraping rules
- Respect robots.txt; 2–5 s between requests to the same host; honest User-Agent.
- Never work around a block: 403/429, captcha, JS challenge, bot check — log it,
  mark the site stale, propose a replacement.
- Never work around geo-blocking: no proxies, no VPN. A site that is not reachable
  from GitHub-hosted runners is not used.
- Licensed operators only; public pages only, never anything behind a login.

## Content rules
- This is market data, not advertising: no affiliate links, no promo copy, no calls
  to play. README and every sheet header carry: "B2B market data, 18+, not an offer
  to gamble".
- A field that cannot be parsed stays empty — never guess. Always keep the full
  terms text and its hash.

## Git
- Author e-mail: the user's GitHub noreply address (set in this repo's git config).
- Repository stays private; the user decides on visibility.

## Decisions log
- (Phase 0) Candidate list and CI reachability probe: `probe/` on a temporary branch.
