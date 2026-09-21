"""Crash-safe Robinhood Chain discovery, enrichment, and dashboard worker."""
import argparse
import concurrent.futures
import json
import logging
import logging.handlers
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import uuid
from contextlib import AbstractContextManager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from branding_scraper import BrandingScraper
from config import CHAIN_METADATA, RPC_ENDPOINTS, ROBIN_ETHERSCAN_API_KEY
from database import ForensicDatabase
from forensics import extract_full_token_metadata
from phase1 import build_report, persist_profile, write_report_artifacts

APP_DIR = Path(__file__).resolve().parent
RUNTIME_DIR = Path(os.getenv("RUNTIME_DIR", APP_DIR / "runtime")).resolve()
DB_PATH = Path(os.getenv("FORENSICS_DB_PATH", APP_DIR / "forensics.db")).resolve()
OUTPUT_DIR = Path(os.getenv("DASHBOARD_OUTPUT_DIR", APP_DIR)).resolve()
REPORT_OUTPUT_PATH = Path(os.getenv(
    "REPORT_OUTPUT_PATH", APP_DIR / "phase1_fingerprint_report.json"
)).resolve()
CHAIN_ID = 4663  # Robinhood PoolManager scanner; other chains use market discovery.
POOL_MANAGER = os.getenv("ROBINHOOD_POOL_MANAGER", "0x8366a39cc670b4001a1121b8f6a443a643e40951").lower()
INITIALIZE_TOPIC0 = "0xdd466e674ea557f56295e2d0218a125ea4b4f0f6f3307b95f85e6110838d6438"
try:
    ENABLED_CHAIN_IDS = tuple(dict.fromkeys(
        int(value.strip()) for value in os.getenv("ENABLED_CHAIN_IDS", "4663,5042").split(",")
        if value.strip()
    ))
except ValueError as exc:
    raise RuntimeError("ENABLED_CHAIN_IDS must be comma-separated integers") from exc
unsupported_chains = [chain_id for chain_id in ENABLED_CHAIN_IDS if chain_id not in CHAIN_METADATA]
if unsupported_chains:
    raise RuntimeError(f"unsupported ENABLED_CHAIN_IDS: {unsupported_chains}")
DEX_CHAIN_TO_ID = {
    str(CHAIN_METADATA[chain_id]["dex_chain_id"]).lower(): chain_id
    for chain_id in ENABLED_CHAIN_IDS
}
ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
DEX_ENDPOINTS = {
    "dex_boost_latest": "https://api.dexscreener.com/token-boosts/latest/v1",
    "dex_boost_top": "https://api.dexscreener.com/token-boosts/top/v1",
    "dex_profile_latest": "https://api.dexscreener.com/token-profiles/latest/v1",
    "dex_profile_recent": "https://api.dexscreener.com/token-profiles/recent-updates/v1",
    "dex_ads_latest": "https://api.dexscreener.com/ads/latest/v1",
}
DEX_PAID_SOURCES = frozenset(DEX_ENDPOINTS)
SEARCH_QUERIES = ("robinhood", "arc", "pons", "inu", "doge", "cat", "ai", "pepe", "trump", "eth", "coin", "token", "moon", "chad", "elon")
LOG = logging.getLogger("omnireborn.streamer")
STOP_REQUESTED = False

class CollectorError(RuntimeError):
    pass

class IncompleteProfileError(CollectorError):
    pass

def utcnow() -> datetime:
    return datetime.now(timezone.utc)

def iso_utc(value: Optional[datetime] = None) -> str:
    return (value or utcnow()).replace(microsecond=0).isoformat().replace("+00:00", "Z")

def configure_logging(verbose: bool = False):
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    LOG.setLevel(logging.DEBUG if verbose else logging.INFO)
    if LOG.handlers:
        return
    formatter = logging.Formatter("%(asctime)sZ %(levelname)s %(message)s", "%Y-%m-%dT%H:%M:%S")
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    LOG.addHandler(console)
    rotating = logging.handlers.RotatingFileHandler(RUNTIME_DIR / "streamer.log", maxBytes=10_000_000, backupCount=5, encoding="utf-8")
    rotating.setFormatter(formatter)
    LOG.addHandler(rotating)

def build_http_session() -> requests.Session:
    retry = Retry(total=5, connect=5, read=5, status=5, backoff_factor=0.8,
                  status_forcelist=(408, 425, 429, 500, 502, 503, 504),
                  allowed_methods=frozenset({"GET", "POST"}), respect_retry_after_header=True,
                  raise_on_status=False)
    session = requests.Session()
    session.headers.update({"User-Agent": "OmniReborn/1.0", "Accept": "application/json"})
    adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

HTTP = build_http_session()

def request_json(url: str, *, timeout: Tuple[float, float] = (5.0, 20.0)) -> Any:
    response = HTTP.get(url, timeout=timeout)
    if response.status_code >= 400:
        raise CollectorError(f"GET {url} returned HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError as exc:
        raise CollectorError(f"GET {url} returned invalid JSON") from exc

def rpc_call_strict(rpc_url: str, method: str, params: list) -> Any:
    response = HTTP.post(rpc_url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=(5.0, 30.0))
    if response.status_code >= 400:
        snippet = response.text[:200].strip().replace("\n", " ")
        raise CollectorError(f"RPC {method} returned HTTP {response.status_code}: {snippet}")
    payload = response.json()
    if payload.get("error"):
        raise CollectorError(f"RPC {method}: {payload['error']}")
    if "result" not in payload:
        raise CollectorError(f"RPC {method} returned no result")
    return payload["result"]

def validate_address(value: str) -> str:
    clean = str(value or "").strip().lower()
    if not ADDRESS_RE.fullmatch(clean):
        raise ValueError(f"Invalid EVM address: {value!r}")
    return clean

class SingleInstanceLock(AbstractContextManager):
    def __init__(self, path: Path):
        self.path, self.handle = path, None
    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+")
        self.handle.seek(0)
        self.handle.write("0")
        self.handle.flush()
        try:
            if os.name == "nt":
                import msvcrt
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, IOError) as exc:
            self.handle.close()
            raise CollectorError("another streamer instance already holds the lock") from exc
        self.handle.seek(0)
        self.handle.truncate()
        self.handle.write(str(os.getpid()))
        self.handle.flush()
        return self
    def __exit__(self, exc_type, exc, tb):
        if self.handle:
            try:
                if os.name == "nt":
                    import msvcrt
                    self.handle.seek(0)
                    msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            finally:
                self.handle.close()
        return False

class QueueStore:
    def __init__(self, db: ForensicDatabase):
        self.db = db
    def enqueue(self, ca: str, source: str, payload: Optional[Dict[str, Any]] = None,
                chain_id: int = CHAIN_ID, priority: int = 100, refresh_after_hours: int = 6) -> bool:
        ca = validate_address(ca)
        payload_json = json.dumps(payload or {}, sort_keys=True, default=str)
        with self.db.get_connection() as conn:
            before = conn.total_changes
            conn.execute("""
                INSERT INTO ingestion_jobs (ca, chain_id, status, source, source_payload, priority)
                VALUES (?, ?, 'pending', ?, ?, ?)
                ON CONFLICT(ca, chain_id) DO UPDATE SET
                    source=excluded.source, source_payload=excluded.source_payload,
                    priority=MIN(ingestion_jobs.priority, excluded.priority),
                    status=CASE WHEN ingestion_jobs.status='succeeded'
                         AND ingestion_jobs.completed_at <= datetime('now', ?)
                         THEN 'pending' ELSE ingestion_jobs.status END,
                    next_attempt_at=CASE WHEN ingestion_jobs.status='succeeded'
                         AND ingestion_jobs.completed_at <= datetime('now', ?)
                         THEN datetime('now') ELSE ingestion_jobs.next_attempt_at END,
                    updated_at=datetime('now')
            """, (ca, chain_id, source, payload_json, priority,
                  f"-{refresh_after_hours} hours", f"-{refresh_after_hours} hours"))
            return conn.total_changes > before
    def claim(self, worker_id: str, limit: int = 10, lease_seconds: int = 900) -> List[Dict[str, Any]]:
        with self.db.get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("""UPDATE ingestion_jobs SET status='retry', worker_id=NULL, lease_until=NULL,
                next_attempt_at=datetime('now'), last_error=COALESCE(last_error || '\n', '') || 'expired worker lease',
                updated_at=datetime('now') WHERE status='running' AND lease_until < datetime('now')""")
            rows = conn.execute("""SELECT * FROM ingestion_jobs
                WHERE status IN ('pending','retry') AND next_attempt_at <= datetime('now') AND attempts < max_attempts
                ORDER BY priority ASC, discovered_at ASC LIMIT ?""", (max(1, limit),)).fetchall()
            lease_until = (utcnow() + timedelta(seconds=lease_seconds)).strftime("%Y-%m-%d %H:%M:%S")
            result = []
            for row in rows:
                conn.execute("""UPDATE ingestion_jobs SET status='running', attempts=attempts+1,
                    worker_id=?, lease_until=?, updated_at=datetime('now')
                    WHERE ca=? AND chain_id=? AND status IN ('pending','retry')""",
                    (worker_id, lease_until, row["ca"], row["chain_id"]))
                item = dict(row)
                item["attempts"] = int(row["attempts"]) + 1
                result.append(item)
            return result
    def succeed(self, ca: str, chain_id: int):
        with self.db.get_connection() as conn:
            conn.execute("""UPDATE ingestion_jobs SET status='succeeded', lease_until=NULL, worker_id=NULL,
                last_error=NULL, completed_at=datetime('now'), updated_at=datetime('now')
                WHERE ca=? AND chain_id=?""", (ca, chain_id))
    def fail(self, job: Dict[str, Any], error: Exception):
        attempts, max_attempts = int(job.get("attempts") or 1), int(job.get("max_attempts") or 8)
        terminal = attempts >= max_attempts
        delay = min(21600, 30 * (2 ** max(0, attempts - 1)))
        next_attempt = (utcnow() + timedelta(seconds=delay)).strftime("%Y-%m-%d %H:%M:%S")
        message = f"{type(error).__name__}: {error}"[-4000:]
        with self.db.get_connection() as conn:
            conn.execute("""UPDATE ingestion_jobs SET status=?, next_attempt_at=?, lease_until=NULL,
                worker_id=NULL, last_error=?, updated_at=datetime('now') WHERE ca=? AND chain_id=?""",
                ("dead" if terminal else "retry", next_attempt, message, job["ca"], job["chain_id"]))
    def retry_dead(self) -> int:
        with self.db.get_connection() as conn:
            cursor = conn.execute("""UPDATE ingestion_jobs SET status='retry', attempts=0,
                next_attempt_at=datetime('now'), last_error=NULL, updated_at=datetime('now') WHERE status='dead'""")
            return cursor.rowcount
    def stats(self) -> Dict[str, int]:
        with self.db.get_connection() as conn:
            rows = conn.execute("SELECT status, COUNT(*) n FROM ingestion_jobs GROUP BY status").fetchall()
        result = {name: 0 for name in ("pending", "running", "retry", "succeeded", "dead")}
        result.update({row["status"]: row["n"] for row in rows})
        return result
    def get_state(self, key: str) -> Optional[str]:
        with self.db.get_connection() as conn:
            row = conn.execute("SELECT state_value FROM stream_state WHERE state_key=?", (key,)).fetchone()
            return row[0] if row else None
    def set_state(self, key: str, value: Any):
        with self.db.get_connection() as conn:
            conn.execute("""INSERT INTO stream_state(state_key, state_value) VALUES (?, ?)
                ON CONFLICT(state_key) DO UPDATE SET state_value=excluded.state_value, updated_at=datetime('now')""",
                (key, str(value)))
    def record_event(self, log: Dict[str, Any], token_ca: Optional[str], source: str,
                         chain_id: int = CHAIN_ID) -> bool:
        with self.db.get_connection() as conn:
            cursor = conn.execute("""INSERT OR IGNORE INTO chain_events
                (chain_id, tx_hash, log_index, block_number, contract_address, topic0, token_ca, source, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (chain_id, str(log.get("transactionHash") or "").lower(),
                 int(log.get("logIndex") or "0x0", 16), int(log.get("blockNumber") or "0x0", 16),
                 str(log.get("address") or "").lower(), (log.get("topics") or [None])[0], token_ca,
                 source, json.dumps(log, sort_keys=True)))
            return cursor.rowcount == 1


def merge_discovery(target: Dict[str, Dict[str, Any]], item: Dict[str, Any]):
    dex_chain_id = str(item.get("dex_chain_id") or item.get("chainId") or "").lower()
    chain_id = int(item.get("chain_id") or DEX_CHAIN_TO_ID.get(dex_chain_id) or 0)
    if chain_id not in ENABLED_CHAIN_IDS:
        return
    try:
        ca = validate_address(item.get("ca") or item.get("tokenAddress"))
    except ValueError:
        return
    key = f"{chain_id}:{ca}"
    spec = CHAIN_METADATA[chain_id]
    existing = target.setdefault(key, {
        "ca": ca, "chain_id": chain_id, "chain": spec["tag"],
        "dex_chain_id": spec["dex_chain_id"], "sources": [], "payloads": [],
    })
    source = item.get("source") or "unknown"
    if source not in existing["sources"]:
        existing["sources"].append(source)
    existing["payloads"].append(item)


def fetch_live_dexpaid_tokens() -> List[Dict[str, Any]]:
    """Collect paid surfaces for every enabled DexScreener chain."""
    merged: Dict[str, Dict[str, Any]] = {}
    failures, successes = [], 0
    for source, url in DEX_ENDPOINTS.items():
        try:
            payload = request_json(url)
            successes += 1
            for item in payload if isinstance(payload, list) else []:
                dex_chain_id = str(item.get("chainId") or "").lower()
                if dex_chain_id in DEX_CHAIN_TO_ID:
                    merge_discovery(merged, {
                        **item, "chain_id": DEX_CHAIN_TO_ID[dex_chain_id],
                        "dex_chain_id": dex_chain_id, "source": source,
                    })
        except Exception as exc:
            failures.append(f"{source}: {exc}")
            LOG.warning("Dex discovery failed: %s", failures[-1])
    if successes == 0:
        raise CollectorError("all DexScreener discovery surfaces failed: " + "; ".join(failures))
    return list(merged.values())


def backfill_historical_tokens(days: int = 10) -> List[Dict[str, Any]]:
    """Date-filtered Dex search supplement for all enabled chains."""
    cutoff_ms = int((utcnow() - timedelta(days=max(1, days))).timestamp() * 1000)
    merged: Dict[str, Dict[str, Any]] = {}
    failures = 0
    for query in SEARCH_QUERIES:
        try:
            payload = request_json(f"https://api.dexscreener.com/latest/dex/search?q={query}")
            for pair in payload.get("pairs") or []:
                dex_chain_id = str(pair.get("chainId") or "").lower()
                chain_id = DEX_CHAIN_TO_ID.get(dex_chain_id)
                if not chain_id:
                    continue
                created = int(pair.get("pairCreatedAt") or 0)
                if not created or created < cutoff_ms:
                    continue
                token = pair.get("baseToken") or {}
                merge_discovery(merged, {
                    "ca": token.get("address"), "chain_id": chain_id,
                    "dex_chain_id": dex_chain_id,
                    "symbol": token.get("symbol"), "name": token.get("name"),
                    "pairCreatedAt": created, "fdv": pair.get("fdv"),
                    "marketCap": pair.get("marketCap"), "liquidity": pair.get("liquidity"),
                    "source": "dex_search_backfill",
                })
            time.sleep(0.22)
        except Exception as exc:
            failures += 1
            LOG.warning("Dex search %r failed: %s", query, exc)
    if failures == len(SEARCH_QUERIES):
        raise CollectorError("all DexScreener historical searches failed")
    return list(merged.values())

def block_timestamp(rpc_url: str, block_number: int) -> int:
    block = rpc_call_strict(rpc_url, "eth_getBlockByNumber", [hex(block_number), False])
    if not block:
        raise CollectorError(f"block {block_number} unavailable")
    return int(block["timestamp"], 16)

def find_block_at_or_after(rpc_url: str, timestamp: int, head: int) -> int:
    low, high = 0, head
    while low < high:
        middle = (low + high) // 2
        if block_timestamp(rpc_url, middle) < timestamp:
            low = middle + 1
        else:
            high = middle
    return low

def topic_address(topic: str) -> Optional[str]:
    if not topic or len(topic) < 42:
        return None
    candidate = "0x" + topic[-40:].lower()
    if candidate == "0x" + "0" * 40:
        return None
    return candidate if ADDRESS_RE.fullmatch(candidate) else None

def scan_uniswap_v4_initialize(store: QueueStore, *, days: Optional[int] = None,
                               from_block: Optional[int] = None, confirmations: int = 20,
                               chunk_size: int = 2000, reorg_overlap: int = 50) -> Dict[str, int]:
    rpc_url = RPC_ENDPOINTS.get(CHAIN_ID)
    if not rpc_url:
        raise CollectorError(f"no RPC configured for chain {CHAIN_ID}")
    rpc_urls = [rpc_url]
    public_rpc = CHAIN_METADATA.get(CHAIN_ID, {}).get("public_rpc")
    if public_rpc and public_rpc.rstrip("/") != rpc_url.rstrip("/"):
        rpc_urls.append(public_rpc)

    head = None
    for endpoint in rpc_urls:
        try:
            head = int(rpc_call_strict(endpoint, "eth_blockNumber", []), 16)
            break
        except Exception as exc:
            LOG.warning("eth_blockNumber failed on %s: %s", endpoint, exc)
    if head is None:
        raise CollectorError(f"no working RPC endpoint for chain {CHAIN_ID}")

    safe_head = max(0, head - max(1, confirmations))
    state_key = f"uniswap_v4:{CHAIN_ID}:{POOL_MANAGER}:next_block"
    minimum_key = f"uniswap_v4:{CHAIN_ID}:{POOL_MANAGER}:min_scanned_block"
    saved = store.get_state(state_key)
    saved_minimum = store.get_state(minimum_key)
    if from_block is not None:
        cursor = max(0, from_block)
    elif days:
        cutoff = int((utcnow() - timedelta(days=max(1, days))).timestamp())
        cutoff_block = find_block_at_or_after(rpc_url, cutoff, safe_head)
        # A later backfill must be able to extend history behind an existing live cursor.
        if saved_minimum is None or cutoff_block < int(saved_minimum):
            cursor = cutoff_block
        elif saved is not None:
            cursor = max(cutoff_block, int(saved) - reorg_overlap)
        else:
            cursor = cutoff_block
    elif saved is not None:
        cursor = max(0, int(saved) - reorg_overlap)
        # During live streaming, if saved cursor is deeply stale (> 10,000 blocks behind safe_head),
        # fast-forward cursor to recent window so live polling doesn't hang or hit archive limits.
        if safe_head - cursor > 10000:
            LOG.info("Saved scan cursor %s is %s blocks behind head; advancing to safe window %s",
                     cursor, safe_head - cursor, safe_head - 2000)
            cursor = max(0, safe_head - 2000)
    else:
        cursor = max(0, safe_head - reorg_overlap)
    if cursor > safe_head:
        return {"from_block": cursor, "to_block": safe_head, "logs": 0, "tokens": 0}
    original_start, event_count, token_count = cursor, 0, 0
    current_chunk = max(100, chunk_size)
    while cursor <= safe_head and not STOP_REQUESTED:
        end = min(safe_head, cursor + current_chunk - 1)
        logs = None
        for endpoint in rpc_urls:
            try:
                logs = rpc_call_strict(endpoint, "eth_getLogs", [{
                    "fromBlock": hex(cursor), "toBlock": hex(end), "address": POOL_MANAGER,
                    "topics": [INITIALIZE_TOPIC0],
                }]) or []
                break
            except Exception as exc:
                if endpoint != rpc_urls[-1]:
                    LOG.warning("eth_getLogs failed on %s (%s); trying fallback RPC", endpoint, exc)
                    continue
                if current_chunk > 20:
                    current_chunk = max(20, current_chunk // 2)
                    LOG.warning("eth_getLogs failed on all endpoints; reducing chunk to %s blocks", current_chunk)
                    break
                LOG.error("eth_getLogs failed for blocks %s..%s on all endpoints (%s); advancing", cursor, end, exc)
                cursor = end + 1
                break
        if logs is None:
            continue
        for log in logs:
            topics = log.get("topics") or []
            if len(topics) < 4:
                continue
            tokens = [v for v in (topic_address(topics[2]), topic_address(topics[3])) if v]
            primary = tokens[0] if len(tokens) == 1 else None
            if store.record_event(log, primary, "uniswap_v4_initialize", chain_id=CHAIN_ID):
                event_count += 1
            for token in tokens:
                store.enqueue(token, "uniswap_v4_initialize", log, chain_id=CHAIN_ID, priority=20)
                token_count += 1
        store.set_state(state_key, end + 1)
        current_minimum = store.get_state(minimum_key)
        if current_minimum is None or original_start < int(current_minimum):
            store.set_state(minimum_key, original_start)
        cursor = end + 1
        if current_chunk < chunk_size:
            current_chunk = min(chunk_size, current_chunk * 2)
    return {"from_block": original_start, "to_block": safe_head, "logs": event_count, "tokens": token_count}

def fetch_market_profile(ca: str, chain_id: int) -> Dict[str, Any]:
    spec = CHAIN_METADATA.get(chain_id)
    if not spec:
        raise CollectorError(f"unsupported market chain {chain_id}")
    payload = request_json(f"https://api.dexscreener.com/tokens/v1/{spec['dex_chain_id']}/{ca}")
    pairs = payload if isinstance(payload, list) else []
    if not pairs:
        return {}
    return max(pairs, key=lambda pair: float((pair.get("liquidity") or {}).get("usd") or 0))

def market_fields(pair: Dict[str, Any]) -> Dict[str, Any]:
    base, info = pair.get("baseToken") or {}, pair.get("info") or {}
    websites, socials = info.get("websites") or [], info.get("socials") or []
    created_at = None
    if pair.get("pairCreatedAt"):
        created_at = datetime.fromtimestamp(int(pair["pairCreatedAt"]) / 1000, timezone.utc).isoformat()
    x_value = next((s.get("handle") or s.get("url") for s in socials
                    if (s.get("platform") or s.get("type")) in {"twitter", "x"}), None)
    tg_value = next((s.get("handle") or s.get("url") for s in socials
                     if (s.get("platform") or s.get("type")) == "telegram"), None)
    return {
        "symbol": base.get("symbol"), "name": base.get("name"), "token_live_at": created_at,
        "current_market_cap_usd": pair.get("marketCap"),
        "fdv_usd": pair.get("fdv"),
        "current_liquidity_usd": (pair.get("liquidity") or {}).get("usd"),
        "peak_liquidity_usd": (pair.get("liquidity") or {}).get("usd"),
        "market_pair_url": pair.get("url"),
        "market_data_at": iso_utc(),
        "website": websites[0].get("url") if websites else None,
        "x_handle": x_value, "tg_url": tg_value,
    }


def required_profile_gaps(profile: Dict[str, Any]) -> List[str]:
    required = ("bytecode_sha256", "normalized_bytecode_sha256", "deployer_address", "creation_tx_hash")
    return [field for field in required if not profile.get(field)]

def ingest_and_enrich_job(db: ForensicDatabase, job: Dict[str, Any]) -> Dict[str, Any]:
    ca, chain_id = validate_address(job["ca"]), int(job["chain_id"])
    if chain_id not in ENABLED_CHAIN_IDS:
        raise CollectorError(f"job targets disabled or unsupported chain {chain_id}")
    spec = CHAIN_METADATA[chain_id]
    source = str(job.get("source") or "stream")
    LOG.info("enriching %s on %s from %s (attempt %s)",
             ca, spec["tag"], source, job.get("attempts"))
    profile = extract_full_token_metadata(ca, chain_id=chain_id)
    gaps = required_profile_gaps(profile)
    if gaps:
        raise IncompleteProfileError("explorer/RPC profile incomplete: " + ", ".join(gaps))
    canonical_ca = persist_profile(db, profile, qualified=False)
    pair = fetch_market_profile(ca, chain_id)
    market = market_fields(pair) if pair else {}
    source_parts = {part for part in source.split("+") if part}
    dex_paid = bool(source_parts & DEX_PAID_SOURCES)
    migrated = chain_id == CHAIN_ID and "uniswap_v4_initialize" in source_parts
    qualified = bool(dex_paid or migrated)
    qualification_gates = [
        gate for gate, passed in (("dex_paid", dex_paid), ("migrated", migrated)) if passed
    ]
    canonical_ca = db.upsert_token({
        "ca": canonical_ca, "chain_id": chain_id, "chain": spec["tag"],
        "launchpad": "Uniswap v4" if migrated else None,
        "symbol": market.get("symbol") or profile.get("token_symbol"),
        "name": market.get("name") or profile.get("token_name"),
        "token_live_at": market.get("token_live_at"),
        "current_market_cap_usd": market.get("current_market_cap_usd"),
        "observed_peak_market_cap_usd": market.get("current_market_cap_usd"),
        "fdv_usd": market.get("fdv_usd"),
        "current_liquidity_usd": market.get("current_liquidity_usd"),
        "market_pair_url": market.get("market_pair_url"),
        "market_data_at": market.get("market_data_at"),
        "peak_liquidity_usd": market.get("peak_liquidity_usd"),
        "website": market.get("website") or profile.get("website_url"),
        "x_handle": market.get("x_handle") or profile.get("twitter_url"),
        "description": profile.get("description"), "is_migrated": migrated,
        "is_dex_paid": dex_paid, "is_qualified": qualified,
        "is_training_anchor": False,
        "qualification_reasons": {
            "gates": qualification_gates, "discovery_sources": sorted(source_parts),
            "profile_complete": True, "market_found": bool(pair),
            "training_anchor": False, "chain_id": chain_id,
            "native_symbol": spec["native_symbol"],
        },
    })
    if market:
        scraper = BrandingScraper()
        tg = scraper.analyze_telegram(market.get("tg_url"))
        x_info = scraper.analyze_x(market.get("x_handle"))
        db.upsert_branding_profile({
            "ca": canonical_ca, "website_url": market.get("website"),
            "tg_url": market.get("tg_url"), "tg_handle": tg.get("tg_handle"),
            "tg_naming_pattern": tg.get("tg_naming_pattern"),
            "x_handle": x_info.get("x_handle"),
            "x_naming_pattern": x_info.get("x_naming_pattern"),
        })
    return {
        "ca": canonical_ca, "chain_id": chain_id, "chain": spec["tag"],
        "symbol": market.get("symbol") or profile.get("token_symbol"),
        "market_found": bool(pair), "is_qualified": qualified,
        "is_training_anchor": False, "qualification_gates": qualification_gates,
    }

def process_queue(db: ForensicDatabase, store: QueueStore, max_jobs: int = 10, concurrency: int = 4) -> Dict[str, int]:
    worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
    counts = {"succeeded": 0, "failed": 0, "claimed": 0}
    jobs = store.claim(worker_id, limit=max(1, max_jobs))
    counts["claimed"] = len(jobs)
    if not jobs:
        return counts

    def _execute_job(job: Dict[str, Any]) -> bool:
        if STOP_REQUESTED:
            return False
        try:
            ingest_and_enrich_job(db, job)
            store.succeed(job["ca"], job["chain_id"])
            return True
        except Exception as exc:
            store.fail(job, exc)
            LOG.warning("job %s (chain %s) failed: %s", job.get("ca"), job.get("chain_id"), exc)
            return False

    workers = min(max(1, concurrency), len(jobs))
    if workers > 1:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(_execute_job, j) for j in jobs]
            for f in concurrent.futures.as_completed(futures):
                if f.result():
                    counts["succeeded"] += 1
                else:
                    counts["failed"] += 1
    else:
        for job in jobs:
            if _execute_job(job):
                counts["succeeded"] += 1
            else:
                counts["failed"] += 1
    return counts

def atomic_write_json(path: Path, payload: Dict[str, Any]):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".{os.getpid()}.tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temp, path)

def write_health(status: str, store: QueueStore, **details):
    atomic_write_json(RUNTIME_DIR / "health.json", {
        "status": status, "timestamp": iso_utc(), "pid": os.getpid(), "queue": store.stats(), **details,
    })

def regenerate_outputs(db: ForensicDatabase):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    report = build_report(db, qualified_only=True)
    write_report_artifacts(report, str(REPORT_OUTPUT_PATH))
    subprocess.run(
        [sys.executable, str(APP_DIR / "generate_html_dashboard.py")],
        cwd=str(APP_DIR),
        check=True,
        timeout=180,
        env={
            **os.environ,
            "DASHBOARD_DATA_DIR": str(REPORT_OUTPUT_PATH.parent),
            "DASHBOARD_OUTPUT_DIR": str(OUTPUT_DIR),
        },
    )

def discover_once(store: QueueStore, include_chain: bool = True) -> Dict[str, Any]:
    dex = fetch_live_dexpaid_tokens()
    dex_counts: Dict[str, int] = {}
    for item in dex:
        source = "+".join(sorted(item.get("sources") or ["dex_live"]))
        chain_id = int(item["chain_id"])
        store.enqueue(item["ca"], source, item, chain_id=chain_id, priority=50)
        tag = str(CHAIN_METADATA[chain_id]["tag"])
        dex_counts[tag] = dex_counts.get(tag, 0) + 1
    rbh_chain = {"logs": 0, "tokens": 0}
    if include_chain and CHAIN_ID in ENABLED_CHAIN_IDS:
        try:
            rbh_chain = scan_uniswap_v4_initialize(store)
        except Exception as exc:
            LOG.error("Robinhood Chain Uniswap v4 scan failed (skipping for this cycle): %s", exc)
            rbh_chain = {"error": str(exc), "logs": 0, "tokens": 0}
    return {"dex_tokens": len(dex), "dex_by_chain": dex_counts, "robinhood_v4": rbh_chain}

def run_cycle(db: ForensicDatabase, store: QueueStore, max_jobs: int, include_chain: bool = True) -> Dict[str, Any]:
    run_id = None
    started_at = iso_utc()
    try:
        with db.get_connection() as conn:
            cur = conn.execute(
                "INSERT INTO ingestion_runs (mode, started_at, status) VALUES ('stream', ?, 'running')",
                (started_at,)
            )
            run_id = cur.lastrowid
    except Exception as exc:
        LOG.warning("Failed to record run start in ingestion_runs: %s", exc)

    discovery = discover_once(store, include_chain=include_chain)
    concurrency = int(os.getenv("COLLECTOR_CONCURRENCY", "4"))
    processing = process_queue(db, store, max_jobs=max_jobs, concurrency=concurrency)
    if processing["succeeded"]:
        regenerate_outputs(db)
    result = {"discovery": discovery, "processing": processing}
    write_health("ok", store, **result)

    if run_id:
        try:
            with db.get_connection() as conn:
                conn.execute(
                    """UPDATE ingestion_runs
                       SET status='ok', finished_at=?, discovered_count=?, succeeded_count=?, failed_count=?
                       WHERE run_id=?""",
                    (iso_utc(), discovery.get("dex_tokens", 0), processing.get("succeeded", 0), processing.get("failed", 0), run_id)
                )
        except Exception as exc:
            LOG.warning("Failed to record run completion in ingestion_runs: %s", exc)

    return result

def backfill(db: ForensicDatabase, store: QueueStore, days: int, max_jobs: int,
             from_block: Optional[int] = None) -> Dict[str, Any]:
    rbh_chain = (
        scan_uniswap_v4_initialize(store, days=days, from_block=from_block)
        if CHAIN_ID in ENABLED_CHAIN_IDS else {"logs": 0, "tokens": 0}
    )
    dex = backfill_historical_tokens(days)
    for item in dex:
        store.enqueue(
            item["ca"], "dex_search_backfill", item,
            chain_id=int(item["chain_id"]), priority=80,
        )
    processing = process_queue(db, store, max_jobs=max_jobs)
    if processing["succeeded"]:
        regenerate_outputs(db)
    result = {"robinhood_v4": rbh_chain, "dex_tokens": len(dex), "processing": processing}
    write_health("ok", store, mode="backfill", **result)
    return result

def preflight(db: ForensicDatabase, store: QueueStore) -> Tuple[bool, Dict[str, Any]]:
    checks: Dict[str, Dict[str, Any]] = {}

    def record(name: str, ok: bool, detail: Any):
        checks[name] = {"ok": bool(ok), "detail": detail}

    record("python", sys.version_info >= (3, 10), sys.version.split()[0])
    if 4663 in ENABLED_CHAIN_IDS:
        record("explorer_key_4663", bool(ROBIN_ETHERSCAN_API_KEY),
               "configured" if ROBIN_ETHERSCAN_API_KEY else "ROBIN_ETHERSCAN_API_KEY missing")
    if 5042 in ENABLED_CHAIN_IDS:
        try:
            arc_api = str(CHAIN_METADATA[5042]["explorer_api_url"])
            response = HTTP.get(
                arc_api, params={"module": "proxy", "action": "eth_blockNumber"},
                timeout=(5.0, 12.0),
            )
            response.raise_for_status()
            block_hex = response.json().get("result")
            record("explorer_api_5042", bool(block_hex and str(block_hex).startswith("0x")),
                   arc_api if block_hex else "ArcScan returned no block number")
        except Exception as exc:
            record("explorer_api_5042", False, f"{type(exc).__name__}: {exc}")

    for chain_id in ENABLED_CHAIN_IDS:
        spec = CHAIN_METADATA[chain_id]
        rpc_url = RPC_ENDPOINTS.get(chain_id, "")
        public_rpc = rpc_url.rstrip("/") == str(spec["public_rpc"]).rstrip("/")
        private_ready = bool(rpc_url) and not public_rpc and "REPLACE_ME" not in rpc_url
        record(
            f"production_rpc_{chain_id}", private_ready,
            "private/archive provider configured"
            if private_ready else f"public or placeholder {spec['name']} RPC is not production-safe",
        )
        try:
            actual_chain = int(rpc_call_strict(rpc_url, "eth_chainId", []), 16)
            record(f"rpc_chain_{chain_id}", actual_chain == chain_id, actual_chain)
        except Exception as exc:
            record(f"rpc_chain_{chain_id}", False, f"{type(exc).__name__}: {exc}")

    if CHAIN_ID in ENABLED_CHAIN_IDS:
        try:
            code = rpc_call_strict(RPC_ENDPOINTS[CHAIN_ID], "eth_getCode", [POOL_MANAGER, "latest"])
            record("pool_manager_4663", bool(code and code != "0x"), POOL_MANAGER)
        except Exception as exc:
            record("pool_manager_4663", False, f"{type(exc).__name__}: {exc}")

    try:
        with db.get_connection() as conn:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            token_columns = {row[1] for row in conn.execute("PRAGMA table_info(tokens)").fetchall()}
        record("database", integrity == "ok" and "chain_id" in token_columns,
               integrity if "chain_id" in token_columns else "tokens.chain_id missing")
    except Exception as exc:
        record("database", False, f"{type(exc).__name__}: {exc}")
    free = shutil.disk_usage(DB_PATH.parent).free
    record("disk_space", free >= 500 * 1024 * 1024, f"{free / 1024 / 1024:.0f} MiB free")
    try:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        probe = OUTPUT_DIR / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        record("output_directory", True, str(OUTPUT_DIR))
    except Exception as exc:
        record("output_directory", False, str(exc))
    record("queue", True, store.stats())
    ok = all(item["ok"] for item in checks.values())
    result = {
        "ready": ok, "enabled_chain_ids": list(ENABLED_CHAIN_IDS),
        "timestamp": iso_utc(), "checks": checks,
    }
    atomic_write_json(RUNTIME_DIR / "preflight.json", result)
    return ok, result

def health_check(max_age_seconds: int = 180) -> bool:
    path = RUNTIME_DIR / "health.json"
    if not path.exists():
        print("health file missing", file=sys.stderr)
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        timestamp = datetime.fromisoformat(payload["timestamp"].replace("Z", "+00:00"))
        healthy = payload.get("status") == "ok" and (utcnow() - timestamp).total_seconds() <= max_age_seconds
    except Exception as exc:
        print(f"invalid health file: {exc}", file=sys.stderr)
        return False
    print(json.dumps(payload, indent=2, sort_keys=True))
    return healthy

def handle_signal(signum, _frame):
    global STOP_REQUESTED
    LOG.warning("received signal %s; finishing current operation", signum)
    STOP_REQUESTED = True

def run_streamer(interval_seconds: int, max_jobs: int, include_chain: bool = True):
    signal.signal(signal.SIGINT, handle_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, handle_signal)
    db, store = ForensicDatabase(str(DB_PATH)), None
    store = QueueStore(db)
    with SingleInstanceLock(RUNTIME_DIR / "streamer.lock"):
        LOG.info("streamer started interval=%ss db=%s", interval_seconds, DB_PATH)
        while not STOP_REQUESTED:
            started = time.monotonic()
            try:
                result = run_cycle(db, store, max_jobs=max_jobs, include_chain=include_chain)
                LOG.info("cycle complete: %s", result)
            except Exception as exc:
                LOG.exception("stream cycle failed")
                write_health("degraded", store, error=f"{type(exc).__name__}: {exc}")
            end = time.monotonic() + max(0, interval_seconds - (time.monotonic() - started))
            while not STOP_REQUESTED and time.monotonic() < end:
                time.sleep(min(1.0, end - time.monotonic()))
        write_health("stopped", store, reason="graceful shutdown")
        LOG.info("streamer stopped")

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--stream", action="store_true")
    mode.add_argument("--once", action="store_true")
    mode.add_argument("--backfill", type=int, metavar="DAYS")
    mode.add_argument("--drain", action="store_true")
    mode.add_argument("--preflight", action="store_true")
    mode.add_argument("--status", action="store_true")
    mode.add_argument("--health-check", action="store_true")
    parser.add_argument("--interval", type=int, default=int(os.getenv("COLLECTION_INTERVAL_SECONDS", "300")))
    parser.add_argument("--max-jobs", type=int, default=10)
    parser.add_argument("--from-block", type=int)
    parser.add_argument("--retry-dead", action="store_true")
    parser.add_argument("--dex-only", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    configure_logging(args.verbose)
    if args.health_check:
        return 0 if health_check(max_age_seconds=max(180, args.interval * 3)) else 1
    db, store = ForensicDatabase(str(DB_PATH)), None
    store = QueueStore(db)
    if args.retry_dead:
        LOG.info("requeued %s dead jobs", store.retry_dead())
    if args.preflight:
        ok, result = preflight(db, store)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if ok else 1
    if args.status:
        print(json.dumps(store.stats(), indent=2, sort_keys=True))
        return 0
    if args.stream:
        try:
            run_streamer(max(15, args.interval), max(1, args.max_jobs), include_chain=not args.dex_only)
            return 0
        except Exception as exc:
            LOG.exception("streamer failed")
            write_health("failed", store, error=f"{type(exc).__name__}: {exc}")
            return 1
    try:
        with SingleInstanceLock(RUNTIME_DIR / "streamer.lock"):
            if args.backfill:
                result = backfill(db, store, args.backfill, max(1, args.max_jobs), args.from_block)
            elif args.drain:
                result = process_queue(db, store, max_jobs=max(1, args.max_jobs))
                if result["succeeded"]:
                    regenerate_outputs(db)
                write_health("ok", store, mode="drain", processing=result)
            elif args.once:
                result = run_cycle(db, store, max(1, args.max_jobs), include_chain=not args.dex_only)
            else:
                parser.print_help()
                return 2
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0
    except Exception as exc:
        LOG.exception("collector command failed")
        write_health("failed", store, error=f"{type(exc).__name__}: {exc}")
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
