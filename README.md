# iGaming Bonus Monitor

> **B2B market data, 18+, not an offer to gamble.**

A daily monitor of online-casino welcome-offer terms for B2B users (affiliates, operator
marketing teams). It reads the public offer and terms pages of licensed UK (UKGC) and
Curaçao-licensed (CGA) crypto casinos, parses the terms into comparable fields, writes them
to Google Sheets and sends neutral Telegram alerts when terms change. Runs on GitHub Actions.

*Full documentation (operators, metrics, limitations, screenshots) follows in the next update.*

## Schedule

The workflow `.github/workflows/daily.yml` is scheduled at **07:17 UTC** (GitHub cron uses UTC;
the job avoids `:00`, where scheduled runs queue longest). **In practice GitHub starts scheduled
jobs on this account 7–9 hours late**, so a day's data usually lands in the afternoon (UTC).
If the run has to happen at a fixed time, trigger it from an external scheduler through the
workflow's `workflow_dispatch` event.

Each run:
1. reads every enabled operator from a GitHub-hosted runner (US IP; operators may show
   different terms elsewhere) — robots.txt respected, nothing behind a login, blocks and
   geo-blocks are never worked around;
2. updates the Google Sheet (offers, issues, changes, terms, status, history);
3. sends a Telegram message only if something changed;
4. commits the state the next run compares against (`data/`), as `github-actions[bot]`.

A failed run sends a short failure notice to Telegram and writes no data.
