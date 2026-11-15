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

### Phase 1 — data model, first parser (2026-11-15)
- Pages for fixtures are fetched by a GitHub runner (`fetch-pages` workflow,
  `tools/fetch_pages.py`), so parsers are built on the HTML CI actually gets.
- Offer = one row per reward; alternative rewards share an `offer_id`.
  `wagering_applies_to` (bonus / winnings / deposit+bonus) replaces the original
  `wagering_base` (it is a superset). Durations: `claim_days` = time to qualify/claim,
  `use_days` = time to use spins/bonus once credited, `expiry_days` = time to finish
  wagering. Metrics: `reward_per_unit` (UK), `required_turnover` (crypto; empty when
  wagering applies to winnings), `over_10x` (UK, multiplier > 10).
- Parsing policy: every candidate value is collected with its sentence; a field is set
  only if all candidates agree. Contradictions → empty + "conflict" note; ambiguous
  phrasing → empty + "ambiguous" note. Example: Unibet UK gives 48 h, 7 days and 30 days
  for using the spins, and writes the spin value as "0.10p".
- In a free-spins offer, "bonus funds must be wagered 10x" refers to the spin winnings:
  such sentences give the multiplier only; the base comes from sentences that name it.
- Midnite: the casino page's embedded JSON has no offer terms (the phase 0 hit was the
  meta description). Full terms are in the public help-centre article
  help.midnite.com/en/articles/9018497-uk-casino-welcome-offer (fetched from CI) → in.
- Crypto licences matched to the CGA OGL register of 2026-11-12: BitStarz (Gareton
  B.V., 160656, OGL/2024/165/0185 — exact match), 7Bit (Kika Technology B.V.,
  OGL/2023/147/0081; site prints reg. no. 16498, register 164980), mBit (site "Scores55
  Tech B.V.", register "N.V.", same reg. no. 162990 and licence OGL/2024/1307/0748).
  Cloudbet not confirmed (licence expired, "Assessment in progress") → not used.

### Phase 2 — all operators (2026-11-15)
- A field may come from any official page of the same operator; each filled field keeps
  `source_url` + verbatim quote (`Offer.evidence`). If sources disagree or one is
  ambiguous: the unambiguous value is used and the discrepancy goes to `issues`; if the
  unambiguous sources disagree, the field stays empty (conflict).
- `issues`: operator, offer_id, field, type (conflict / ambiguous / typo), quotes with
  source_url, date, neutral `detail`. Facts and quotes only.
- `stated_none`: the terms explicitly say a limit/code does not exist ("no cap on
  winnings", "No code is needed") — different from "not stated". `field_notes`: why a
  field is empty when it is not an issue (conditional max bet, min deposit in EUR while
  the bonus is in BTC, two spin-activation windows).
- Rows: alternatives (Monopoly free spins / free bets, Spin Genie A/B/C) and crypto
  deposit steps ("Deposit N of M") share an `offer_id`.
- Match bonuses: `qualifying_amount` = deposit that unlocks the maximum bonus
  (max_bonus × 100 / bonus_percent), so `reward_per_unit` compares like with like.
- Wagering "0" = the terms say no wagering. Free-spin winnings wagering on crypto
  packages is not stored in the bonus row (the row's multiplier is the bonus wagering).
- Crypto reserves vs CGA register: KatsuBet (Kika Technology B.V., 164980,
  OGL/2023/147/0081 — exact match) → added. Wild.io (Anjouan ALSI-202504044) and Winz.io
  (Anjouan ALSI-202508056) are not CGA licensees; Bets.io shows no licence text in the
  HTML → not used.
- Table cells are not phrase boundaries (a row reads as one phrase); a "Question?" cell
  is joined with a short "No./Yes." answer cell.
- Fixtures are shrunk (scripts/styles/SVG removed, `tools/shrink_fixture.py`); parsers
  read visible text only.
- Gotcha: writing regexes through a bash heredoc turned `\b` into a backspace character
  three times — edit regex code with the Edit tool.

### Phase 3 — Google Sheets (2026-11-15)
- New separate spreadsheet (id in `.env` / GitHub Secret `SHEET_ID`), locale en_GB, UTC. Tabs:
  offers_uk, offers_crypto, issues, changes, terms, status, history. Row 1 on every tab:
  "B2B market data. 18+. Not an offer to gamble. Collected from a US IP on <date>; terms may
  differ by location." Row 2: what the tab shows; row 3 header; data from row 4.
- User corrections: `product` (casino / sports; Monopoly choice B = sports) — ranking and
  comparison only within one product. Crypto main metric `turnover_x_deposit` = bonus % / 100 ×
  wagering (with deposit+bonus wagering: (1 + bonus %/100) × wagering); `required_turnover` at the
  maximum bonus stays as a reference column. Issue severity: conflict high, ambiguous medium, typo low.
- offers_uk: product, then reward_per_unit desc; on equal value no-wagering first, then known
  wagering, then unknown. Competition ranks ("1, 2, 2, 4") within the product. Wagering and its
  base right after the metric; over_10x is a checkbox.
- offers_crypto: turnover_x_deposit asc; days to wager and max bet next to it.
- Empty cells: "none" (terms say there is no limit), "n/a" (does not apply), "—" (not stated),
  "conflict" (link to the issue's first row). Every parsed value links to its source page, the
  cell note holds the verbatim quote; a value that has an issue against it is highlighted.
- Links are only the operators' own pages, no tracking parameters.
- State between runs (committed by CI): data/site_status.json, data/offers_latest.json (last known
  offers with evidence, issues with first/last seen, terms hash + last changed), data/changes.json.
  First observation of an operator is the baseline (no change rows). A STALE operator (page
  failed/blocked/layout changed) logs no changes; its last known rows are shown flagged.
  Pages are never parsed partially: one failed page makes the operator stale for the day.
- history keeps one snapshot per day (same-day rerun replaces it), newest first.
- First fill: from the pages a GitHub runner fetched on 2026-11-15 (tests/fixtures), via
  `python -m src.main --pages-dir tests/fixtures --date 2026-11-15`. 7Bit's legal page is not
  fetched daily any more (`license_page`, not used by the parser).

### Phase 4 — Telegram alerts (2026-11-15)
- Licences verified in the UKGC register (2026-11-15): Spin Genie → Skill On Net Limited, account
  39326 (licence active, www.spingenie.com listed; the site's FAQ links to this entry); Midnite →
  Midnite Limited, account 101839 (active from 2026-08-11, www.midnite.com listed; number from the
  midnite.com footer). Turnover formula for deposit+bonus wagering: (1 + bonus %/100) × wagering (kept).
- Alert groups, thresholds and limits in config/alerts.yaml: numeric_change, terms_change (short
  diff, up to 3 passages, nearby edits merged, "+N more"), offer_appeared, offer_disappeared,
  issue_opened, issue_closed, over_10x (UK, when it starts), offer_ending (≤ 7 days, once per row and
  end date). 5 lines per group, then "…and N more". Caption "B2B market data. 18+. Not an offer to gamble."
- No message on the first run (baseline), for stale operators, or when nothing changed. An
  operator's first observation is its own baseline (no changes, no issue alerts).
- State is saved after the sheet and Telegram: if either fails, the next run diffs against the
  same baseline again (no lost alert).
- Terms normalisation (src/terms_text.py) — only rules seen on monitored pages. Evidence: CI fetch
  43732834320 (~13:00 UTC) vs the morning fetches; whitespace-only comparison showed BitStarz
  changing within 4 hours, all others identical. Cut before hashing: lower case; "last updated
  [on] <date>" stamps (Virgin Games, Jackpotjoy, BitStarz); copyright year (mBit "© 2026", Monopoly
  "© 1935"); exchange-rate equivalent lists of 3+ amounts in different coins/currencies → digits as
  # (BitStarz min deposit/max bet "1,000 NOK, 0.488 BCH, 2.231 LTC…", mBit FS max win list);
  invisible characters (Midnite ​); typographic quotes/dashes; unicode spaces. NOT cut:
  dates, times ("until 23:59" on Monopoly is a rule) and amounts inside the rules.
- BitStarz terms text narrowed to the welcome-package blocks + bonus terms page: the rest of its
  pages is a live lobby ("Biggest Wins (24 hours)" ticker, rotating game carousels, an A/B headline).
- Hashes are recomputed from stored text with the current rules on every diff, so changing a rule
  never reads as a terms edit. Baseline in data/ rebuilt from the 2026-11-15 fixtures after this.
- Test: tools/alert_demo.py (artificial yesterday in a throw-away dir; Unibet 10x→20x and NetBet
  clauses edited in the HTML, Spin Genie choice C removed); the sent test message is headed as a test.
