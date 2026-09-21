import hashlib
import tempfile
import unittest
from pathlib import Path

from cluster import build_feature_counts, calculate_pair_similarity
from database import ForensicDatabase
from forensics import (
    compute_bytecode_hashes,
    compute_normalized_bytecode_hash,
    extract_compiler_version,
    extract_function_selectors,
    decode_creation_bytecode_args,
    strip_solidity_metadata,
)
from phase1 import build_report, calculate_nearest_duplicate, discover_fingerprints


class BytecodeTests(unittest.TestCase):
    def test_hashes_actual_bytes(self):
        sha, md5 = compute_bytecode_hashes("0x60016000")
        raw = bytes.fromhex("60016000")
        self.assertEqual(sha, hashlib.sha256(raw).hexdigest())
        self.assertEqual(md5, hashlib.md5(raw).hexdigest())

    def test_solidity_metadata_is_removed_and_version_read(self):
        executable = bytes.fromhex("60016000")
        metadata = bytes.fromhex("a164736f6c6343000817")
        bytecode = "0x" + (executable + metadata + len(metadata).to_bytes(2, "big")).hex()
        stripped, found_metadata = strip_solidity_metadata(bytecode)
        self.assertEqual(stripped, executable)
        self.assertEqual(found_metadata, metadata)
        self.assertEqual(extract_compiler_version(bytecode), "solc 0.8.23")
        self.assertEqual(compute_normalized_bytecode_hash(bytecode), hashlib.sha256(executable).hexdigest())

    def test_selector_parser_ignores_push4_data_without_eq(self):
        _, selectors = extract_function_selectors("0x63a9059cbb1460015763deadbeef6000")
        self.assertEqual(selectors, "a9059cbb")

    def test_constructor_urls_are_bounded(self):
        payload = b"\x00" * 64 + b"ipfs://bafybeigdyrzt5sfp7udm7hu76uh7y26nf3ad7z5t4k3k6f5w7h6q5q" + b"\x00" + b"https://x.com/example" + b"\x00"
        result = decode_creation_bytecode_args("0x" + payload.hex())
        self.assertTrue(result["ipfs_metadata"].startswith("ipfs://bafy"))
        self.assertEqual(result["twitter"], "https://x.com/example")


class SimilarityTests(unittest.TestCase):
    def test_missing_zero_defaults_do_not_score(self):
        left = {"priority_gwei": 0, "nonce": 0, "value_eth": 0}
        right = {"priority_gwei": 0, "nonce": 0, "value_eth": 0}
        score, reasons = calculate_pair_similarity(left, right)
        self.assertEqual(score, 0)
        self.assertEqual(reasons["evidence_count"], 0)

    def test_same_developer_is_strong_identity(self):
        left = {"dev_wallet": "0xabc", "template_hash": "shared"}
        right = {"dev_wallet": "0xABC", "template_hash": "shared"}
        counts = build_feature_counts([left, right])
        score, reasons = calculate_pair_similarity(left, right, counts, 2)
        self.assertGreaterEqual(score, 70)
        self.assertIn("dev_wallet_match", reasons["identity_evidence"])

    def test_common_template_is_only_weak_evidence(self):
        rows = [{"template_hash": "launchpad-default"} for _ in range(100)]
        counts = build_feature_counts(rows)
        score, reasons = calculate_pair_similarity(rows[0], rows[1], counts, len(rows))
        self.assertLessEqual(score, 3)
        self.assertFalse(reasons["identity_evidence"])


class CrossChainScoringTests(unittest.TestCase):
    def test_native_amounts_never_match_between_rbh_and_arc(self):
        candidate = {
            "chain_id": 5042, "value_eth": 0.005, "gwei": 1.0,
            "creation_tx_fee_eth": 0.01, "nonce": 4,
        }
        anchor = {
            "chain_id": 4663, "value_eth": 0.005, "gwei": 1.0,
            "creation_tx_fee_eth": 0.01, "nonce": 4,
        }
        _, evidence = calculate_nearest_duplicate(candidate, anchor)
        self.assertEqual([item["feature"] for item in evidence], ["nonce"])


class Phase1ReportTests(unittest.TestCase):
    def test_catalog_surfaces_repeated_team_habit(self):
        rows = [
            {"ca": "a", "symbol": "A", "candidate_team": "team one", "funder_1hop": "0xf", "value_eth": 0.01005},
            {"ca": "b", "symbol": "B", "candidate_team": "team one", "funder_1hop": "0xf", "value_eth": 0.01005},
            {"ca": "c", "symbol": "C", "candidate_team": "team two", "funder_1hop": "0xg", "value_eth": 0.2},
        ]
        catalog = discover_fingerprints(rows)
        match = next(item for item in catalog if item["feature"] == "funder_1hop")
        self.assertEqual(match["leading_team"], "team one")
        self.assertEqual(match["team_purity_pct"], 100.0)

    def test_schema_supports_qualified_report(self):
        with tempfile.TemporaryDirectory() as directory:
            db = ForensicDatabase(str(Path(directory) / "test.db"))
            db.upsert_token({
                "ca": "0x" + "1" * 40, "symbol": "ONE",
                "is_qualified": True, "is_training_anchor": True,
            })
            report = build_report(db, qualified_only=True)
            self.assertEqual(report["token_count"], 1)


if __name__ == "__main__":
    unittest.main()
