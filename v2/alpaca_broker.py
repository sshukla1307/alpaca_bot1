"""
AI Portfolio Experiment V2 — Alpaca Broker Adapter

Thin wrapper around Alpaca's official Python SDK (alpaca-trade-api), used
exclusively by the live-money path (v2/live_money_runner.py). Never imported
by the paper simulation path — the two are kept structurally separate so a
bug in one cannot reach the other.

Safety notes:
  - Defaults to the PAPER endpoint unless explicitly told otherwise. Real
    money only moves if the caller passes live=True AND has genuine live
    (non-paper) API keys — paper and live accounts use different key pairs.
  - Stop-loss/take-profit on OPENING orders are submitted as native Alpaca
    bracket orders (order_class="bracket"), so the exit is enforced
    server-side by Alpaca continuously, not just when our hourly tick runs.
    This is a stronger guarantee than polling once an hour.
"""

import logging
import math
import os
from typing import Optional

logger = logging.getLogger(__name__)

PAPER_BASE_URL = "https://paper-api.alpaca.markets"
LIVE_BASE_URL = "https://api.alpaca.markets"


class AlpacaBroker:
    def __init__(self, live: bool = False):
        import alpaca_trade_api as tradeapi

        key = os.getenv("ALPACA_LIVE_KEY")
        secret = os.getenv("ALPACA_LIVE_SECRET")
        if not key or not secret:
            raise ValueError(
                "ALPACA_LIVE_KEY / ALPACA_LIVE_SECRET not set. These are intentionally "
                "distinct from ALPACA_KEY/ALPACA_SECRET (which are paper-only in this repo's "
                "V1 history) so a live run can never accidentally reuse paper credentials."
            )

        self.live = live
        base_url = LIVE_BASE_URL if live else PAPER_BASE_URL
        self.api = tradeapi.REST(key, secret, base_url, api_version="v2")
        logger.info(f"AlpacaBroker initialized against {'LIVE' if live else 'PAPER'} endpoint ({base_url})")

    # ─────────────────────────────────────
    # Account / position state
    # ─────────────────────────────────────

    def get_account_state(self) -> dict:
        acct = self.api.get_account()
        return {
            "cash": float(acct.cash),
            "equity": float(acct.equity),
            "buying_power": float(acct.buying_power),
            "daytrade_count": int(acct.daytrade_count),
            "pattern_day_trader": bool(acct.pattern_day_trader),
            "trading_blocked": bool(acct.trading_blocked),
            "account_blocked": bool(acct.account_blocked),
        }

    def get_positions(self) -> dict:
        """Returns {ticker: {shares, direction, entry_price, current_price,
        market_value, unrealized_pnl, unrealized_pnl_pct}}, shaped to match
        the paper simulator's Position so prompt-building code can be reused."""
        positions = {}
        for p in self.api.list_positions():
            direction = "SHORT" if p.side == "short" else "LONG"
            shares = abs(float(p.qty))
            positions[p.symbol] = {
                "shares": shares,
                "direction": direction,
                "entry_price": float(p.avg_entry_price),
                "current_price": float(p.current_price),
                "market_value": float(p.market_value),
                "unrealized_pnl": float(p.unrealized_pl),
                "unrealized_pnl_pct": float(p.unrealized_plpc),
            }
        return positions

    def get_open_stop_levels(self) -> dict:
        """Reconstructs {ticker: {stop_loss_price, take_profit_price}} by reading
        currently-open bracket child orders from Alpaca, rather than maintaining
        our own duplicate ledger that could drift out of sync with reality."""
        levels: dict = {}
        try:
            orders = self.api.list_orders(status="open", nested=True)
        except Exception as e:
            logger.warning(f"Could not fetch open orders for stop-level reconstruction: {e}")
            return levels

        for o in orders:
            legs = getattr(o, "legs", None) or []
            entry = {"stop_loss_price": None, "take_profit_price": None}
            for leg in legs:
                if leg.order_type == "stop" and leg.stop_price:
                    entry["stop_loss_price"] = float(leg.stop_price)
                elif leg.order_type == "limit" and leg.limit_price:
                    entry["take_profit_price"] = float(leg.limit_price)
            if entry["stop_loss_price"] or entry["take_profit_price"]:
                levels[o.symbol] = entry
        return levels

    def get_asset_info(self, ticker: str) -> Optional[dict]:
        try:
            a = self.api.get_asset(ticker)
            return {
                "tradable": bool(a.tradable),
                "shortable": bool(a.shortable),
                "easy_to_borrow": bool(a.easy_to_borrow),
            }
        except Exception as e:
            logger.warning(f"Could not fetch asset info for {ticker}: {e}")
            return None

    def is_market_open_now(self) -> bool:
        """Authoritative real-time cross-check against Alpaca's own market clock,
        in addition to our own NYSE-calendar-based gating."""
        try:
            return bool(self.api.get_clock().is_open)
        except Exception as e:
            logger.warning(f"Could not fetch Alpaca market clock: {e}")
            return False

    # ─────────────────────────────────────
    # Order submission
    # ─────────────────────────────────────

    @staticmethod
    def whole_share_qty(dollar_amount: float, price: float) -> int:
        if price <= 0:
            return 0
        return math.floor(dollar_amount / price)

    def submit_bracket_order(self, ticker: str, side: str, qty: int,
                              stop_loss_price: float, take_profit_price: Optional[float] = None) -> dict:
        """Submit an OPENING order (buy-to-open-long or sell-to-open-short) with a
        server-side stop-loss (and optional take-profit) attached as a bracket.
        Alpaca enforces the exit continuously — not just once per hourly tick."""
        if qty < 1:
            return {"status": "rejected", "reason": f"Computed qty {qty} < 1 share"}

        stop_loss = {"stop_price": round(stop_loss_price, 2)}
        take_profit = {"limit_price": round(take_profit_price, 2)} if take_profit_price else None

        try:
            order = self.api.submit_order(
                symbol=ticker,
                qty=qty,
                side=side,
                type="market",
                time_in_force="gtc",
                order_class="bracket",
                stop_loss=stop_loss,
                take_profit=take_profit,
            )
            return {"status": "submitted", "order_id": order.id, "qty": qty}
        except Exception as e:
            logger.error(f"Bracket order failed for {ticker}: {e}")
            return {"status": "rejected", "reason": str(e)}

    def submit_market_order(self, ticker: str, side: str, qty: int) -> dict:
        """Submit a CLOSING order (sell-to-close-long or buy-to-cover-short)."""
        if qty < 1:
            return {"status": "rejected", "reason": f"Computed qty {qty} < 1 share"}

        try:
            order = self.api.submit_order(
                symbol=ticker,
                qty=qty,
                side=side,
                type="market",
                time_in_force="day",
            )
            return {"status": "submitted", "order_id": order.id, "qty": qty}
        except Exception as e:
            logger.error(f"Market order failed for {ticker}: {e}")
            return {"status": "rejected", "reason": str(e)}
