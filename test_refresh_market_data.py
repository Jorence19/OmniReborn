import unittest

from refresh_market_data import choose_pairs


class MarketRefreshTests(unittest.TestCase):
    def test_selects_most_liquid_base_token_pair(self):
        ca = "0x" + "a" * 40
        other = "0x" + "b" * 40
        payload = [
            {"baseToken": {"address": ca}, "liquidity": {"usd": 10}, "marketCap": 100},
            {"baseToken": {"address": ca.upper().replace("0X", "0x")}, "liquidity": {"usd": 50}, "marketCap": 500},
            {"baseToken": {"address": other}, "liquidity": {"usd": 999}, "marketCap": 999},
        ]
        selected = choose_pairs(payload, {ca})
        self.assertEqual(selected[ca]["marketCap"], 500)

    def test_quote_token_pair_is_not_mislabeled_with_base_market_cap(self):
        ca = "0x" + "a" * 40
        payload = [{
            "baseToken": {"address": "0x" + "b" * 40},
            "quoteToken": {"address": ca},
            "liquidity": {"usd": 1000},
            "marketCap": 999999,
        }]
        self.assertEqual(choose_pairs(payload, {ca}), {})


if __name__ == "__main__":
    unittest.main()
