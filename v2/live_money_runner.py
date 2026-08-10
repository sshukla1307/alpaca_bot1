"""
AI Portfolio Experiment V2 — Live Money Runner

Drives ONE real-money Alpaca account with the Aggressive persona, on the
same hourly cadence as the paper live_runner.py, but as a fully isolated
path: separate broker module, separate audit trail, separate GitHub Actions
workflow (live_money_trading.yml). A bug in the paper pipeline cannot reach
this path, and vice versa — they share only read-only pieces (the MIH
snapshot cache, the agent decision-making code in agent_runner.py).

Two independent switches must BOTH be explicitly set for real money to move:
  - ALPACA_LIVE_TRADING_ENABLED=true   (master kill switch: do anything at all)
  - ALPACA_LIVE=true                   (paper vs live Alpaca endpoint; defaults to paper)
Neither is set by daily_simulation.yml (the paper workflow), so the paper
pipeline can never accidentally reach a real account.

Regulatory note: this account is under the $25,000 Pattern Day Trader
threshold, so v2.pdt_tracker actively blocks any same-day round-trip once
Alpaca's own daytrade_count hits 3 in the trailing 5 business days.
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
MARKET_OPEN_MIN = 9 * 60 + 30
MARKET_CLOSE_MIN = 16 * 60

LIVE_AGENT_ID = "chatgpt_live_aggressive"
LIVE_DATA_DIR = Path(__file__).parent / "data" / "live_money"
ORDER_LOG_PATH = LIVE_DATA_DIR / "order_log.jsonl"

MIN_ALLOCATION_PCT = 5.0
MAX_ALLOCATION_PCT = 25.0
MAX_POSITIONS = 15


def _log_order_event(event: dict):
    LIVE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    event = dict(event)
    event["logged_at"] = datetime.now(ET).isoformat()
    with open(ORDER_LOG_PATH, "a") as f:
        f.write(json.dumps(event, default=str) + "\n")
    logger.warning(f"[LIVE-MONEY] {event}")


def _build_portfolio_state(account: dict, positions: dict, stop_levels: dict) -> dict:
    """Shape live Alpaca account/positions into the same dict shape
    PortfolioSimulator.get_portfolio_summary() produces, so agent_runner's
    prompt-building code can be reused unmodified."""
    positions_summary = []
    for ticker, pos in positions.items():
        levels = stop_levels.get(ticker, {})
        positions_summary.append({
            "ticker": ticker,
            "direction": pos["direction"],
            "shares": round(pos["shares"], 4),
            "entry_price": pos["entry_price"],
            "current_price": pos["current_price"],
            "pnl_pct": f"{pos['unrealized_pnl_pct']:+.2%}",
            "market_value": round(pos["market_value"], 2),
            "stop_loss": levels.get("stop_loss_price"),
            "take_profit": levels.get("take_profit_price"),
        })

    return {
        "agent_id": LIVE_AGENT_ID,
        "cash": round(account["cash"], 2),
        "portfolio_value": round(account["equity"], 2),
        "num_positions": len(positions),
        "max_positions": MAX_POSITIONS,
        "positions": positions_summary,
        "pending_orders": 0,
        "note": (
            "THIS IS A REAL ALPACA BROKERAGE ACCOUNT. Every trade you propose executes "
            "with real capital immediately — there is no paper simulation and no human "
            "reviewing your orders before they fill."
        ),
    }


def _validate_live_trade(trade: dict, account: dict, positions: dict, broker, today: str):
    """Mirrors the paper simulator's 'Code is Law' checks, adapted for a real
    Alpaca account (position sizing, direction consistency, mandatory stop-loss,
    PDT day-trade guard, and Alpaca tradability/shortability). Returns (ok, reason)."""
    from .pdt_tracker import pdt_blocks_close

    action = trade.get("action", "").upper()
    ticker = trade.get("ticker", "").upper()
    allocation_pct = trade.get("allocation_pct", 0)
    stop_loss_pct = trade.get("stop_loss_pct")

    if action not in ("BUY", "SELL", "SHORT", "COVER"):
        return False, f"Invalid action: {action}"
    if not ticker or len(ticker) > 5:
        return False, f"Invalid ticker: {ticker}"

    opening = action in ("BUY", "SHORT")
    existing = positions.get(ticker)

    if opening:
        if allocation_pct < MIN_ALLOCATION_PCT or allocation_pct > MAX_ALLOCATION_PCT:
            return False, f"Allocation {allocation_pct}% outside [{MIN_ALLOCATION_PCT}, {MAX_ALLOCATION_PCT}]%"
        if not existing and len(positions) >= MAX_POSITIONS:
            return False, f"Max positions ({MAX_POSITIONS}) reached"
        if not stop_loss_pct:
            return False, f"Stop-loss is mandatory for all {action} orders"

    if action == "BUY" and existing and existing["direction"] == "SHORT":
        return False, f"Cannot BUY {ticker}: position is SHORT (use COVER first)"
    if action == "SHORT" and existing and existing["direction"] == "LONG":
        return False, f"Cannot SHORT {ticker}: position is LONG (use SELL first)"
    if action == "SELL" and (not existing or existing["direction"] != "LONG"):
        return False, f"Cannot SELL {ticker}: no LONG position held"
    if action == "COVER" and (not existing or existing["direction"] != "SHORT"):
        return False, f"Cannot COVER {ticker}: no SHORT position held"

    if action in ("SELL", "COVER"):
        pdt_reason = pdt_blocks_close(account, LIVE_DATA_DIR, ticker, today)
        if pdt_reason:
            return False, pdt_reason

    if opening:
        asset = broker.get_asset_info(ticker)
        if not asset or not asset["tradable"]:
            return False, f"{ticker} is not tradable on Alpaca"
        if action == "SHORT" and not asset["shortable"]:
            return False, f"{ticker} is not shortable on Alpaca"

    return True, "OK"


def run_live_money_tick():
    enabled = os.getenv("ALPACA_LIVE_TRADING_ENABLED", "").lower() == "true"
    if not enabled:
        logger.info("[LIVE-MONEY] ALPACA_LIVE_TRADING_ENABLED is not 'true'. Doing nothing.")
        return

    from .config import Persona, PLAYBOOKS_DIR, PROVIDERS, SNAPSHOTS_DIR
    from .snapshot_engine import is_market_day, build_mih_snapshot
    from .daily_snapshot import load_snapshot
    from .agent_runner import get_agent_trades, fetch_live_prices
    from .alpaca_broker import AlpacaBroker
    from .pdt_tracker import load_opened_dates, save_opened_dates, mark_opened, mark_closed

    live = os.getenv("ALPACA_LIVE", "").lower() == "true"
    logger.warning(f"[LIVE-MONEY] Tick starting against {'LIVE (REAL MONEY)' if live else 'PAPER'} Alpaca endpoint.")

    now_et = datetime.now(ET)
    today = now_et.date().isoformat()
    minutes = now_et.hour * 60 + now_et.minute

    if now_et.weekday() >= 5:
        logger.info(f"[LIVE-MONEY] {today} is a weekend. Skipping.")
        return
    if not is_market_day(now_et.date()):
        logger.info(f"[LIVE-MONEY] {today} is a NYSE holiday. Skipping.")
        return
    if not (MARKET_OPEN_MIN <= minutes < MARKET_CLOSE_MIN):
        logger.info(f"[LIVE-MONEY] {now_et.strftime('%H:%M')} ET is outside market hours. Skipping.")
        return

    broker = AlpacaBroker(live=live)

    if not broker.is_market_open_now():
        logger.warning("[LIVE-MONEY] Alpaca's own market clock says the market is closed. Skipping as an extra safety check.")
        return

    account = broker.get_account_state()
    if account["trading_blocked"] or account["account_blocked"]:
        logger.error(f"[LIVE-MONEY] Trading is blocked on this account by Alpaca. Skipping. State: {account}")
        return

    positions = broker.get_positions()
    stop_levels = broker.get_open_stop_levels()

    # Reconcile our local "opened today" ledger against reality (e.g. drop
    # tickers a bracket stop already closed since the last tick).
    opened_dates = load_opened_dates(LIVE_DATA_DIR)
    for ticker in list(opened_dates.keys()):
        if ticker not in positions:
            opened_dates.pop(ticker)
    save_opened_dates(LIVE_DATA_DIR, opened_dates)

    # Reuse the same daily MIH snapshot the paper pipeline builds/caches.
    snapshot = load_snapshot(now_et.date(), SNAPSHOTS_DIR)
    if not snapshot:
        snapshot = build_mih_snapshot(now_et.date(), save_dir=SNAPSHOTS_DIR)

    portfolio_state = _build_portfolio_state(account, positions, stop_levels)

    playbook_path = PLAYBOOKS_DIR / "chatgpt_aggressive.md"
    playbook = (
        playbook_path.read_text(encoding="utf-8")
        if playbook_path.exists()
        else "Default strategy: Maximize risk-adjusted returns."
    )

    class _LiveMoneyAgent:
        id = LIVE_AGENT_ID
        display_name = "ChatGPT (Aggressive) — REAL MONEY"
        persona = Persona.AGGRESSIVE
        provider = PROVIDERS["chatgpt"]

    agent = _LiveMoneyAgent()

    trades = get_agent_trades(agent, LIVE_AGENT_ID, snapshot, portfolio_state,
                               intraday=True, now_et=now_et.strftime("%Y-%m-%d %H:%M %Z"))

    if not trades:
        logger.info("[LIVE-MONEY] Agent proposed no trades this tick (HOLD).")
        return

    # Fetch live prices for every ticker referenced, to size whole-share orders.
    tickers = {t.get("ticker", "").upper() for t in trades}
    prices = fetch_live_prices(tickers)

    for trade in trades:
        ticker = trade.get("ticker", "").upper()
        action = trade.get("action", "").upper()

        ok, reason = _validate_live_trade(trade, account, positions, broker, today)
        if not ok:
            _log_order_event({"date": today, "action": action, "ticker": ticker, "status": "REJECTED", "reason": reason})
            continue

        price = prices.get(ticker)
        if not price:
            _log_order_event({"date": today, "action": action, "ticker": ticker, "status": "REJECTED",
                               "reason": "Could not fetch a live price"})
            continue

        if action in ("BUY", "SHORT"):
            dollar_amount = account["equity"] * (trade["allocation_pct"] / 100)
            qty = broker.whole_share_qty(dollar_amount, price)
            side = "buy" if action == "BUY" else "sell"
            stop_loss_price = price * (1 - trade["stop_loss_pct"] / 100) if action == "BUY" else price * (1 + trade["stop_loss_pct"] / 100)
            take_profit_pct = trade.get("take_profit_pct")
            take_profit_price = None
            if take_profit_pct:
                take_profit_price = price * (1 + take_profit_pct / 100) if action == "BUY" else price * (1 - take_profit_pct / 100)

            result = broker.submit_bracket_order(ticker, side, qty, stop_loss_price, take_profit_price)
            _log_order_event({"date": today, "action": action, "ticker": ticker, "qty": qty,
                               "price": price, "stop_loss_price": stop_loss_price,
                               "take_profit_price": take_profit_price, "reason": trade.get("reason", ""), **result})
            if result["status"] == "submitted":
                mark_opened(LIVE_DATA_DIR, ticker, today)

        else:  # SELL or COVER
            pos = positions[ticker]
            close_pct = min(trade.get("allocation_pct", 100) / 100, 1.0)
            qty = broker.whole_share_qty(pos["shares"] * price * close_pct, price)
            qty = min(qty, int(pos["shares"]))
            side = "sell" if action == "SELL" else "buy"

            result = broker.submit_market_order(ticker, side, qty)
            _log_order_event({"date": today, "action": action, "ticker": ticker, "qty": qty,
                               "price": price, "reason": trade.get("reason", ""), **result})
            if result["status"] == "submitted" and qty >= int(pos["shares"]):
                mark_closed(LIVE_DATA_DIR, ticker)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).parent.parent / ".env")
    except ImportError:
        pass
    run_live_money_tick()
