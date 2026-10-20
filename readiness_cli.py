"""Read-only research-readiness report for a collector database."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from observation_store import ObservationStore
from readiness_audit import audit_research_readiness


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit synchronized data without training or opening holdouts")
    parser.add_argument("database")
    parser.add_argument("--payout", type=float,
                        help="optional visible-session cross-check, e.g. 0.83; "
                             "otherwise require one consistent screen-OCR value")
    args = parser.parse_args()
    database = Path(args.database)
    if not database.is_file():
        parser.error("database does not exist")
    report = audit_research_readiness(
        ObservationStore(database), payout_ratio=args.payout)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "READY_FOR_BASELINE_RESEARCH" else 3


if __name__ == "__main__":
    raise SystemExit(main())
