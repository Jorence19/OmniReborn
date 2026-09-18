import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from api_server import ApiSettings, SnapshotStore, create_app, readiness_checks


class CandidateApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.snapshot = Path(self.temp.name) / "dashboard_candidates.json"
        self.snapshot.write_text(json.dumps({
            "schema_version": 1,
            "generated_at": "2026-09-18T00:00:00+00:00",
            "candidates": [{
                "ca": "0x" + "a" * 40,
                "symbol": "<script>alert(1)</script>",
                "name": "Candidate",
                "confidence": "HIGH_LEAD",
                "score": 91.2,
                "team": "team astro",
                "best_match_symbol": "ANCHOR",
                "best_match_ca": "0x" + "b" * 40,
                "ath": 10000,
                "is_rug": False,
                "evidence": [{"feature": "funder_1hop", "value": "0xabc", "reliability": .85}],
            }],
        }), encoding="utf-8")
        self.settings = ApiSettings(
            snapshot_path=self.snapshot,
            allowed_origins=("https://tracker.example.org",),
            allowed_hosts=("api.example.org", "testserver"),
            max_snapshot_age_seconds=3600,
        )
        self.client = TestClient(create_app(self.settings))

    def tearDown(self):
        self.temp.cleanup()

    def test_candidates_and_cors(self):
        response = self.client.get("/api/candidates", headers={"Origin": "https://tracker.example.org"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 1)
        self.assertEqual(response.headers["access-control-allow-origin"], "https://tracker.example.org")
        self.assertIn("etag", response.headers)

    def test_etag_returns_not_modified(self):
        first = self.client.get("/api/candidates")
        second = self.client.get("/api/candidates", headers={"If-None-Match": first.headers["etag"]})
        self.assertEqual(second.status_code, 304)

    def test_host_guard_and_no_docs(self):
        self.assertEqual(self.client.get("/api/live", headers={"Host": "attacker.invalid"}).status_code, 400)
        self.assertEqual(self.client.get("/docs").status_code, 404)

    def test_invalid_candidate_is_dropped(self):
        self.snapshot.write_text(json.dumps({
            "generated_at": "2026-09-18T00:00:00+00:00",
            "candidates": [{"ca": "not-an-address"}],
        }), encoding="utf-8")
        response = self.client.get("/api/candidates")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 0)

    def test_readiness_rejects_wildcards(self):
        unsafe = ApiSettings(self.snapshot, ("*",), ("*",), 3600)
        errors = readiness_checks(unsafe, SnapshotStore(unsafe))
        self.assertTrue(any("unsafe API_ALLOWED_ORIGINS" in error for error in errors))
        self.assertTrue(any("unsafe API_ALLOWED_HOSTS" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
