import math
import unittest

from reference_basket import (
    BasketMember,
    CANDIDATE_BASKETS,
    EXPLORATORY_BASKETS,
    ReferenceBasketSpec,
    basket_log_return,
    return_direction,
)


class ReferenceBasketTests(unittest.TestCase):
    def test_weights_must_sum_to_one(self):
        with self.assertRaises(ValueError):
            ReferenceBasketSpec("bad", "v1", (BasketMember("BTCUSDT", 0.8),))

    def test_missing_price_is_rejected(self):
        spec = CANDIDATE_BASKETS["BTC_ETH_EQUAL_V1"]
        with self.assertRaises(ValueError):
            basket_log_return(spec, {"BTCUSDT": 100}, {"BTCUSDT": 101})

    def test_fingerprint_is_stable_across_member_order(self):
        left = ReferenceBasketSpec(
            "PAIR", "v1", (BasketMember("BTCUSDT", 0.5), BasketMember("ETHUSDT", 0.5)))
        right = ReferenceBasketSpec(
            "PAIR", "v1", (BasketMember("ETHUSDT", 0.5), BasketMember("BTCUSDT", 0.5)))
        self.assertEqual(left.fingerprint, right.fingerprint)

    def test_weighted_log_return_and_direction(self):
        spec = CANDIDATE_BASKETS["BTC_ETH_EQUAL_V1"]
        result = basket_log_return(
            spec,
            {"BTCUSDT": 100.0, "ETHUSDT": 200.0},
            {"BTCUSDT": 110.0, "ETHUSDT": 180.0},
        )
        expected = 0.5 * math.log(1.1) + 0.5 * math.log(0.9)
        self.assertAlmostEqual(result, expected)
        self.assertEqual(return_direction(result), "DOWN")

    def test_versions_have_distinct_identity_and_fingerprint(self):
        first = ReferenceBasketSpec("PAIR", "v1", (BasketMember("BTCUSDT", 1.0),))
        second = ReferenceBasketSpec("PAIR", "v2", (BasketMember("BTCUSDT", 1.0),))
        self.assertNotEqual(first.identity, second.identity)
        self.assertNotEqual(first.fingerprint, second.fingerprint)

    def test_major_five_exploratory_basket_is_equal_weighted(self):
        spec = EXPLORATORY_BASKETS["MAJOR5_EQUAL_EXPLORATORY_V1"]
        self.assertEqual(tuple(member.symbol for member in spec.members), (
            "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"))
        self.assertTrue(all(member.weight == 0.2 for member in spec.members))


if __name__ == "__main__":
    unittest.main()
