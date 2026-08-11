"""
AI Portfolio Experiment V2 — Agent Runner

Shared prompt-building and decision-making code for the live-money agent.
Injects the MIH snapshot and the agent's meta-strategy playbook, provides
the MCP tools, and runs the LLM tool-calling loop until it proposes trades.
Execution against the real Alpaca account happens in live_money_runner.py,
not here — this module has no broker dependency of its own.
"""

import json
import logging
from pathlib import Path

from .config import RULES, PERSONA_MODIFIERS
from .mcp_servers import get_tool_definitions
from .api_adapters import UnifiedLLMClient

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

def build_system_prompt(agent_config, playbook: str) -> str:
    """Build the system prompt combining rules, persona, and the Day 0 playbook."""
    prompt = f"YOU ARE {agent_config.display_name}.\n\n"

    prompt += "=== SYSTEM RULES ===\n"
    for rule_name, rule_val in RULES.__dict__.items():
        prompt += f"- {rule_name}: {rule_val}\n"

    prompt += "\n=== YOUR PERSONA ===\n"
    prompt += f"{PERSONA_MODIFIERS[agent_config.persona]}\n"

    prompt += "\n=== YOUR INVESTMENT PLAYBOOK (META-STRATEGY) ===\n"
    prompt += "You defined this strategy on Day 0. You MUST adhere to it.\n"
    prompt += f"{playbook}\n\n"

    prompt += "\n=== LIVE CHECK-IN ===\n"
    prompt += (
        "This is a LIVE check-in that repeats roughly once an hour (at :30 past) while the market is open "
        "(9:30am-4:00pm ET), against a REAL Alpaca brokerage account.\n"
        "- Any trade you submit now fills IMMEDIATELY at the current live price.\n"
        "- Your stop-loss/take-profit are enforced continuously by Alpaca once set, not just checked once an hour.\n"
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

def build_user_prompt(snapshot: dict, portfolio_state: dict, now_et: str = "") -> str:
    prompt = f"Market Date: {snapshot['metadata']['date']}\n"
    if now_et:
        prompt += f"Current Time (ET): {now_et}\n"
    prompt += "\n"

    # Inject the high-density Market Intelligence Hub (MIH) payload
    prompt += "=== MARKET INTELLIGENCE HUB (MIH) ===\n"
    prompt += f"{snapshot.get('mih_prompt_payload', 'No MIH data available.')}\n\n"

    prompt += "=== YOUR CURRENT PORTFOLIO STATE ===\n"
    prompt += json.dumps(portfolio_state, indent=2) + "\n\n"

    prompt += "This is a live check-in. Trade now only if you see a genuine reason to; otherwise call propose_trades with an empty trades list to HOLD."
    return prompt


def get_agent_trades(agent, agent_id: str, snapshot: dict, portfolio_state: dict, now_et: str = "") -> list[dict]:
    """Get this tick's trade decisions from the LLM. portfolio_state is a plain
    dict (see live_money_runner._build_portfolio_state for its shape)."""
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
    sys_prompt = build_system_prompt(agent, playbook)
    user_prompt = build_user_prompt(snapshot, portfolio_state, now_et=now_et)

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


def fetch_live_prices(tickers: set[str], max_retries: int = 3, retry_delay: float = 1.5) -> dict:
    """Fetch the current live/last-traded price for a set of tickers.

    yfinance's fast_info hits Yahoo Finance directly and is occasionally flaky
    (transient failures, thin/small-cap tickers) — retries a few times before
    falling back to the most recent daily bar's close, so a real trade decision
    isn't silently dropped over a data-fetch hiccup. The fallback price can be
    a bit stale (up to a few days old around a weekend/holiday), which is
    logged clearly since it affects the accuracy of any stop-loss/take-profit
    levels computed from it.
    """
    prices = {}
    if not tickers:
        return prices

    import time
    import yfinance as yf

    for tkr in tickers:
        price = None

        for attempt in range(1, max_retries + 1):
            try:
                candidate = yf.Ticker(tkr).fast_info.get("last_price")
                if candidate:
                    price = float(candidate)
                    break
            except Exception as e:
                logger.warning(f"[{tkr}] fast_info attempt {attempt}/{max_retries} failed: {e}")
            if attempt < max_retries:
                time.sleep(retry_delay)

        if price is None:
            try:
                hist = yf.Ticker(tkr).history(period="5d")
                if not hist.empty:
                    price = float(hist["Close"].iloc[-1])
                    logger.warning(
                        f"[{tkr}] fast_info failed after {max_retries} attempts; "
                        f"using fallback daily-close price ${price:.2f} instead."
                    )
            except Exception as e:
                logger.warning(f"[{tkr}] Fallback history fetch also failed: {e}")

        if price:
            prices[tkr] = price
        else:
            logger.warning(f"[{tkr}] Could not fetch a price via fast_info or fallback history.")

    return prices
