import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from database import ForensicDatabase
from streamer import revalidate_existing_tokens


class GraduationRevalidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "forensics.db"
        self.db = ForensicDatabase(str(self.path))

    def tearDown(self):
        self.temp.cleanup()

    def add_token(self, ca, chain_id, **values):
        data = {
            "ca": ca, "chain_id": chain_id, "symbol": "TEST",
            "is_qualified": values.pop("is_qualified", True),
            "is_graduated": values.pop("is_graduated", True),
            "is_training_anchor": values.pop("is_training_anchor", False),
            **values,
        }
        self.db.upsert_token(data)

    def flags(self, ca):
        conn = sqlite3.connect(self.path)
        try:
            return conn.execute(
                "SELECT is_qualified, is_graduated FROM tokens WHERE ca=?", (ca,)
            ).fetchone()
        finally:
            conn.close()

    def test_dry_run_reports_legacy_row_without_changing_it(self):
        ca = "0x" + "1" * 40
        self.add_token(ca, 4663)

        result = revalidate_existing_tokens(self.db)

        self.assertEqual(result["rejected"], 1)
        self.assertEqual(result["would_demote"], 1)
        self.assertEqual(result["demoted"], 0)
        self.assertEqual(self.flags(ca), (1, 1))

    def test_apply_demotes_legacy_row_and_keeps_an_audit_record(self):
        ca = "0x" + "2" * 40
        self.add_token(ca, 5042, qualification_reasons={"legacy": True})

        result = revalidate_existing_tokens(self.db, apply=True)

        self.assertEqual(result["demoted"], 1)
        self.assertEqual(self.flags(ca), (0, 0))
        conn = sqlite3.connect(self.path)
        try:
            outcome, prior_qualified, prior_graduated = conn.execute(
                """SELECT outcome, prior_is_qualified, prior_is_graduated
                   FROM graduation_revalidations WHERE ca=?""", (ca,)
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual((outcome, prior_qualified, prior_graduated), ("rejected", 1, 1))

    def test_training_anchor_is_never_demoted(self):
        ca = "0x" + "3" * 40
        self.add_token(ca, 4663, is_training_anchor=True)

        result = revalidate_existing_tokens(self.db, apply=True)

        self.assertEqual(result["anchors_preserved"], 1)
        self.assertEqual(self.flags(ca), (1, 1))

    def test_valid_arc_evidence_is_promoted(self):
        ca = "0x" + "4" * 40
        evidence = {
            "gate": "arc_confirmed_dex_pair", "chain_id": 5042,
            "market_pair_url": "https://dexscreener.com/arc/pair",
            "pair_created_at": 1_700_000_000_000,
        }
        self.add_token(ca, 5042, is_qualified=False, is_graduated=False,
                       graduation_evidence=evidence)

        result = revalidate_existing_tokens(self.db, apply=True)

        self.assertEqual(result["verified"], 1)
        self.assertEqual(result["promoted"], 1)
        self.assertEqual(self.flags(ca), (1, 1))

    def test_valid_robinhood_evidence_rechecks_the_token_bound_transaction(self):
        ca = "0x" + "5" * 40
        tx = "0x" + "a" * 64
        evidence = {
            "gate": "robinhood_token_bound_launchpad_event_plus_fresh_dex_pair",
            "chain_id": 4663, "event_timestamp": 1_700_000_000,
            "market_pair_url": "https://dexscreener.com/robinhood/pair",
            "pair_created_at": 1_700_000_100_000,
            "migration_path": {"transaction_hash": tx},
        }
        self.add_token(ca, 4663, graduation_evidence=evidence)

        with patch("streamer.rbh_migration_tokens", return_value={ca: {"transaction_hash": tx}}) as prove:
            result = revalidate_existing_tokens(self.db, apply=True)

        prove.assert_called_once_with(tx)
        self.assertEqual(result["verified"], 1)
        self.assertEqual(self.flags(ca), (1, 1))

    def test_rpc_failure_does_not_demote_a_robinhood_token(self):
        ca = "0x" + "6" * 40
        evidence = {
            "gate": "robinhood_token_bound_launchpad_event_plus_fresh_dex_pair",
            "chain_id": 4663, "event_timestamp": 1_700_000_000,
            "market_pair_url": "https://dexscreener.com/robinhood/pair",
            "pair_created_at": 1_700_000_100_000,
            "migration_path": {"transaction_hash": "0x" + "b" * 64},
        }
        self.add_token(ca, 4663, graduation_evidence=json.dumps(evidence))

        with patch("streamer.rbh_migration_tokens", side_effect=RuntimeError("RPC down")):
            result = revalidate_existing_tokens(self.db, apply=True)

        self.assertEqual(result["unverified"], 1)
        self.assertEqual(self.flags(ca), (1, 1))


if __name__ == "__main__":
    unittest.main()
