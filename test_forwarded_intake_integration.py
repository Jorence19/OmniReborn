import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import streamer
from database import ForensicDatabase
from sources import is_source_enabled, set_source_switch
from streamer import QueueStore, ingest_and_enrich_job
from telegram_bot import BotService, Settings, ensure_schema, queue_forwarded_notice


class ForwardedIntakeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.db_path = root / "forensics.db"
        ForensicDatabase(str(self.db_path))
        self.settings = Settings(
            token="test", chats=(-1001,), db=self.db_path,
            snapshot=root / "snapshot.json", report=root / "report.json",
            csv=root / "candidates.csv", dashboard=root / "index.html",
            runtime=root, health=root / "health.json", state=root / "state.json",
            lock=root / "bot.lock", push_alerts=False,
        )
        ensure_schema(self.db_path)

    def tearDown(self):
        self.temp.cleanup()

    def test_pons_notice_is_audited_and_queued_for_robinhood(self):
        ca = "0x35d9123d4fa11ee93e350c59c7ef70741714374a"
        message = {
            "message_id": 42,
            "text": "/ingest",
            "reply_to_message": {
                "text": "Pons Uniswap Migration "
                f"https://gmgn.ai/robinhood/token/ref_{ca}",
            },
        }
        result = queue_forwarded_notice(self.settings, -1001, message)
        self.assertEqual([item.ca for item in result["queued"]], [ca])
        self.assertEqual(result["held"], [])
        connection = sqlite3.connect(self.db_path)
        try:
            intake = connection.execute(
                "SELECT chain_id,source_kind,status FROM forwarded_token_intake"
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(intake, (4663, "forwarded_pons_migration", "queued"))
        job = QueueStore(ForensicDatabase(str(self.db_path))).claim("test", limit=1)[0]
        self.assertEqual((job["ca"], job["chain_id"], job["source"]),
                         (ca, 4663, "forwarded_pons_migration"))

    def test_multi_address_intake_queues_only_ca_with_dev_in_payload(self):
        token_ca = "0x1111111111111111111111111111111111111111"
        dev_ca = "0x2222222222222222222222222222222222222222"
        pair_ca = "0x3333333333333333333333333333333333333333"
        message = {
            "message_id": 43,
            "text": "/ingest",
            "reply_to_message": {
                "text": (
                    "Pons Migration Alert!\n"
                    f"Token: {token_ca}\n"
                    f"Dev: {dev_ca}\n"
                    f"Pair: {pair_ca}\n"
                    "$AGENT on Robinhood"
                ),
            },
        }
        result = queue_forwarded_notice(self.settings, -1001, message)
        self.assertEqual([item.ca for item in result["queued"]], [token_ca])
        self.assertEqual(result["held"], [])
        job = QueueStore(ForensicDatabase(str(self.db_path))).claim("test", limit=1)[0]
        payload = json.loads(job["source_payload"])
        self.assertEqual(payload["dev_wallet"], dev_ca)
        self.assertEqual(payload["pair_address"], pair_ca)
        self.assertEqual(payload["symbol"], "AGENT")

    def test_disabled_source_switch_holds_intake(self):
        ca = "0x35d9123d4fa11ee93e350c59c7ef70741714374a"
        set_source_switch("pons_forward", False, runtime_dir=self.settings.runtime)
        message = {
            "message_id": 44,
            "text": "/ingest",
            "reply_to_message": {
                "text": f"Pons Uniswap Migration https://gmgn.ai/robinhood/token/ref_{ca}",
            },
        }
        result = queue_forwarded_notice(self.settings, -1001, message)
        self.assertEqual(result["queued"], [])
        self.assertEqual([item.ca for item in result["held"]], [ca])

    def test_authorized_forward_is_ingested_without_a_second_listener(self):
        ca = "0x2e799bda738df565fdedf4081659fad67ec4501b"
        api = MagicMock()
        service = BotService(self.settings, api=api)
        service.handle_update({
            "message": {
                "message_id": 47,
                "chat": {"id": -1001},
                "forward_origin": {"type": "channel"},
                "text": (
                    "Pons Uniswap Migration\nHOP | Hoodhop\n"
                    f"https://gmgn.ai/robinhood/token/lZZ6fdDe_{ca}"
                ),
            }
        })
        job = QueueStore(ForensicDatabase(str(self.db_path))).claim("test", limit=1)[0]
        self.assertEqual((job["ca"], job["source"]), (ca, "forwarded_pons_migration"))
        api.message.assert_called()
    def test_telegram_sources_command_displays_and_toggles(self):
        api = MagicMock()
        service = BotService(self.settings, api=api)
        service.command(-1001, "/sources")
        api.message.assert_called()
        msg_text = api.message.call_args[0][1]
        self.assertIn("Pons Forward Intake", msg_text)

        service.command(-1001, "/sources rbh_rpc_scan on")
        self.assertTrue(is_source_enabled("rbh_rpc_scan", runtime_dir=self.settings.runtime))

        service.command(-1001, "/sources rbh_rpc_scan off")
        self.assertFalse(is_source_enabled("rbh_rpc_scan", runtime_dir=self.settings.runtime))

    def test_telegram_sources_inline_button_callback_is_authorized_and_persistent(self):
        api = MagicMock()
        service = BotService(self.settings, api=api)
        service.handle_update({
            "callback_query": {
                "id": "callback-1",
                "data": "source:pons_forward:off",
                "message": {"chat": {"id": -1001}},
            }
        })
        self.assertFalse(is_source_enabled("pons_forward", runtime_dir=self.settings.runtime))
        api.answer_callback.assert_called_once_with("callback-1", "Source updated")
        self.assertIn("reply_markup", api.message.call_args.kwargs)
    def test_bsc_notice_is_held_without_enqueuing_unsupported_chain(self):
        ca = "0x0dac078a7511c3587dc3aad2c0991950093d7777"
        result = queue_forwarded_notice(
            self.settings, -1001,
            {"message_id": 45, "text": f"NEW TOKEN MIGRATION https://bscscan.com/token/{ca}"},
        )
        self.assertEqual(result["queued"], [])
        self.assertEqual([item.ca for item in result["held"]], [ca])
        self.assertEqual(QueueStore(ForensicDatabase(str(self.db_path))).stats()["pending"], 0)

    def test_dev_only_notice_is_audited_without_a_token_job(self):
        dev_ca = "0x2222222222222222222222222222222222222222"
        result = queue_forwarded_notice(
            self.settings, -1001,
            {"message_id": 46, "text": f"Pons Robinhood Dev: {dev_ca}"},
        )
        self.assertEqual(result["queued"], [])
        self.assertEqual([item.ca for item in result["dev_seeds"]], [dev_ca])
        self.assertEqual(QueueStore(ForensicDatabase(str(self.db_path))).stats()["pending"], 0)
        connection = sqlite3.connect(self.db_path)
        try:
            status = connection.execute(
                "SELECT status FROM forwarded_token_intake WHERE ca=?", (dev_ca,)
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(status, "dev_seed")
    def test_forwarded_pons_token_still_needs_chain_evidence_and_full_profile(self):
        ca = "0x" + "2" * 40
        log = {"blockNumber": "0x64", "transactionHash": "0x" + "b" * 64}
        job = {
            "ca": ca, "chain_id": 4663, "source": "forwarded_pons_migration",
            "source_payload": json.dumps({"forwarded_at": streamer.iso_utc(), "metadata": {"reported_tax_percent": 2.0, "reported_age_seconds": 59}}),
        }
        profile = {
            "ca": ca, "token_name": "Forwarded", "token_symbol": "FWD",
            "bytecode_sha256": "a" * 64, "normalized_bytecode_sha256": "b" * 64,
            "deployer_address": "0x" + "3" * 40,
            "creation_tx_hash": "0x" + "4" * 64,
            "function_selectors": "a9059cbb", "method_ids_hash": "deadbeef",
            "funding_lineage": [], "hardcoded_addresses": [], "storage_address_candidates": [],
        }
        pair = {"pairAddress": "0x" + "a" * 40, "pairCreatedAt": 1_000_000_000,
                "baseToken": {"address": ca, "symbol": "FWD", "name": "Forwarded"}}
        with patch("streamer.find_forwarded_rbh_migration", return_value=log), \
             patch("streamer.block_timestamp", return_value=1_000_000), \
             patch("streamer.rbh_migration_tokens", return_value={ca: {"router": "test"}}), \
             patch("streamer.fetch_market_profile", return_value=pair), \
             patch("streamer.extract_full_token_metadata", return_value=profile) as extractor:
            ingest_and_enrich_job(ForensicDatabase(str(self.db_path)), job)
        extractor.assert_called_once_with(ca, chain_id=4663)
        connection = sqlite3.connect(self.db_path)
        try:
            evidence = json.loads(connection.execute(
                "SELECT graduation_evidence FROM tokens WHERE ca=?", (ca,)
            ).fetchone()[0])
        finally:
            connection.close()
        self.assertEqual(evidence["source_observations"]["reported_tax_percent"], 2.0)
        self.assertEqual(evidence["source_observations"]["reported_age_seconds"], 59)

    def test_forwarded_only_does_not_call_api_discovery(self):
        db = ForensicDatabase(str(self.db_path))
        store = QueueStore(db)
        with patch.dict("os.environ", {"DISCOVERY_MODE": "forwarded_only"}), \
             patch("streamer.discover_once") as discover, \
             patch("streamer.process_queue", return_value={"succeeded": 0, "failed": 0, "skipped": 0, "claimed": 0}):
            result = streamer.run_cycle(db, store, max_jobs=1)
        discover.assert_not_called()
        self.assertEqual(result["discovery"]["mode"], "forwarded_only")


if __name__ == "__main__":
    unittest.main()
