"""Unit tests for granular source switches."""
import os
import tempfile
import unittest
from pathlib import Path

from sources import (
    SOURCE_DEFINITIONS,
    default_for_source,
    is_source_enabled,
    load_source_switches,
    normalize_source_name,
    set_source_switch,
)


class SourcesTests(unittest.TestCase):
    def test_default_switches_in_forwarded_only_mode(self):
        env = {"DISCOVERY_MODE": "forwarded_only"}
        switches = load_source_switches(env=env)
        self.assertTrue(switches["pons_forward"])
        self.assertTrue(switches["arc_forward"])
        self.assertFalse(switches["bsc_forward"])
        self.assertFalse(switches["rbh_rpc_scan"])
        self.assertFalse(switches["dexscreener_scan"])

    def test_default_switches_in_hybrid_mode(self):
        env = {"DISCOVERY_MODE": "hybrid"}
        switches = load_source_switches(env=env)
        self.assertTrue(switches["pons_forward"])
        self.assertTrue(switches["arc_forward"])
        self.assertTrue(switches["rbh_rpc_scan"])
        self.assertTrue(switches["dexscreener_scan"])

    def test_explicit_env_overrides(self):
        env = {
            "DISCOVERY_MODE": "forwarded_only",
            "SOURCE_RBH_RPC_SCAN": "true",
            "SOURCE_PONS_FORWARD": "false",
        }
        self.assertTrue(default_for_source("rbh_rpc_scan", env=env))
        self.assertFalse(default_for_source("pons_forward", env=env))

    def test_runtime_file_override_and_persistence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime = Path(tmpdir)
            self.assertFalse(is_source_enabled("rbh_rpc_scan", runtime_dir=runtime, env={"DISCOVERY_MODE": "forwarded_only"}))
            set_source_switch("rbh_rpc_scan", True, runtime_dir=runtime)
            self.assertTrue(is_source_enabled("rbh_rpc_scan", runtime_dir=runtime, env={"DISCOVERY_MODE": "forwarded_only"}))
            set_source_switch("rpc", False, runtime_dir=runtime)
            self.assertFalse(is_source_enabled("rbh_rpc_scan", runtime_dir=runtime, env={"DISCOVERY_MODE": "forwarded_only"}))

    def test_aliases(self):
        self.assertEqual(normalize_source_name("pons"), "pons_forward")
        self.assertEqual(normalize_source_name("arc"), "arc_forward")
        self.assertEqual(normalize_source_name("rpc"), "rbh_rpc_scan")
        self.assertEqual(normalize_source_name("dex"), "dexscreener_scan")


if __name__ == "__main__":
    unittest.main()
