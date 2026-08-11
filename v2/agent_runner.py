"""
AI Portfolio Experiment V2 — Agent Runner

The main execution engine.
Iterates over all defined agents, injects the daily snapshot and their meta-strategy playbook,
provides the MCP tools, and runs the LLM loop until it proposes trades.
Validates the trades with the PortfolioSimulator and commits them for T+1 execution.
"""

import json
import logging
import random
import time
from pathlib import Path

from .config import AGENTS, RULES, PERSONA_MODIFIERS
from .mcp_servers import get_tool_definitions
from .api_adapters import UnifiedLLMClient
from .portfolio_simulator import PortfolioSimulator

logger = logging.getLogger(__name__)

# Force propose_trades as a tool that MUST be called to end the turn.
def get_trade_tool() -> dict:
    # Load schema
    schema_path = Path(__file__).parent / "schemas" / "propose_trades.json"
    with open(schema_path) as f:
        schema = json.load(f)
    
    if "$schema" in schema:
        del schema["$schema"]
        
    return {
        "name": "propose_trades",
        "description": "Submit your final daily trading decisions. You MUST call this tool when you are done analyzing.",
        "parameters": schema
    }

def build_system_prompt(agent_config, playbook: str, intraday: bool = False) -> str:
    """Build the robust system prompt combining rules, persona, and the Day 0 playbook."""
    prompt = f"YOU ARE {agent_config.display_name}.\n\n"

    prompt += "=== SYSTEM RULES ===\n"
    for rule_name, rule_val in RULES.__dict__.items():
        prompt += f"- {rule_name}: {rule_val}\n"

    prompt += "\n=== YOUR PERSONA ===\n"
    prompt += f"{PERSONA_MODIFIERS[agent_config.persona]}\n"

    prompt += "\n=== YOUR INVESTMENT PLAYBOOK (META-STRATEGY) ===\n"
    prompt += "You defined this strategy on Day 0. You MUST adhere to it.\n"
    prompt += f"{playbook}\n\n"

    if intraday:
        prompt += "\n=== LIVE INTRADAY MODE ===\n"
        prompt += (
            "This is a LIVE check-in that repeats roughly once an hour (at :30 past) while the market is open "
            "(9:30am-4:00pm ET). This overrides the 'execution_model: T+1_OPEN' and "
            "'stop_trigger_model: OHLC' rules above for THIS call only:\n"
            "- Any trade you submit now fills IMMEDIATELY at the current live price, not at tomorrow's open.\n"
            "- Your stop-loss/take-profit are checked against the live price at every check-in, not just daily highs/lows.\n"
            "- You decide your own cadence: trade once this check-in, multiple times today, or not at all. "
            "If you have no new information or edge since your last check-in, return an EMPTY trades list (HOLD) "
            "— you will be asked again in about an hour. Do not trade just because you were asked.\n"
        )

    prompt += "\n=== INSTRUCTIONS ===\n"
    prompt += "1. Analyze the daily snapshot and use tools to investigate further if needed.\n"
    action_options = "buy, sell, or hold" if RULES.long_only else "buy, sell, short, cover, or hold"
    prompt += f"2. Determine if you want to {action_options}.\n"
    prompt += "3. You MUST end your turn by calling the `propose_trades` tool.\n"

    return prompt

def build_user_prompt(snapshot: dict, portfolio_state: dict, intraday: bool = False, now_et: str = "") -> str:
    prompt = f"Market Date: {snapshot['metadata']['date']}\n"
    if intraday and now_et:
        prompt += f"Current Time (ET): {now_et}\n"
    prompt += "\n"

    # Inject the high-density Market Intelligence Hub (MIH) payload
    prompt += "=== MARKET INTELLIGENCE HUB (MIH) ===\n"
    prompt += f"{snapshot.get('mih_prompt_payload', 'No MIH data available.')}\n\n"

    prompt += "=== YOUR CURRENT PORTFOLIO STATE ===\n"
    prompt += json.dumps(portfolio_state, indent=2) + "\n\n"

    if intraday:
        prompt += "This is a live intraday check-in. Trade now only if you see a genuine reason to; otherwise call propose_trades with an empty trades list to HOLD."
    else:
        prompt += "What are your trading decisions for tomorrow (T+1)? Based on the RRG/Watchlist above, use your tools to confirm then call propose_trades."
    return prompt


def get_agent_trades(agent, agent_id: str, snapshot: dict, portfolio_state: dict,
                      intraday: bool = False, now_et: str = "") -> list[dict]:
    """Get this tick's trade decisions from an agent (LLM or random control).
    Shared by the once-daily T+1 path, the paper live-tick path, and the live-money
    path — portfolio_state is a plain dict (see PortfolioSimulator.get_portfolio_summary()
    for its shape), so this function has no dependency on the paper simulator backend."""
    if agent.id == "random_control":
        from .random_agent import execute_random_trades
        result_json = json.dumps(execute_random_trades(portfolio_state, intraday=intraday))
    else:
        # Load agent's Day 0 playbook
        from .config import PLAYBOOKS_DIR
        playbook_path = PLAYBOOKS_DIR / f"{agent_id}.md"
        if not playbook_path.exists():
            playbook = "Default strategy: Maximize risk-adjusted returns."
        else:
            with open(playbook_path, encoding="utf-8") as f:
                playbook = f.read()

        # Get tools
        tools = get_tool_definitions()

        for t in tools:
            if "parameters" in t and "$schema" in t["parameters"]:
                del t["parameters"]["$schema"]

        tools.append(get_trade_tool())

        # Build prompts
        sys_prompt = build_system_prompt(agent, playbook, intraday=intraday)
        user_prompt = build_user_prompt(snapshot, portfolio_state, intraday=intraday, now_et=now_et)

        logger.info(f"Starting LLM execution for {agent_id} ({agent.provider.name})...")

        client = UnifiedLLMClient(agent.provider)
        try:
            result_json = client.generate(sys_prompt, user_prompt, tools, max_tool_calls=15)
        except Exception as e:
            logger.error(f"Agent {agent_id} crashed: {e}")
            result_json = ""

    if not result_json:
        logger.error(f"{agent_id} failed to propose trades.")
        return []

    try:
        trades_data = json.loads(result_json)
        trades = trades_data.get("trades", [])
        logger.info(f"{agent_id} proposed: {len(trades)} trades.")
        return trades
    except json.JSONDecodeError:
        logger.error(f"{agent_id} returned invalid JSON: {result_json}")
        return []


def fetch_ohlc_bars(tickers: set[str]) -> dict:
    """Fetch today's daily OHLC bar for a set of tickers (used by the T+1 daily path)."""
    ohlc_data = {}
    if not tickers:
        return ohlc_data

    import yfinance as yf
    data = yf.download(list(tickers), period="1d", progress=False)
    if data.empty:
        return ohlc_data

    for tkr in tickers:
        try:
            row = data.xs(tkr, level=1, axis=1).iloc[-1] if len(tickers) > 1 else data.iloc[-1]
            ohlc_data[tkr] = {
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
            }
        except Exception:
            continue
    return ohlc_data


def run_agent(agent_id: str, snapshot: dict, simulator: PortfolioSimulator):
    """Run a single agent for the current day (T+1: decide after close, fill at next open)."""
    from .config import AGENTS

    if agent_id not in AGENTS:
        logger.error(f"Agent {agent_id} not found in config.")
        return

    agent = AGENTS[agent_id]

    # 1. Simulator pre-LLM checks: fetch today's OHLC for held/pending tickers,
    # check stops/targets, fill yesterday's pending orders at today's open.
    tickers_needed = set(simulator.position_tickers + [o.ticker for o in simulator.pending_orders])
    ohlc_data = fetch_ohlc_bars(tickers_needed)

    current_date = snapshot["metadata"]["date"]
    simulator.check_stops_and_targets(ohlc_data, current_date)

    open_prices = {tkr: bar["open"] for tkr, bar in ohlc_data.items()}
    simulator.execute_pending_orders(open_prices, current_date)
    simulator.mark_to_market(open_prices, current_date)

    trades = get_agent_trades(agent, agent_id, snapshot, simulator.get_portfolio_summary(), intraday=False)
    simulator.submit_orders(trades, current_date)

    simulator.save_state()
    simulator.save_snapshot_history()


def run_agent_live_tick(agent_id: str, snapshot: dict, simulator: PortfolioSimulator, now_et):
    """Run a single agent for one live intraday check-in.
    Unlike run_agent (T+1), any accepted trade fills IMMEDIATELY at the current live price,
    and the agent may choose to hold indefinitely between check-ins."""
    from .config import AGENTS

    if agent_id not in AGENTS:
        logger.error(f"Agent {agent_id} not found in config.")
        return

    agent = AGENTS[agent_id]
    today = now_et.date().isoformat()

    # 1. Live prices for currently held tickers -> check stops/targets against the live tape.
    held_tickers = set(simulator.position_tickers)
    prices = fetch_live_prices(held_tickers)
    live_ohlc = {t: {"open": p, "high": p, "low": p, "close": p} for t, p in prices.items()}
    simulator.check_stops_and_targets(live_ohlc, today)

    # 2. Ask the agent (or random control) what it wants to do right now.
    trades = get_agent_trades(agent, agent_id, snapshot, simulator.get_portfolio_summary(), intraday=True,
                               now_et=now_et.strftime("%Y-%m-%d %H:%M %Z"))

    # 3. Fetch live prices for any newly-referenced tickers, then validate + fill immediately.
    new_tickers = {t.get("ticker", "").upper() for t in trades} - prices.keys()
    if new_tickers:
        prices.update(fetch_live_prices(new_tickers))

    results = simulator.submit_orders(trades, today)
    simulator.execute_pending_orders(prices, today)  # same-tick fill, not T+1
    simulator.mark_to_market(prices, today)

    simulator.save_state()
    simulator.save_snapshot_history()
    return results


def fetch_live_prices(tickers: set[str]) -> dict:
    """Fetch the current live/last-traded price for a set of tickers."""
    prices = {}
    if not tickers:
        return prices

    import yfinance as yf
    for tkr in tickers:
        try:
            price = yf.Ticker(tkr).fast_info.get("last_price")
            if price:
                prices[tkr] = float(price)
        except Exception as e:
            logger.warning(f"Could not fetch live price for {tkr}: {e}")
    return prices


def run_all_agents(snapshot_date: str):
    """Main entry point for running all agents on a specific date."""
    from .snapshot_engine import build_mih_snapshot
    from datetime import date
    from .config import AGENTS, STARTING_CAPITAL, RULES, PORTFOLIOS_DIR, SNAPSHOTS_DIR
    
    logger.info(f"--- STARTING MIH-DRIVEN DAILY RUN: {snapshot_date} ---")
    
    dt = date.fromisoformat(snapshot_date)
    # The MIH Snapshot now drives the high-intelligence context
    snapshot = build_mih_snapshot(dt, save_dir=SNAPSHOTS_DIR)
    
    # Metadata structure changed in V2
    if not snapshot.get("metadata", {}).get("is_market_day", True):
        logger.info(f"{snapshot_date} is not a market day. Skipping.")
        return
        
    # Run agents
    agent_ids = list(AGENTS.keys())
    for i, agent_id in enumerate(agent_ids):
        # Apply Jitter (except for the first one for speed, or always for safety)
        if i > 0:
            delay = random.uniform(5, 15)
            logger.info(f"Roster Jitter: Waiting {delay:.1f}s before starting {agent_id}...")
            time.sleep(delay)
            
        simulator = PortfolioSimulator(agent_id, STARTING_CAPITAL, RULES, PORTFOLIOS_DIR)
        run_agent(agent_id, snapshot, simulator)

    logger.info("--- DAILY RUN COMPLETE ---")
