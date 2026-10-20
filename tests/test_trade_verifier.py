import unittest

from trade_verifier import TradeEvidence, verify_trade


def evidence(**overrides):
    values = dict(
        direction="UP", entry_price=641.8673912912,
        entry_observed_at_ms=1_000_000, expiry_at_ms=1_060_000,
        exit_price=641.8673915950, exit_observed_at_ms=1_060_000,
        stake=100.0, payout_ratio=0.82,
        reported_outcome="WIN", reported_total_return=182.0)
    values.update(overrides)
    return TradeEvidence(**values)


class TradeVerifierTests(unittest.TestCase):
    def test_potential_return_before_expiry_is_not_scored(self):
        result = verify_trade(evidence(
            exit_observed_at_ms=1_053_000,
            reported_outcome=None,
            reported_total_return=182.0))
        self.assertEqual(result.status, "PENDING")
        self.assertIsNone(result.mechanical_outcome)

    def test_completed_up_win_and_payout_are_consistent(self):
        result = verify_trade(evidence())
        self.assertEqual(result.status, "CONSISTENT")
        self.assertEqual(result.mechanical_outcome, "WIN")
        self.assertEqual(result.expected_total_return, 182.0)

    def test_reported_win_against_falling_price_is_flagged(self):
        result = verify_trade(evidence(exit_price=641.8673900000))
        self.assertEqual(result.status, "MISMATCH")
        self.assertEqual(result.mechanical_outcome, "LOSS")
        self.assertIn("reported_outcome_mismatch", result.reasons)

    def test_incorrect_payout_is_flagged_independently(self):
        result = verify_trade(evidence(reported_total_return=183.0))
        self.assertEqual(result.status, "MISMATCH")
        self.assertEqual(result.reasons, ("reported_total_return_mismatch",))


if __name__ == "__main__":
    unittest.main()
