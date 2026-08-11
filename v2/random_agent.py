"""
AI Portfolio Experiment V2 — Random Control Agent

This agent makes completely random trades within the system constraints.
Serves as the ultimate baseline: if an LLM cannot beat this agent, 
its "intelligence" has zero or negative alpha.
"""

import random
import logging
from typing import Dict

from .config import RULES

logger = logging.getLogger(__name__)

# A predefined universe of standard liquid stocks/ETFs for the random agent
UNIVERSE = [
    "SPY", "QQQ", "IWM", "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", 
    "META", "TSLA", "BRK.B", "UNH", "JNJ", "XOM", "JPM", "V", "PG", 
    "MA", "COST", "HD", "ABBV", "MRK", "CVX", "PEP", "KO", "BAC", 
    "PFE", "TMO", "CSCO", "MCD", "DIS", "WMT", "AMD", "NFLX"
]

def execute_random_trades(portfolio_state: Dict, intraday: bool = False) -> Dict:
    """
    Generate random trades adhering to system constraints.
    - Max 15 positions
    - Tries to stay mostly invested over time
    - Random stop losses
    - Randomly goes LONG or SHORT on new positions (coin flip), unless RULES.long_only
      disables shorting, in which case every open is a BUY

    Args:
        intraday: When True, this is being called on an hourly cadence during market
            hours (~7 calls/trading day) rather than once at end of day. Probabilities
            are scaled down so the random baseline's actual trading pace stays roughly
            comparable to the once-a-day version, instead of firing on nearly every tick.
    """
    cash = portfolio_state["cash"]
    positions = portfolio_state["positions"]  # This is a list of dicts

    # Once-a-day probabilities, scaled down per-tick so that, in expectation across
    # ~7 ticks/day, the daily behavior is roughly unchanged. Approximate, not calibrated.
    close_prob = 0.031 if intraday else 0.20
    open_attempt_prob = 0.143 if intraday else 1.0

    trades = []

    # 1. Randomly decide to close existing positions.
    # LONG positions close via SELL, SHORT positions close via COVER.
    for pos in positions:
        ticker = pos["ticker"]
        direction = pos.get("direction", "LONG")
        if random.random() < close_prob:
            trades.append({
                "action": "COVER" if direction == "SHORT" else "SELL",
                "ticker": ticker,
                "allocation_pct": 100,  # Close 100% of the position
                "order_type": "MARKET",
                "stop_loss_pct": 10, # Doesn't matter for a close
                "reason": "Random control close decision."
            })

    # 2. Randomly decide to open new positions IF we have cash and slot space.
    # Each new position is a coin flip between BUY (open LONG) and SHORT (open SHORT).
    # Max slots = 15
    open_slots = 15 - len(positions) + len(trades) # rough estimate including the closes

    portfolio_value = portfolio_state["portfolio_value"]
    if random.random() < open_attempt_prob and cash > 0.1 * portfolio_value and open_slots > 0:
        # Number of new trades (1 to 3, bounded by slots)
        num_opens = min(random.randint(1, 3), open_slots)

        # Get list of existing tickers string
        existing_tickers = [p["ticker"] for p in positions]

        candidates = [t for t in UNIVERSE if t not in existing_tickers]
        random.shuffle(candidates)

        for i in range(num_opens):
            if not candidates:
                break
            ticker = candidates.pop()

            # Random sizing between 5% and 15% of current portfolio value,
            # constrained by current cash (SHORT also requires cash as collateral)
            target_amount = random.uniform(0.05, 0.15) * portfolio_value
            if target_amount > cash:
                target_amount = cash

            qty_pct = (target_amount / portfolio_value) * 100

            # Clamp between 5 and 25
            qty_pct = max(5.0, min(qty_pct, 25.0))

            cash -= target_amount

            # Random stop loss between 3% and 15%
            stop_loss = round(random.uniform(3.0, 15.0), 1)

            # Random take profit between 10% and 50% (or None half the time)
            take_profit = round(random.uniform(10.0, 50.0), 1) if random.random() > 0.5 else None

            # Coin flip between LONG (BUY) and SHORT, unless shorting is disabled system-wide
            action = "SHORT" if (not RULES.long_only and random.random() < 0.5) else "BUY"

            trade = {
                "action": action,
                "ticker": ticker,
                "allocation_pct": round(qty_pct, 2),
                "order_type": "MARKET",
                "stop_loss_pct": stop_loss,
                "reason": f"Randomly selected {'short' if action == 'SHORT' else 'long'} from liquid universe.",
            }
            if take_profit:
                trade["take_profit_pct"] = take_profit

            trades.append(trade)

    logger.info(f"Random Agent generated {len(trades)} trades.")
    
    # Needs to match schema!
    return {
        "daily_notes": "I am a monkey throwing darts at a board.",
        "trades": trades,
        "portfolio_review": []
    }
