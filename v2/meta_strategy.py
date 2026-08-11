"""
AI Portfolio Experiment V2 — Meta Strategy (Day 0)

Run once to define the live agent's "Playbook" (investment strategy). The
resulting document is saved to disk and injected into the prompt at every
live check-in, so the agent doesn't drift from its initial strategy.
"""

import logging
from pathlib import Path

from .config import PROVIDERS, PERSONA_MODIFIERS, Persona, PLAYBOOKS_DIR
from .api_adapters import UnifiedLLMClient

logger = logging.getLogger(__name__)

LIVE_AGENT_ID = "chatgpt_live_aggressive"
LIVE_PLAYBOOK_FILENAME = "chatgpt_aggressive.md"

DAY_0_PROMPT = """
You are about to begin trading a real Alpaca brokerage account autonomously.
Before we start, you must define your "Meta-Strategy" (Investment Playbook).

This playbook will be injected into your prompt at every live check-in
(roughly once an hour while the market is open). You must adhere to it.

=== SYSTEM CONSTRAINTS ===
- Capital: your actual current Alpaca account cash/equity, shown to you fresh at every check-in.
- Execution: LIVE — orders fill immediately at the current price, not at a future open.
- Instrument Universe: US Equities (including penny stocks) and ETFs. NO crypto, NO options.
- Direction: LONG only (BUY to open, SELL to close). No short selling.
- Position Sizing: 5% to 25% per trade. Max 15 open positions.
- Risk Management: the system enforces a mandatory Stop-Loss AND Take-Profit on every position
  (both required, no exceptions) — but YOU decide where to place them.

Please define your Playbook answering the following:
1. Core Strategy: Are you value, growth, momentum, mean-reversion, or macro-driven?
2. Asset Selection: How will you pick stocks? What sectors will you favor? Will you use ETFs?
3. Trade Horizon: How long do you expect to hold a typical position?
4. Risk Profile: What will be your typical Stop-Loss % and Take-Profit %?
5. Data Usage: Which of your available tools (FRED macro, market news, Technicals, Fundamentals) will you prioritize?

Output a detailed, structured markdown document. This is your constitution.
No greetings, just the markdown document.
"""


def generate_playbook(force: bool = False):
    """Generate the Day 0 playbook for the live agent, unless one already exists."""
    playbook_path = PLAYBOOKS_DIR / LIVE_PLAYBOOK_FILENAME

    if playbook_path.exists() and playbook_path.stat().st_size > 0 and not force:
        logger.info(f"Playbook already exists at {playbook_path}. Skipping (pass force=True to regenerate).")
        return

    logger.info("Generating Day 0 Playbook for the live agent...")
    provider = PROVIDERS["chatgpt"]
    system_prompt = (
        f"YOU ARE ChatGPT (Aggressive) — REAL MONEY.\n"
        f"Persona: {PERSONA_MODIFIERS[Persona.AGGRESSIVE]}\n"
    )

    client = UnifiedLLMClient(provider)
    try:
        # No tools for the Day 0 prompt, just the strategy text itself
        playbook = client.generate(system_prompt, DAY_0_PROMPT, tools=[], max_tool_calls=0)
        playbook_path.parent.mkdir(parents=True, exist_ok=True)
        with open(playbook_path, "w", encoding="utf-8") as f:
            f.write(playbook)
        logger.info(f"Playbook saved to {playbook_path}.")
    except Exception as e:
        logger.error(f"Failed to generate playbook: {e}")


if __name__ == "__main__":
    generate_playbook()
