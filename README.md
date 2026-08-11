# Live Trading Bot — Alpaca Account

An autonomous GPT-4o agent (Aggressive persona) trading a real Alpaca brokerage account, checking in roughly once an hour during NYSE market hours. No paper simulation, no backtest — every trade executes with real capital, subject to a hard-coded rules firewall and two independent kill switches.

## How it works

Every hour (at :30 past, Mon–Fri, market hours only) `.github/workflows/live_money_trading.yml` runs `python -m v2.live_money_runner`, which:

1. Skips immediately if it's not a real NYSE trading day/hour (DST-correct via `zoneinfo`, cross-checked against Alpaca's own market clock).
2. Builds (or reuses) the day's Market Intelligence Hub snapshot — macro data, sector rotation, a catalyst watchlist — built once per day, not once per tick.
3. Runs a profit-lock backstop: force-closes any position that's up 15%+ with no take-profit order attached, before the agent even gets a turn.
4. Lets the agent decide to trade or hold. It sets its own cadence — it's told explicitly not to trade just because it was asked.
5. Validates and executes any accepted trade immediately at the live price, then re-exports the dashboard.

## "Code is Law" — the rules firewall

`v2/live_money_runner.py` enforces these regardless of what the agent wants:

- **Long-only.** `SHORT`/`COVER` are rejected outright.
- **Sizing**: 5–25% of account equity per position, max 15 open positions.
- **Mandatory stop-loss AND take-profit** on every opening trade — both required, no exceptions. Both get submitted as a native Alpaca bracket order, so exits are enforced server-side continuously, not just once an hour.
- **Profit-lock backstop**: any position that ends up unprotected (no take-profit order) gets force-closed once it's up 15%+.
- **PDT guard (the constraint that actually applies here)**: this is a **margin account, PDT-enabled** (Alpaca's `multiplier` field reads "4") — confirmed directly against the account after an earlier assumption that it was a cash account turned out to be wrong (the very first live tick crashed on a briefly-absent `daytrade_count` field while the account was still provisioning, which looked like a cash-account signature but wasn't). `v2/pdt_tracker.py` blocks any same-day round-trip once `daytrade_count` hits 3 in the trailing 5 business days, since equity is under $25,000.
- **Buying-power guard**: every opening trade is checked against Alpaca's `buying_power` before submission, so a rejection comes with a clear reason instead of relying solely on the broker's own error. A settled-cash/T+1 check also remains in the code as the correct path *if* this were ever a genuine cash account (or account classification is unavailable) — it just isn't the active path for this account.
- **Universe**: US equities and ETFs, including penny stocks (min price $0.01). No crypto, no options, no shorting.

## Two independent safety switches

Both must be explicitly `"true"` for an order to ever be submitted — set only inside `live_money_trading.yml`, never in any other workflow:

- `ALPACA_LIVE_TRADING_ENABLED` — master kill switch. Off by default; the broker isn't even constructed if this isn't set.
- `ALPACA_LIVE` — live vs. paper Alpaca endpoint. Defaults to paper.

## Project structure

- `v2/live_money_runner.py` — the tick orchestrator: validation, execution, PDT guard, profit-lock backstop.
- `v2/alpaca_broker.py` — Alpaca SDK wrapper. Bracket orders for opens, plain market orders for closes.
- `v2/pdt_tracker.py` — Pattern Day Trader day-trade guard.
- `v2/agent_runner.py` — prompt-building and the LLM tool-calling loop (no broker dependency of its own).
- `v2/snapshot_engine.py` — the Market Intelligence Hub: macro, sector rotation, catalyst watchlist.
- `v2/dashboard_exporter.py` — turns the live audit trail into the JSON files `index.html` reads.
- `v2/meta_strategy.py` — one-time Day-0 playbook generation for the agent.
- `v2/mcp_servers/` — tools the agent can call: technicals, fundamentals, macro, news, web search.

## Usage

**One-time setup** — generate the agent's Day-0 investment playbook:
```bash
python -m v2.main --day-0
```

**The trading loop itself** runs automatically via GitHub Actions — there's no manual daily-run command. To re-export the dashboard from the current audit trail without waiting for the next tick:
```bash
python -m v2.main --export
```

**Manual trigger**: Actions tab → "LIVE MONEY Trading Loop (Real Capital)" → Run workflow → type the confirmation phrase.

## Dashboard

`index.html` reads `v2/data/live_money/dashboard/*.json` (equity curve, current positions, recent trade/rejection log) and auto-refreshes hourly. Live at `https://sshukla1307.github.io/alpaca_bot1/` via GitHub Pages. Note: this repo is **public** — real trade/equity history is visible to anyone, by deliberate choice (Pages on the free plan requires a public repo; a private repo would still publish an unauthenticated public URL on paid plans anyway, so there was no truly private option here without self-hosting).

## License
MIT
