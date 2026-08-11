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

- **This account is CASH, not margin — confirmed via a production incident,
  not assumption.** The very first live tick crashed: `AttributeError: 'Account'
  object has no attribute 'daytrade_count'`. `alpaca_trade_api`'s Account entity
  raises on missing fields rather than returning `None`, and `daytrade_count`/
  `pattern_day_trader` are margin-account-only fields in Alpaca's API — a cash
  account's response omits them entirely. Fixed in `v2/alpaca_broker.py` via
  defensive `getattr(...)` with fail-closed defaults.
- **PDT does not apply to this account and is effectively a no-op.**
  `v2/pdt_tracker.py` still exists (harmless — `daytrade_count` always defaults
  to 0 now, so it never blocks anything) but is not the guard doing real work.
- **The guard that actually matters: settled-cash sizing.** Cash accounts settle
  T+1 — a same-day sale's proceeds aren't usable for a new position until the
  next business day, or it risks a "good faith violation." Every opening trade in
  `_validate_live_trade` is sized strictly against `account["settled_cash"]`
  (Alpaca's `non_marginable_buying_power`, via `alpaca_broker.get_account_state()`),
  never total cash/equity — structurally impossible to violate, not just checked.
  If `settled_cash` is ever absent from the API response, it fails closed to 0
  (blocks trading) rather than assuming full cash is available.
- **Mandatory stop-loss AND take-profit** on every opening trade, submitted as a
  native Alpaca bracket order — Alpaca enforces the exit server-side continuously,
  not just when the hourly tick runs. A tick-time backstop
  (`PROFIT_LOCK_THRESHOLD_PCT = 15.0` in `live_money_runner.py`) force-closes
  anything that somehow ends up unprotected once it's up 15%+.
- **Shorting is disabled system-wide** (`SHORTING_ENABLED = False` in
  `live_money_runner.py`) — also consistent with a cash account, which can't
  short at all regardless.
- **Two independent kill switches**, both required for any order to fire:
  `ALPACA_LIVE_TRADING_ENABLED=true` and `ALPACA_LIVE=true`. Both are set only in
  `live_money_trading.yml`.

## 3. Known limitations / things the next person should check

- **The repo is public, deliberately.** GitHub Pages requires it (the free plan
  won't serve Pages from a private repo, and even the paid tier that does still
  publishes an unauthenticated public URL — there was no actually-private Pages
  option without self-hosting). Dashboard is live at
  `https://sshukla1307.github.io/alpaca_bot1/`, and the full trade/equity history
  is public record via the committed `v2/data/live_money/*.jsonl` files, not just
  the dashboard view of them.
- **This code had exactly one confirmed live run before the cash-account fix**,
  which crashed. The settled-cash guard added afterward has only been tested
  against mocked brokers, not a real Alpaca response — watch the next few ticks'
  logs closely rather than assuming it's now fully correct.
- **PDT tracking only sees orders this system submitted** (moot for a cash
  account, but would matter again if this account ever converts to margin).
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
