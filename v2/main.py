"""
AI Portfolio Experiment V2 — Main Entrypoint

Flags:
  --day-0     Generate the live agent's Day 0 playbook (one-time setup)
  --export    Re-export the dashboard from the current live-money audit trail

The recurring live trading loop itself runs via `python -m v2.live_money_runner`
(what live_money_trading.yml actually invokes on its hourly schedule) — this
file is only for one-off setup/maintenance commands.
"""

import argparse
import logging
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("V2_Main")

def main():
    parser = argparse.ArgumentParser(description="AI Portfolio Experiment V2 — Live Money Bot")
    parser.add_argument("--day-0", action="store_true", help="Generate the live agent's Day 0 playbook")
    parser.add_argument("--export", action="store_true", help="Re-export the dashboard from the current audit trail")

    args = parser.parse_args()

    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).parent.parent / ".env")
    except ImportError:
        logger.warning("python-dotenv not installed. Using system environment variables.")

    if args.day_0:
        logger.info("Generating Day 0 playbook...")
        from v2.meta_strategy import generate_playbook
        generate_playbook()

    if args.export:
        logger.info("Exporting dashboard from live-money audit trail...")
        from v2.dashboard_exporter import export_for_dashboard
        data_dir = Path(__file__).parent / "data"
        out_dir = data_dir / "live_money" / "dashboard"
        export_for_dashboard(data_dir, out_dir)

    if not args.day_0 and not args.export:
        parser.print_help()

if __name__ == "__main__":
    main()
