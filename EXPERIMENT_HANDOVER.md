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

- **This account is MARGIN, PDT-enabled (multiplier=4) — took two rounds to
  pin down correctly, worth understanding why.** The very first live tick
  crashed: `AttributeError: 'Account' object has no attribute 'daytrade_count'`.
  That looked exactly like the signature of a cash account (whose API response
  omits margin-only fields entirely), so the account was assumed to be cash and
  a settled-cash/T+1-settlement guard was built around that assumption. The
  *next* successful run then logged `multiplier='4'` — Alpaca's own code for a
  PDT-enabled margin account — which is inconsistent with a cash account. The
  account owner confirmed directly in the Alpaca dashboard: it's margin. Best
  explanation: the account was still provisioning when the very first tick ran,
  which is why `daytrade_count` was briefly absent (not because it's cash, but
  because Alpaca hadn't finished attaching margin fields to a brand-new
  account yet). Lesson: a single missing-field crash isn't sufficient evidence
  to classify account type — `alpaca_broker.get_account_state()` now reads
  `multiplier` directly and treats it as the source of truth, with defensive
  `getattr(...)` defaults so a transient absence never crashes a tick again
  regardless of which account type it turns out to be.
- **PDT is the guard that actually matters here**, not settlement.
  `v2/pdt_tracker.py` blocks any close that would create a same-day round-trip
  once Alpaca's own `daytrade_count` hits 3 in the trailing 5 business days,
  since equity is under $25,000. This is real, active protection now — not a
  no-op like it was briefly assumed to be.
- **`_validate_live_trade` branches on `account["is_cash_account"]`**: `False`
  (this account) checks proposed trade size against `buying_power`; anything
  else (a genuine cash account, or if `multiplier` is ever unreadable) falls
  back to the settled-cash/T+1 check instead. Both paths are tested; only the
  margin path is what actually runs against this account.
- **Mandatory stop-loss AND take-profit** on every opening trade, submitted as a
  native Alpaca bracket order — Alpaca enforces the exit server-side continuously,
  not just when the hourly tick runs. A tick-time backstop
  (`PROFIT_LOCK_THRESHOLD_PCT = 15.0` in `live_money_runner.py`) force-closes
  anything that somehow ends up unprotected once it's up 15%+.
- **Shorting is disabled system-wide** (`SHORTING_ENABLED = False` in
  `live_money_runner.py`) — a deliberate choice, independent of account type.
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
- **This code has now placed real trades and it works.** A manually-triggered
  run bought NVDA and FSLY, both via the fast_info-failed → daily-close-fallback
  path, both with stop-loss/take-profit brackets attached and Alpaca order IDs
  confirmed. Not just mocked-broker tests anymore.
- **PDT tracking only sees orders this system submitted.** Manual trades on the
  same account (if any) aren't reflected in the local "opened today" ledger,
  only in Alpaca's own `daytrade_count`, which the guard also checks directly
  as the primary source of truth.
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
