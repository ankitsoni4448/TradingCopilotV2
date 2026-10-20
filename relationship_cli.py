"""Read-only development report for one preregistered lead/lag hypothesis."""
from __future__ import annotations

import argparse
import json
import sys

from observation_store import ObservationStore
from reference_basket import CANDIDATE_BASKETS
from relationship_lab import (
    ALLOWED_HORIZONS_SECONDS,
    PREREGISTERED_V1_LOOKBACKS,
    build_lead_lag_samples,
    chronological_development_split,
    development_report,
    preregistered_v1_registry,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Leakage-resistant Crypto IDX relationship development report")
    parser.add_argument("database")
    parser.add_argument("--basket", choices=sorted(CANDIDATE_BASKETS),
                        default="BTC_ETH_EQUAL_V1")
    parser.add_argument("--lookback", type=int,
                        choices=PREREGISTERED_V1_LOOKBACKS, default=30)
    parser.add_argument("--horizon", type=int,
                        choices=sorted(ALLOWED_HORIZONS_SECONDS), default=60)
    args = parser.parse_args(argv)
    try:
        samples = build_lead_lag_samples(
            ObservationStore(args.database),
            basket=CANDIDATE_BASKETS[args.basket],
            lookback_seconds=args.lookback, horizon_seconds=args.horizon)
        split = chronological_development_split(samples)
        output = {
            "experiment": {
                "basket": args.basket, "lookback_seconds": args.lookback,
                "horizon_seconds": args.horizon,
            },
            "registry": preregistered_v1_registry(len(CANDIDATE_BASKETS)),
            "report": development_report(split),
        }
        print(json.dumps(output, sort_keys=True))
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"status": "INSUFFICIENT_OR_INVALID_EVIDENCE",
                          "reason": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
