"""
AI Portfolio Experiment V2 — Dashboard Exporter

Reads the granular agent portfolio states and outputs the aggregated
JSON and CSV formats required by the dashboard in index.html.

Dashboard shapes (fixed by index.html's parsing code, not by us):
  - performance.csv: wide format, "Date,<Label1>,<Label2>,..." (one column per agent)
  - portfolios.json: {date: {label: {timestamp, cash, equity, positions: [...]}}}
  - transactions.json: {label: [{timestamp, side, symbol, qty, price, type}, ...]}
"""

import json
import logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)


def export_for_dashboard(data_dir: Path, output_dir: Path):
    """
    Export all V2 portfolio data to the dashboard's expected formats.
    Reads from: v2/data/portfolios/*_history.json
    Writes to: v1/logs/portfolios.json, v1/logs/transactions.json, v1/logs/performance.csv
    """
    logger.info("Exporting V2 data to Dashboard format...")

    from .config import AGENTS

    portfolios_dir = data_dir / "portfolios"
    if not portfolios_dir.exists():
        logger.warning("No portfolios directory found. Run agents first.")
        return

    # Only export LLM-backed agents (skip random_control), keyed by persona label
    agent_labels = {
        agent_id: agent.persona.value.title()
        for agent_id, agent in AGENTS.items()
        if agent.provider_key
    }

    per_agent_history = {}   # agent_id -> {date: state}
    dashboard_transactions = {label: [] for label in agent_labels.values()}
    dates_seen = set()

    for agent_id, label in agent_labels.items():
        hist_file = portfolios_dir / f"{agent_id}_history.json"
        if not hist_file.exists():
            continue

        with open(hist_file) as f:
            historian = json.load(f)
        if not historian:
            continue

        by_date = {}
        for state in historian:
            by_date[state["date"]] = state
            dates_seen.add(state["date"])

            for trade in state.get("transactions_today", []):
                dashboard_transactions[label].append({
                    "timestamp": state["date"],
                    "side": trade["action"].lower(),
                    "symbol": trade["ticker"],
                    "qty": trade["shares"],
                    "price": trade["price"],
                    "type": trade.get("order_type", "MARKET").lower(),
                })
        per_agent_history[agent_id] = by_date

    if not dates_seen:
        logger.warning("No snapshot history found for any agent. Nothing to export.")
        return

    sorted_dates = sorted(dates_seen)

    # 1. performance.csv (wide format, one column per agent label)
    csv_rows = ["Date," + ",".join(agent_labels.values())]
    for date_str in sorted_dates:
        row = [date_str]
        for agent_id in agent_labels:
            state = per_agent_history.get(agent_id, {}).get(date_str)
            row.append(f"{state['portfolio_value']:.2f}" if state else "")
        csv_rows.append(",".join(row))

    # 2. portfolios.json (date-first nesting, as the dashboard expects)
    dashboard_portfolios = {}
    for date_str in sorted_dates:
        day_entry = {}
        for agent_id, label in agent_labels.items():
            state = per_agent_history.get(agent_id, {}).get(date_str)
            if not state:
                continue

            positions = []
            for ticker, pos in state["positions"].items():
                direction = pos.get("direction", "LONG")
                raw_value = pos["shares"] * pos["current_price"]
                positions.append({
                    "ticker": ticker,
                    "symbol": ticker,
                    "direction": direction,
                    "qty": pos["shares"],
                    "avg_price": pos["entry_price"],
                    "current_price": pos["current_price"],
                    "market_value": raw_value if direction == "LONG" else -raw_value,
                    "unrealized_pl": pos.get("unrealized_pnl", 0.0),
                    "unrealized_pl_pct": pos.get("unrealized_pnl_pct", 0.0) * 100,
                })

            day_entry[label] = {
                "timestamp": date_str,
                "cash": state["cash"],
                "equity": state["portfolio_value"],
                "positions": positions,
            }
        dashboard_portfolios[date_str] = day_entry

    # Write output files
    output_dir.mkdir(parents=True, exist_ok=True)

    with open(output_dir / "performance.csv", "w") as f:
        f.write("\n".join(csv_rows))

    with open(output_dir / "portfolios.json", "w") as f:
        json.dump(dashboard_portfolios, f, indent=2)

    with open(output_dir / "transactions.json", "w") as f:
        json.dump(dashboard_transactions, f, indent=2)

    with open(output_dir / "last_updated.json", "w") as f:
        json.dump({"timestamp": datetime.now().isoformat()}, f, indent=2)

    logger.info(
        f"Dashboard export complete. Exported {len(agent_labels)} agents "
        f"across {len(sorted_dates)} days."
    )
