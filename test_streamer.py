import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import streamer
from database import ForensicDatabase
from streamer import QueueStore, ingest_and_enrich_job, market_fields, topic_address, validate_address


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

    def test_same_address_on_two_chains_has_independent_queue_jobs(self):
        self.store.enqueue(self.ca, "rbh", chain_id=4663)
        self.store.enqueue(self.ca, "arc", chain_id=5042)
        self.assertEqual(self.store.stats()["pending"], 2)
        jobs = self.store.claim("worker", limit=5)
        self.assertEqual({job["chain_id"] for job in jobs}, {4663, 5042})

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
            db.upsert_token({"ca": ca, "is_qualified": True, "is_training_anchor": True,
                             "qualification_reasons": {"source": "manual"}})
            db.upsert_token({"ca": ca, "is_qualified": True, "is_training_anchor": False,
                             "is_dex_paid": True,
                             "qualification_reasons": {"source": "live"}})
            with db.get_connection() as conn:
                row = conn.execute(
                    "SELECT is_qualified, is_training_anchor, qualification_reasons FROM tokens WHERE ca=?",
                    (ca,),
                ).fetchone()
            self.assertEqual(tuple(row[:2]), (1, 1))
            self.assertIn("manual", row[2])

    def test_token_registry_fails_closed_on_cross_chain_address_collision(self):
        with tempfile.TemporaryDirectory() as directory:
            db = ForensicDatabase(str(Path(directory) / "collector.db"))
            ca = "0x" + "8" * 40
            db.upsert_token({"ca": ca, "chain_id": 4663, "symbol": "RBH"})
            with self.assertRaisesRegex(ValueError, "cross-chain address collisions"):
                db.upsert_token({"ca": ca, "chain_id": 5042, "symbol": "ARC"})
            with db.get_connection() as conn:
                row = conn.execute("SELECT chain_id, symbol FROM tokens WHERE ca=?", (ca,)).fetchone()
            self.assertEqual(tuple(row), (4663, "RBH"))

    def test_arc_paid_ingest_qualifies_without_becoming_training_anchor(self):
        with tempfile.TemporaryDirectory() as directory:
            db = ForensicDatabase(str(Path(directory) / "collector.db"))
            ca = "0x" + "7" * 40
            profile = {
                "ca": ca, "chain_id": 5042, "token_name": "Arc Test", "token_symbol": "ARCX",
                "bytecode_sha256": "a" * 64, "normalized_bytecode_sha256": "b" * 64,
                "deployer_address": "0x" + "3" * 40,
                "creation_tx_hash": "0x" + "4" * 64,
                "function_selectors": "a9059cbb", "method_ids_hash": "deadbeef",
                "funding_lineage": [], "hardcoded_addresses": [],
                "storage_address_candidates": [],
            }
            job = {"ca": ca, "chain_id": 5042, "source": "dex_boost_latest", "attempts": 1}
            with patch("streamer.extract_full_token_metadata", return_value=profile) as extractor, \
                 patch("streamer.fetch_market_profile", return_value={}) as market:
                result = ingest_and_enrich_job(db, job)
            extractor.assert_called_once_with(ca, chain_id=5042)
            market.assert_called_once_with(ca, 5042)
            self.assertEqual((result["chain_id"], result["chain"]), (5042, "ARC"))
            with db.get_connection() as conn:
                row = conn.execute(
                    "SELECT chain_id, chain, is_qualified, is_training_anchor, is_dex_paid "
                    "FROM tokens WHERE ca=?", (ca,)
                ).fetchone()
            self.assertEqual(tuple(row), (5042, "ARC", 1, 0, 1))

    def test_current_market_cap_is_never_written_as_ath(self):
        fields = market_fields({
            "baseToken": {"symbol": "LIVE", "name": "Live Token"},
            "marketCap": 1234,
            "fdv": 1500,
            "liquidity": {"usd": 800},
            "pairCreatedAt": 1789305867000,
            "url": "https://dexscreener.com/robinhood/pair",
        })
        self.assertNotIn("ath_usd", fields)
        self.assertEqual(fields["current_market_cap_usd"], 1234)
        self.assertEqual(fields["fdv_usd"], 1500)
        self.assertEqual(fields["current_liquidity_usd"], 800)

    def test_sourced_ath_and_current_market_cap_are_independent(self):
        with tempfile.TemporaryDirectory() as directory:
            db = ForensicDatabase(str(Path(directory) / "collector.db"))
            ca = "0x" + "6" * 40
            db.upsert_token({
                "ca": ca, "ath_usd": 25000, "ath_source": "tgscan_archive",
            })
            db.upsert_token({
                "ca": ca, "current_market_cap_usd": 3200,
                "observed_peak_market_cap_usd": 3200,
            })
            with db.get_connection() as conn:
                row = conn.execute(
                    "SELECT ath_usd,ath_source,current_market_cap_usd,"
                    "observed_peak_market_cap_usd FROM tokens WHERE ca=?", (ca,)
                ).fetchone()
            self.assertEqual(tuple(row), (25000, "tgscan_archive", 3200, 3200))

    def test_address_validation_and_topic_decode(self):
        ca = "0x" + "a" * 40
        self.assertEqual(validate_address(ca.upper().replace("0X", "0x")), ca)
        self.assertEqual(topic_address("0x" + "0" * 24 + "a" * 40), ca)
        self.assertIsNone(topic_address("0x" + "0" * 64))
        with self.assertRaises(ValueError):
            validate_address("not-an-address")

    def test_live_discovery_qualifies_but_never_auto_creates_training_anchor(self):
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
                row = conn.execute(
                    "SELECT is_qualified, is_training_anchor, is_dex_paid FROM tokens WHERE ca=?",
                    (ca,),
                ).fetchone()
            self.assertEqual(tuple(row), (1, 0, 1))

    def test_search_backfill_is_not_misclassified_as_paid_or_qualified(self):
        with tempfile.TemporaryDirectory() as directory:
            db = ForensicDatabase(str(Path(directory) / "collector.db"))
            ca = "0x" + "5" * 40
            profile = {
                "ca": ca, "chain_id": 5042, "token_name": "Backfill", "token_symbol": "OLD",
                "bytecode_sha256": "a" * 64, "normalized_bytecode_sha256": "b" * 64,
                "deployer_address": "0x" + "3" * 40,
                "creation_tx_hash": "0x" + "4" * 64,
                "function_selectors": "a9059cbb", "method_ids_hash": "deadbeef",
                "funding_lineage": [], "hardcoded_addresses": [],
                "storage_address_candidates": [],
            }
            job = {"ca": ca, "chain_id": 5042, "source": "dex_search_backfill", "attempts": 1}
            with patch("streamer.extract_full_token_metadata", return_value=profile), \
                 patch("streamer.fetch_market_profile", return_value={}):
                ingest_and_enrich_job(db, job)
            with db.get_connection() as conn:
                row = conn.execute(
                    "SELECT is_qualified, is_training_anchor, is_dex_paid FROM tokens WHERE ca=?",
                    (ca,),
                ).fetchone()
            self.assertEqual(tuple(row), (0, 0, 0))

    def test_discover_once_does_not_crash_on_chain_scan_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            db = ForensicDatabase(str(Path(directory) / "collector.db"))
            store = QueueStore(db)
            mock_dex = [{"ca": "0x" + "3" * 40, "chain_id": 4663, "sources": ["dex_live"]}]
            with patch("streamer.fetch_live_dexpaid_tokens", return_value=mock_dex), \
                 patch("streamer.scan_uniswap_v4_initialize", side_effect=RuntimeError("RPC 413")):
                res = streamer.discover_once(store, include_chain=True)
            self.assertEqual(res["dex_tokens"], 1)
            self.assertIn("error", res["robinhood_v4"])
            self.assertEqual(store.stats()["pending"], 1)

    def test_rpc_strict_includes_response_snippet(self):
        class MockResp:
            status_code = 413
            text = "Payload Too Large: max limit 2MB"
        with patch.object(streamer.HTTP, "post", return_value=MockResp()):
            with self.assertRaises(streamer.CollectorError) as ctx:
                streamer.rpc_call_strict("https://example.com", "eth_getLogs", [])
            self.assertIn("413", str(ctx.exception))
            self.assertIn("Payload Too Large", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
