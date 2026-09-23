import unittest

from source_intake import forwarded_text, parse_forwarded_tokens


class SourceIntakeTests(unittest.TestCase):
    def test_pons_notice_identifies_robinhood_and_referral_address(self):
        ca = "0x35d9123d4fa11ee93e350c59c7ef70741714374a"
        notice = (
            "Pons \U0001f309\nUniswap Migration\nALF | Agentic Liquid Fund\n"
            f"[GMGN](https://gmgn.ai/robinhood/token/ref_{ca})"
        )
        tokens = parse_forwarded_tokens(notice)
        self.assertEqual(len(tokens), 1)
        self.assertEqual(tokens[0].ca, ca)
        self.assertEqual(tokens[0].chain_id, 4663)
        self.assertEqual(tokens[0].source_kind, "forwarded_pons_migration")
        self.assertEqual(tokens[0].symbol, "ALF")
        self.assertEqual(tokens[0].name, "Agentic Liquid Fund")

    def test_multi_address_notice_disambiguates_token_dev_and_pair(self):
        token_ca = "0x1111111111111111111111111111111111111111"
        dev_ca = "0x2222222222222222222222222222222222222222"
        pair_ca = "0x3333333333333333333333333333333333333333"
        notice = (
            "Pons Migration Alert!\n"
            f"Token: {token_ca}\n"
            f"Dev: {dev_ca}\n"
            f"Pair: {pair_ca}\n"
            "$DOGE on Robinhood"
        )
        tokens = parse_forwarded_tokens(notice)
        self.assertEqual(len(tokens), 1)
        self.assertEqual(tokens[0].ca, token_ca)
        self.assertEqual(tokens[0].dev_wallet, dev_ca)
        self.assertEqual(tokens[0].pair_address, pair_ca)
        self.assertEqual(tokens[0].symbol, "DOGE")
        self.assertEqual(tokens[0].chain_id, 4663)

    def test_dexscreener_pair_url_is_not_treated_as_token_ca(self):
        token_ca = "0x1111111111111111111111111111111111111111"
        pair_ca = "0x3333333333333333333333333333333333333333"
        notice = (
            f"Arc token alert: {token_ca}\n"
            f"Chart: https://dexscreener.com/arc/{pair_ca}"
        )
        tokens = parse_forwarded_tokens(notice)
        self.assertEqual(len(tokens), 1)
        self.assertEqual(tokens[0].ca, token_ca)
        self.assertEqual(tokens[0].pair_address, pair_ca)
        self.assertEqual(tokens[0].chain_id, 5042)

    def test_dev_only_notice_treats_dev_as_seed(self):
        dev_ca = "0x2222222222222222222222222222222222222222"
        notice = f"Robinhood Dev wallet active: Dev: {dev_ca}"
        tokens = parse_forwarded_tokens(notice)
        self.assertEqual(len(tokens), 1)
        self.assertEqual(tokens[0].ca, dev_ca)
        self.assertEqual(tokens[0].dev_wallet, dev_ca)
        self.assertTrue(tokens[0].metadata.get("is_dev_seed"))

    def test_bsc_notice_is_not_misclassified_as_robinhood_or_arc(self):
        ca = "0x0dac078a7511c3587dc3aad2c0991950093d7777"
        tokens = parse_forwarded_tokens(
            f"NEW TOKEN MIGRATION [BSCScan](https://bscscan.com/token/{ca})"
        )
        self.assertEqual(tokens[0].chain_id, 56)
        self.assertEqual(tokens[0].source_kind, "forwarded_bsc_token")

    def test_reply_text_is_used_as_the_forwarded_source(self):
        ca = "0x35d9123d4fa11ee93e350c59c7ef70741714374a"
        text = forwarded_text({"text": "/ingest", "reply_to_message": {"text": ca}})
        self.assertIn(ca, text)


if __name__ == "__main__":
    unittest.main()
