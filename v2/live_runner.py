"""
AI Portfolio Experiment V2 — Live Intraday Runner

Invoked repeatedly (roughly once an hour, at :30 past) by GitHub Actions while
the NYSE is open. Each invocation is a single "tick":
  1. Skip immediately if it's not a real NYSE trading day/hour (DST-aware).
  2. Build (or reuse) today's Market Intelligence Hub snapshot — built once
     per day, not once per tick, since macro/sector data doesn't need
     re-fetching every tick.
  3. Let each agent check stops/targets against live prices and decide
     whether to trade or hold, filling any accepted trade immediately.
  4. Re-export the dashboard so it reflects the latest tick.
"""

import logging
import random
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
MARKET_OPEN_MIN = 9 * 60 + 30   # 9:30 AM ET
MARKET_CLOSE_MIN = 16 * 60      # 4:00 PM ET


def run_live_tick():
    """Run a single live-market tick for all agents. Safe to call repeatedly;
    no-ops outside real NYSE trading hours."""
    from .snapshot_engine import is_market_day, build_mih_snapshot
    from .daily_snapshot import load_snapshot
    from .config import AGENTS, STARTING_CAPITAL, RULES, PORTFOLIOS_DIR, SNAPSHOTS_DIR
    from .portfolio_simulator import PortfolioSimulator
    from .agent_runner import run_agent_live_tick
    from .dashboard_exporter import export_for_dashboard

    now_et = datetime.now(ET)
    today = now_et.date()
    minutes = now_et.hour * 60 + now_et.minute

    if now_et.weekday() >= 5:
        logger.info(f"{today} ({now_et.strftime('%A')}) is a weekend. Skipping tick.")
        return
    if not is_market_day(today):
        logger.info(f"{today} is a NYSE holiday. Skipping tick.")
        return
    if not (MARKET_OPEN_MIN <= minutes < MARKET_CLOSE_MIN):
        logger.info(f"{now_et.strftime('%H:%M %Z')} is outside market hours (9:30-16:00 ET). Skipping tick.")
        return

    logger.info(f"--- LIVE TICK: {now_et.isoformat()} ---")

    # Build today's MIH snapshot once; reuse it for every tick that day.
    snapshot = load_snapshot(today, SNAPSHOTS_DIR)
    if not snapshot:
        logger.info(f"No MIH snapshot cached for {today} yet — building it now.")
        snapshot = build_mih_snapshot(today, save_dir=SNAPSHOTS_DIR)

    agent_ids = list(AGENTS.keys())
    for i, agent_id in enumerate(agent_ids):
        if i > 0:
            delay = random.uniform(3, 8)
            logger.info(f"Roster jitter: waiting {delay:.1f}s before {agent_id}...")
            time.sleep(delay)

        simulator = PortfolioSimulator(agent_id, STARTING_CAPITAL, RULES, PORTFOLIOS_DIR)
        try:
            run_agent_live_tick(agent_id, snapshot, simulator, now_et)
        except Exception as e:
            logger.error(f"Live tick failed for {agent_id}: {e}")

    data_dir = Path(__file__).parent / "data"
    out_dir = Path(__file__).parent.parent / "v1" / "logs"
    export_for_dashboard(data_dir, out_dir)

    logger.info("--- LIVE TICK COMPLETE ---")


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
    run_live_tick()
