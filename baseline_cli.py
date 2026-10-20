"""Read-only validation baseline report for one forecast horizon."""
from __future__ import annotations

import argparse
import json

from baseline_lab import (
    evaluate_baseline_development,
    evaluate_baseline_walk_forward,
)
from feature_engine import build_feature_dataset, chronological_feature_split
from observation_store import ObservationStore
from relationship_lab import ALLOWED_HORIZONS_SECONDS


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate fixed baselines without opening the final holdout")
    parser.add_argument("database")
    parser.add_argument("--horizon", type=int, required=True,
                        choices=sorted(ALLOWED_HORIZONS_SECONDS))
    parser.add_argument("--payout", type=float, required=True,
                        help="net payout on a win, e.g. 0.82")
    parser.add_argument("--minimum-validation-signals", type=int, default=30)
    parser.add_argument("--walk-forward", action="store_true",
                        help="run purged inner-training walk-forward folds")
    parser.add_argument("--walk-forward-folds", type=int, default=4)
    parser.add_argument("--minimum-walk-forward-train-rows", type=int,
                        default=60)
    parser.add_argument("--minimum-fold-signals", type=int, default=20)
    args = parser.parse_args()
    rows = build_feature_dataset(
        ObservationStore(args.database), horizon_seconds=args.horizon).rows
    split = chronological_feature_split(rows)
    report = evaluate_baseline_development(
        split, payout_ratio=args.payout,
        minimum_validation_signals=args.minimum_validation_signals)
    if args.walk_forward:
        report["walk_forward"] = evaluate_baseline_walk_forward(
            split, payout_ratio=args.payout,
            folds=args.walk_forward_folds,
            minimum_train_rows=args.minimum_walk_forward_train_rows,
            minimum_test_signals=args.minimum_fold_signals)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
