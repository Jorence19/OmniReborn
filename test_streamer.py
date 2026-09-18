import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from database import ForensicDatabase
from streamer import QueueStore, ingest_and_enrich_job, topic_address, validate_address


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = ForensicDatabase(str(Path(self.temp.name) / "collector.db"))
        self.store = QueueStore(self.db)
        self.ca = "0x" + "1" * 40

    def tearDown(self):
        self.temp.cleanup()

    def test_queue_is_idempotent_and_retries(self):
        self.store.enqueue(self.ca, "test", {"n": 1})
        self.store.enqueue(self.ca.upper().replace("0X", "0x"), "test", {"n": 2})
        self.assertEqual(self.store.stats()["pending"], 1)
        jobs = self.store.claim("worker", limit=5)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["attempts"], 1)
        self.store.fail(jobs[0], RuntimeError("temporary"))
        self.assertEqual(self.store.stats()["retry"], 1)
        with self.db.get_connection() as conn:
            conn.execute("UPDATE ingestion_jobs SET next_attempt_at=datetime('now')")
        retried = self.store.claim("worker", limit=1)
        self.assertEqual(retried[0]["attempts"], 2)
        self.store.succeed(self.ca, 4663)
        self.assertEqual(self.store.stats()["succeeded"], 1)

    def test_expired_lease_is_recovered(self):
        self.store.enqueue(self.ca, "test")
        self.store.claim("dead-worker", limit=1)
        with self.db.get_connection() as conn:
            conn.execute("UPDATE ingestion_jobs SET lease_until='2000-01-01 00:00:00'")
        recovered = self.store.claim("new-worker", limit=1)
        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0]["attempts"], 2)


class GuardrailTests(unittest.TestCase):
    def test_live_refresh_preserves_manual_qualification_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            db = ForensicDatabase(str(Path(directory) / "collector.db"))
            ca = "0x" + "9" * 40
            db.upsert_token({"ca": ca, "is_qualified": True, "qualification_reasons": {"source": "manual"}})
            db.upsert_token({"ca": ca, "is_qualified": False, "is_dex_paid": True,
                             "qualification_reasons": {"source": "live"}})
            with db.get_connection() as conn:
                row = conn.execute("SELECT is_qualified, qualification_reasons FROM tokens WHERE ca=?", (ca,)).fetchone()
            self.assertEqual(row[0], 1)
            self.assertIn("manual", row[1])

    def test_address_validation_and_topic_decode(self):
        ca = "0x" + "a" * 40
        self.assertEqual(validate_address(ca.upper().replace("0X", "0x")), ca)
        self.assertEqual(topic_address("0x" + "0" * 24 + "a" * 40), ca)
        self.assertIsNone(topic_address("0x" + "0" * 64))
        with self.assertRaises(ValueError):
            validate_address("not-an-address")

    def test_live_discovery_never_auto_qualifies_training_anchor(self):
        with tempfile.TemporaryDirectory() as directory:
            db = ForensicDatabase(str(Path(directory) / "collector.db"))
            ca = "0x" + "2" * 40
            profile = {
                "ca": ca, "token_name": "Test", "token_symbol": "TST",
                "bytecode_sha256": "a" * 64, "normalized_bytecode_sha256": "b" * 64,
                "deployer_address": "0x" + "3" * 40,
                "creation_tx_hash": "0x" + "4" * 64,
                "function_selectors": "a9059cbb", "method_ids_hash": "deadbeef",
                "funding_lineage": [], "hardcoded_addresses": [],
                "storage_address_candidates": [],
            }
            job = {"ca": ca, "chain_id": 4663, "source": "dex_boost_latest", "attempts": 1}
            with patch("streamer.extract_full_token_metadata", return_value=profile), \
                 patch("streamer.fetch_market_profile", return_value={}):
                ingest_and_enrich_job(db, job)
            with db.get_connection() as conn:
                row = conn.execute("SELECT is_qualified, is_dex_paid FROM tokens WHERE ca=?", (ca,)).fetchone()
            self.assertEqual(row[0], 0)
            self.assertEqual(row[1], 1)


if __name__ == "__main__":
    unittest.main()
