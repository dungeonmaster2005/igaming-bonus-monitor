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
### Phase 0 — reconnaissance (2026-11-15)
- Probe: two CI runs (43694333222, 43694612714) on a temporary branch, 76 sites, max 7
  pages per site, robots.txt honoured, nothing worked around. Runner egress: US
  (Virginia, AS8075 Microsoft) — so most IE/NL/Ontario sites and many UK brands
  geo-block or block us. Branch deleted after the decision.
- A "not available in your country" notice on a fully served page counts as a
  geo-block (user decision): BetMGM UK, LeoVegas SE, LeoVegas DK are excluded.
- Chosen markets: UK (UKGC) and a separate crypto list (Curaçao).
  UK: Virgin Games, Jackpotjoy, Monopoly Casino, Rainbow Riches (all Gamesys, UKGC
  38905, one shared rules template), Unibet (UKGC 45322), MrQ (60629), NetBet (39170),
  Spin Genie. Midnite: to check (terms only in embedded JSON — reading JSON in the
  HTML without JS is allowed). Lottoland: reserve (unclear which offer is the
  welcome offer).
- Crypto only if the licence matches the CGA register
  (https://www.cga.cw/en/133i348441001, OGL registry PDF dated 2026-11-12):
  7Bit → Kika Technology B.V. OGL/2023/147/0081 "Indefinite"; mBit → Scores55 Tech
  N.V. OGL/2024/1307/0748 "Indefinite"; BitStarz → to match (Dama N.V.
  OGL/2023/174/0082 is a candidate); Cloudbet → Halcyon Super Holdings B.V.
  OGL/2024/328/0599 expired 2026-05-23, "Assessment in progress" → NOT confirmed,
  not used. None of them is in the CGA enforcement register.
- Rejected, with reasons:
  - 403 / Cloudflare challenge: bet365 (UK), William Hill Vegas, Sky Vegas, Paddy
    Power Games, Betfair Casino (PokerStars Casino redirects there), Grosvenor, Mr Vegas,
    Ladbrokes, Coral, BoyleSports (IE), Spilnu (DK), Holland Casino (NL), Stake, Rollbit,
    Bitcasino.io, Sportsbet.io, Betplay.io, FortuneJack, Jackbit, Thunderpick.
  - Geo-block: Casumo UK/SE (→ restricted.casumo.com), BetVictor and talkSPORT BET
    (HTTP 451), HeySpin, Shuffle ("Not available in your region"), BetMGM UK,
    LeoVegas SE, LeoVegas DK (notice on the page).
  - robots.txt disallows: Betfred, Rialto, Hippodrome Online, Mirax, bet365 Ontario.
  - JS-only shell, no terms in HTML: BetMGM Ontario, Betinia (DK), kwiff, PartyCasino,
    Roobet, Vave, BC.Game, Mr Green SE (terms only in JSON, market not chosen).
  - US egress gets a non-UK version ($, MGA licence): 888casino, PlayOJO, Dream Vegas,
    Bally Casino.
  - Other: Betway (404 on /en-gb), Betsson (HTTP 202, empty page).
  - Reachable but market not chosen (too few operators): Unibet SE/DK/NL, Mr Green DK.
  - Crypto reserves not examined in detail: Wild.io, Bets.io, Winz.io, KatsuBet, BetFury
    (redirects to mirror domain bf1.io).
- Ireland, Netherlands, Ontario dropped: no operator usable from GitHub runners.
