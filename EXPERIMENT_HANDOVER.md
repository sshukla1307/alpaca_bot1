# Live Trading Bot — Handover Dossier

> **DATE**: 2026-08-11
> **STATUS**: LIVE-ONLY. Paper simulation has been removed entirely.
> **CONTEXT**: One autonomous GPT-4o agent (Aggressive persona) trading a real Alpaca account.

## 1. What changed

This project started as a paper-trading benchmark comparing multiple LLM personas
against each other and a random-control baseline (see git history before commit
`785a6d4` for that era). It has since been fully converted to a single live-money
bot:

- **Paper simulation is gone.** `v2/portfolio_simulator.py`, `v2/live_runner.py`,
  `v2/random_agent.py`, `v1/` (legacy V1 system + its historical logs), and the old
  `daily_simulation.yml`/`refresh-data.yml` workflows were deleted outright, not
  just disabled. They're recoverable from git history if ever needed, but nothing
  active depends on them.
- **One persona, one account.** Running two personas against a single real Alpaca
  account was considered and deliberately rejected — Alpaca only tracks one
  position per ticker, so two independent strategies sharing an account risks one
  agent's close order touching the other's shares. The account runs **Aggressive**
  only, sized against 100% of account equity.
- **The dashboard (`index.html`) was rebuilt from scratch**, not edited. The old
  4200-line version was structured entirely around multi-agent comparison
  (per-persona equity charts, holdings modals, etc.) that no longer applies to a
  single real account. The new version is a single-account equity curve, current
  positions, and a recent-activity/rejection log, reading from
  `v2/data/live_money/dashboard/*.json`.

## 2. Regulatory and safety posture

- **Pattern Day Trader guard is mandatory, not optional.** The account is under
  the $25,000 PDT threshold. `v2/pdt_tracker.py` blocks any close that would
  create a same-day round-trip once Alpaca's own `daytrade_count` hits 3 in the
  trailing 5 business days. This cannot be disabled by the agent.
- **Mandatory stop-loss AND take-profit** on every opening trade, submitted as a
  native Alpaca bracket order — Alpaca enforces the exit server-side continuously,
  not just when the hourly tick runs. A tick-time backstop
  (`PROFIT_LOCK_THRESHOLD_PCT = 15.0` in `live_money_runner.py`) force-closes
  anything that somehow ends up unprotected once it's up 15%+.
- **Shorting is disabled system-wide** (`SHORTING_ENABLED = False` in
  `live_money_runner.py`, independent of any other config — this was a deliberate
  design choice to keep the live and paper rule-sets from ever being coupled,
  even though paper no longer exists).
- **Two independent kill switches**, both required for any order to fire:
  `ALPACA_LIVE_TRADING_ENABLED=true` and `ALPACA_LIVE=true`. Both are set only in
  `live_money_trading.yml`.

## 3. Known limitations / things the next person should check

- **This code has never been exercised against Alpaca's real API** — only against
  mocked brokers in local tests (see the test files referenced in commit history;
  they aren't checked into the repo). Run it against `ALPACA_LIVE=false` (paper
  endpoint, separate paper-account keys) before trusting `ALPACA_LIVE=true`.
- **GitHub Pages + private repo**: if Pages is enabled here, verify whether the
  published dashboard is actually access-restricted — on GitHub's free plan, a
  Pages site built from a private repo can still be publicly reachable at its URL
  even though the source repo isn't.
- **PDT tracking only sees orders this system submitted.** Manual trades on the
  same Alpaca account (if any) aren't reflected in `v2/data/live_money/`'s local
  ledger, only in Alpaca's own `daytrade_count`, which the guard also checks as a
  backstop.
- **Bracket order `time_in_force="gtc"`** — chosen so stop-loss/take-profit
  persist across days rather than expiring end-of-day. Worth re-verifying against
  Alpaca's current API docs periodically.

## 4. Key files

- `v2/live_money_runner.py` — the tick orchestrator.
- `v2/alpaca_broker.py` — Alpaca SDK wrapper.
- `v2/pdt_tracker.py` — day-trade guard.
- `v2/agent_runner.py` — prompt-building, LLM tool-calling loop.
- `v2/dashboard_exporter.py` — audit trail → dashboard JSON.
- `.github/workflows/live_money_trading.yml` — the only active automation.

---
*End of Handover.*
