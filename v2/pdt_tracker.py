"""
AI Portfolio Experiment V2 — Pattern Day Trader (PDT) Guard

FINRA's PDT rule: a MARGIN account under $25,000 equity that executes more
than 3 "day trades" (opening and closing the same security on the same
calendar day) within a rolling 5-business-day window gets flagged and
restricted by the broker. This is a regulatory constraint, not a strategy
preference — it is enforced here unconditionally whenever equity is under
$25,000, regardless of which persona or ruleset is driving the account.

Note: PDT applies to margin accounts only. The account this bot actually
trades is a CASH account, where `daytrade_count` is absent from Alpaca's API
entirely — alpaca_broker.get_account_state() defaults it to 0, which makes
pdt_blocks_close() below effectively a permanent no-op for this account. It's
kept in place (harmless, and correct if the account type ever changes) but
the constraint that actually matters for a cash account is T+1 settlement —
see the settled_cash guard in live_money_runner._validate_live_trade instead.

Alpaca's own account object exposes an authoritative `daytrade_count`
(rolling 5-business-day count) and `pattern_day_trader` flag — this module
uses that as the source of truth for "how many day trades have happened,"
and only needs its own tiny local ledger to answer one question Alpaca's
API doesn't directly expose per-request: "did *our* system open this
specific ticker's current position today?" (needed to know whether closing
it right now would create a NEW day trade at all).

Scope note: this only sees orders this system itself submitted. If the same
Alpaca account has positions opened by some other means (manual trades,
another bot), this ledger won't know about them.
"""

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

PDT_EQUITY_THRESHOLD = 25_000.00
PDT_MAX_DAY_TRADES = 3


def _ledger_path(data_dir: Path) -> Path:
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "position_opened_dates.json"


def load_opened_dates(data_dir: Path) -> dict:
    path = _ledger_path(data_dir)
    if not path.exists():
        return {}
    with open(path) as f:
        return json.load(f)


def save_opened_dates(data_dir: Path, data: dict):
    with open(_ledger_path(data_dir), "w") as f:
        json.dump(data, f, indent=2)


def mark_opened(data_dir: Path, ticker: str, today: str):
    """Call when an order transitions a ticker from flat to non-flat."""
    data = load_opened_dates(data_dir)
    data[ticker] = today
    save_opened_dates(data_dir, data)


def mark_closed(data_dir: Path, ticker: str):
    """Call when an order fully closes a ticker's position."""
    data = load_opened_dates(data_dir)
    data.pop(ticker, None)
    save_opened_dates(data_dir, data)


def would_be_day_trade(data_dir: Path, ticker: str, today: str) -> bool:
    """True if closing `ticker` right now would count as a same-day round trip."""
    return load_opened_dates(data_dir).get(ticker) == today


def pdt_blocks_close(account_state: dict, data_dir: Path, ticker: str, today: str) -> Optional[str]:
    """Returns a rejection reason string if PDT rules should block closing this
    position right now, or None if the close is allowed."""
    if account_state["equity"] >= PDT_EQUITY_THRESHOLD:
        return None  # PDT day-trade cap doesn't apply above the equity threshold

    if not would_be_day_trade(data_dir, ticker, today):
        return None  # position wasn't opened today by this system; not a day trade

    if account_state["daytrade_count"] >= PDT_MAX_DAY_TRADES:
        return (
            f"PDT guard: closing {ticker} today would be day trade #{account_state['daytrade_count'] + 1} "
            f"in the trailing 5 business days, and equity (${account_state['equity']:.0f}) is under the "
            f"${PDT_EQUITY_THRESHOLD:.0f} threshold. Blocking to avoid a Pattern Day Trader restriction — "
            f"this position must be held until a later trading day."
        )
    return None
