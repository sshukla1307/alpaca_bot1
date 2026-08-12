"""
AI Portfolio Experiment V2 — Live Money Dashboard Exporter

Reads the live-money audit trail (equity_history.jsonl, order_log.jsonl) and
writes small, dashboard-ready JSON files that index.html fetches directly.
Single real account, not multi-agent comparison — much simpler than the old
paper-simulation exporter this replaces.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def _read_jsonl(path: Path) -> list:
    if not path.exists():
        return []
    records = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def export_for_dashboard(data_dir: Path, out_dir: Path):
    """
    Reads from: v2/data/live_money/equity_history.jsonl, order_log.jsonl
    Writes to: <out_dir>/equity.json, positions.json, trades.json, last_updated.json
    """
    logger.info("Exporting live-money data to dashboard format...")

    live_dir = data_dir / "live_money"
    equity_history = _read_jsonl(live_dir / "equity_history.jsonl")
    order_log = _read_jsonl(live_dir / "order_log.jsonl")

    out_dir.mkdir(parents=True, exist_ok=True)

    if not equity_history:
        logger.warning("No equity history found yet. Nothing to export.")
        return

    # 1. equity.json — full time series for the equity curve
    equity_series = [
        {"timestamp": snap["timestamp"], "cash": snap["cash"], "equity": snap["equity"]}
        for snap in equity_history
    ]
    with open(out_dir / "equity.json", "w") as f:
        json.dump(equity_series, f, indent=2)

    # 2. positions.json — current (latest) holdings
    latest = equity_history[-1]
    with open(out_dir / "positions.json", "w") as f:
        json.dump({
            "timestamp": latest["timestamp"],
            "cash": latest["cash"],
            "settled_cash": latest.get("settled_cash"),  # None for snapshots recorded before this field existed
            "equity": latest["equity"],
            "positions": latest["positions"],
        }, f, indent=2)

    # 3. trades.json — recent order events, most recent first
    trades = [e for e in order_log if e.get("action") not in (None,)]
    trades.sort(key=lambda e: e.get("logged_at", ""), reverse=True)
    with open(out_dir / "trades.json", "w") as f:
        json.dump(trades[:200], f, indent=2)

    # 4. last_updated.json
    with open(out_dir / "last_updated.json", "w") as f:
        json.dump({"timestamp": datetime.now(timezone.utc).isoformat()}, f, indent=2)

    logger.info(
        f"Dashboard export complete. {len(equity_series)} equity points, "
        f"{len(latest['positions'])} open positions, {len(trades)} logged order events."
    )
