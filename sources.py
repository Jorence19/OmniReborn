"""Granular source switches for OmniReborn intake and discovery."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

TRUTHY = {"1", "true", "yes", "on"}

SOURCE_DEFINITIONS: dict[str, dict[str, Any]] = {
    "pons_forward": {
        "label": "Pons Forward Intake",
        "chain_id": 4663,
        "description": "Forwarded Robinhood Pons migration notices in Telegram",
        "default": True,
        "category": "forward",
    },
    "arc_forward": {
        "label": "Arc Forward Intake",
        "chain_id": 5042,
        "description": "Forwarded Arc token launch notices in Telegram",
        "default": True,
        "category": "forward",
    },
    "bsc_forward": {
        "label": "BSC Intake",
        "chain_id": 56,
        "description": "Forwarded BSC notices (retained as held by default)",
        "default": False,
        "category": "forward",
    },
    "rbh_rpc_scan": {
        "label": "Robinhood RPC Scanner",
        "chain_id": 4663,
        "description": "Background RPC polling for Uniswap v4 Initialize events",
        "default": False,
        "category": "scanner",
    },
    "dexscreener_scan": {
        "label": "DexScreener Discovery",
        "chain_id": None,
        "description": "Background polling of DexScreener token-boost / new profiles",
        "default": False,
        "category": "scanner",
    },
}

SOURCE_ALIASES = {
    "pons": "pons_forward",
    "pons_forward": "pons_forward",
    "arc": "arc_forward",
    "arc_forward": "arc_forward",
    "bsc": "bsc_forward",
    "bsc_forward": "bsc_forward",
    "rpc": "rbh_rpc_scan",
    "rbh_rpc": "rbh_rpc_scan",
    "rbh_rpc_scan": "rbh_rpc_scan",
    "dex": "dexscreener_scan",
    "dexscreener": "dexscreener_scan",
    "dexscreener_scan": "dexscreener_scan",
}


def normalize_source_name(name: str) -> str | None:
    return SOURCE_ALIASES.get(str(name or "").strip().lower().replace("-", "_"))


def default_for_source(source_key: str, env: Mapping[str, str] | None = None) -> bool:
    env_map = os.environ if env is None else env
    env_key = f"SOURCE_{source_key.upper()}"
    if env_key in env_map:
        return env_map[env_key].strip().lower() in TRUTHY

    mode = env_map.get("DISCOVERY_MODE", "forwarded_only").strip().lower()
    if source_key in {"rbh_rpc_scan", "dexscreener_scan"}:
        return mode == "hybrid"

    definition = SOURCE_DEFINITIONS.get(source_key)
    return bool(definition.get("default", False)) if definition else False


def switches_file_path(runtime_dir: Path | str | None = None) -> Path:
    if runtime_dir is not None:
        return Path(runtime_dir).resolve() / "source_switches.json"
    env_runtime = os.getenv("RUNTIME_DIR")
    if env_runtime:
        return Path(env_runtime).resolve() / "source_switches.json"
    return Path(__file__).resolve().parent / "runtime" / "source_switches.json"


def load_source_switches(
    runtime_dir: Path | str | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, bool]:
    result: dict[str, bool] = {
        key: default_for_source(key, env=env)
        for key in SOURCE_DEFINITIONS
    }

    switches_path = switches_file_path(runtime_dir)
    if switches_path.exists():
        try:
            overrides = json.loads(switches_path.read_text(encoding="utf-8"))
            if isinstance(overrides, dict):
                for key, val in overrides.items():
                    normalized = normalize_source_name(key)
                    if normalized and isinstance(val, bool):
                        result[normalized] = val
        except (OSError, ValueError):
            pass

    return result


def set_source_switch(
    source_key: str,
    enabled: bool,
    runtime_dir: Path | str | None = None,
) -> bool:
    normalized = normalize_source_name(source_key)
    if not normalized:
        raise ValueError(f"Unknown source: {source_key!r}")

    switches_path = switches_file_path(runtime_dir)
    switches_path.parent.mkdir(parents=True, exist_ok=True)

    overrides: dict[str, bool] = {}
    if switches_path.exists():
        try:
            data = json.loads(switches_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                overrides = {str(k): bool(v) for k, v in data.items()}
        except (OSError, ValueError):
            overrides = {}

    overrides[normalized] = bool(enabled)
    temp_path = switches_path.with_suffix(".tmp")
    temp_path.write_text(json.dumps(overrides, indent=2), encoding="utf-8")
    temp_path.replace(switches_path)
    return True


def is_source_enabled(
    source_key: str,
    runtime_dir: Path | str | None = None,
    env: Mapping[str, str] | None = None,
) -> bool:
    normalized = normalize_source_name(source_key)
    if not normalized:
        return False
    switches = load_source_switches(runtime_dir=runtime_dir, env=env)
    return switches.get(normalized, False)
