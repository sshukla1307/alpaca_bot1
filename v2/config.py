"""
AI Portfolio Experiment V2 — Configuration
System rules, persona, and API settings for the live-money trading agent.
"""

import pathlib
from dataclasses import dataclass
from enum import Enum
from typing import Optional

# ─────────────────────────────────────────────
# System-Enforced Rules ("Code is Law")
# These CANNOT be overridden by the model.
# ─────────────────────────────────────────────

@dataclass(frozen=True)
class SystemRules:
    """Immutable trading rules enforced by live_money_runner.py."""
    min_stock_price: float = 0.01          # Minimum stock price (penny stocks allowed)
    max_position_pct: float = 0.25         # Max 25% of portfolio in one position
    min_position_pct: float = 0.05         # Min 5% of portfolio per trade
    max_positions: int = 15                # Max simultaneous positions
    max_daily_buys: int = 3                # Max new buy orders per day
    slippage_pct: float = 0.001            # 0.1% slippage on all fills
    fractional_shares: bool = True         # Allow fractional share trading
    long_only: bool = True                  # No short selling — SHORT/COVER rejected
    stop_loss_required: bool = True        # Every position must have a stop-loss
    take_profit_required: bool = True      # Every position must have a take-profit
    execution_model: str = "LIVE_IMMEDIATE"  # Orders fill immediately at the current live price
    stop_trigger_model: str = "ALPACA_BRACKET"  # Stop/TP enforced server-side by Alpaca continuously

RULES = SystemRules()

# ─────────────────────────────────────────────
# Persona
# ─────────────────────────────────────────────

class Persona(Enum):
    BALANCED = "balanced"
    AGGRESSIVE = "aggressive"

PERSONA_MODIFIERS = {
    Persona.BALANCED: (
        "You are a BALANCED portfolio manager. "
        "Seek a healthy balance between risk and reward. Mix growth and value opportunities. "
        "Use moderate position sizes (10-15%). Diversify across sectors. "
        "Target consistent returns with controlled drawdowns under 15%."
    ),
    Persona.AGGRESSIVE: (
        "You are an AGGRESSIVE portfolio manager, held to the standard of the "
        "greatest capital allocators in history -- reasoning from first "
        "principles about where real edge exists, not managing career risk. "
        "An idea without conviction is worth nothing: when you find a genuine "
        "edge, size it like you mean it (15-25% of the portfolio, your choice "
        "within that band, not clustered at the low end by habit). A "
        "watered-down bet on your own best idea isn't risk management, it's "
        "mediocrity -- the system's real risk controls (mandatory stop-loss "
        "and take-profit on every position, a 15-position cap, a 3-buy daily "
        "limit) already exist so you don't need to hedge your own conviction "
        "with a timid position size on top of them. Get in front of "
        "asymmetric setups before they're consensus, exit decisively the "
        "moment the thesis breaks (not because the price wobbled), and don't "
        "confuse activity with progress -- a quiet stretch is fine when "
        "nothing genuinely earns a position, but chronic underinvestment "
        "while a real opportunity sat there uncalled is a failure, not "
        "prudence. You are long-only, so channel that conviction into "
        "finding the best absolute opportunities to own, not into fighting "
        "the tape. Judge yourself the way history judges its best investors: "
        "not by any single trade, but by whether your capital ended up "
        "concentrated in your highest-conviction ideas and out of your weak "
        "ones. Target the highest Information Ratio against your chosen "
        "benchmark."
    ),
}

# ─────────────────────────────────────────────
# LLM Provider Configuration
# ─────────────────────────────────────────────

@dataclass
class LLMProvider:
    """Configuration for a single LLM API provider."""
    name: str
    model_id: str
    api_type: str
    api_key_env: str                       # Environment variable name for API key
    base_url: Optional[str] = None
    temperature: float = 0.0              # Deterministic output
    max_tokens: int = 4096
    supports_tool_calling: bool = True
    parallel_tool_calls: bool = False      # DISABLED for fairness/consistency

PROVIDERS = {
    "chatgpt": LLMProvider(
        name="ChatGPT",
        model_id="gpt-4o",
        api_type="openai",
        api_key_env="OPENAI_API_KEY",
    ),
}

# ─────────────────────────────────────────────
# Paths
# ─────────────────────────────────────────────

V2_ROOT = pathlib.Path(__file__).parent
DATA_DIR = V2_ROOT / "data"
SNAPSHOTS_DIR = DATA_DIR / "snapshots"
PLAYBOOKS_DIR = DATA_DIR / "playbooks"
SCHEMAS_DIR = V2_ROOT / "schemas"

for d in [SNAPSHOTS_DIR, PLAYBOOKS_DIR]:
    d.mkdir(parents=True, exist_ok=True)
