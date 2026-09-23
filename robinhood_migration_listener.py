import time
import requests
from datetime import datetime, timezone

RPC_URL = "https://rpc.mainnet.chain.robinhood.com"
CHAIN_NAME = "robinhood"

POOL_MANAGER = "0x8366a39cc670b4001a1121b8f6a443a643e40951"
INITIALIZE_TOPIC0 = (
    "0xdd466e674ea557f56295e2d0218a125ea4b4f0f6f3307b95f85e6110838d6438"
)

# Validated against BUG, AUTOSUBMIT, and MU. A V4 Initialize without one of
# these token-bound events is a generic pool launch, not a graduation.
MIGRATION_PATHS = {
    ("0x65af70b69a36e6e6ab6263ac1fe6d378c9d0740d", "0x3d9ab7c9"): {
        "event_topic0": "0xf0a2493b501685967f6c728cec9ee3e3f745018b6de376818d4448b9533fc4a8",
        "token_topic_index": 1,
    },
    ("0x7ab338fde039feb0da5a38d90d1a08fff1c31af0", "0x39ecce49"): {
        "event_topic0": "0x2ed5a8749a7e3a68a074750cc77850912a0708dc62ab7ea42b0c3e5beb36f017",
        "token_topic_index": 3,
    },
}

POLL_INTERVAL = 15
CONFIRMATIONS = 2
INITIAL_LOOKBACK_BLOCKS = 30
DEX_INDEX_GRACE_SECONDS = 600      # wait up to 10 minutes for DexScreener indexing
PAIR_TIME_TOLERANCE_SECONDS = 600  # pair must be created near the pool event

# WETH from your listener output, plus the recurring quote-side address observed there.
# Add verified Robinhood USDC / other quote assets here when known.
QUOTE_ASSET_ADDRESSES = {
    "0x0bd7d308f8e1639fab988df18a8011f41eacad73",  # WETH
    "0x5fc5360d0400a0fd4f2af552add042d716f1d168",  # recurring quote side; verify before production
}

QUOTE_SYMBOLS = {"WETH", "ETH", "USDC", "USDT", "DAI"}

session = requests.Session()
session.headers.update({"User-Agent": "OmniReborn-RBH-Migration-Test/1.0"})

last_block = None
seen_logs = set()
pending_events = {}
block_time_cache = {}


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def rpc(method, params):
    response = session.post(
        RPC_URL,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()

    if payload.get("error"):
        raise RuntimeError(f"RPC {method}: {payload['error']}")
    if "result" not in payload:
        raise RuntimeError(f"RPC {method}: missing result")

    return payload["result"]


def topic_address(topic):
    if not topic or len(topic) < 42:
        return None

    address = "0x" + topic[-40:].lower()
    return None if address == "0x" + "0" * 40 else address


def migration_tokens_in_transaction(tx_hash):
    """Return token-bound known migration evidence from one transaction."""
    transaction = rpc("eth_getTransactionByHash", [tx_hash]) or {}
    destination = str(transaction.get("to") or "").lower()
    selector = str(transaction.get("input") or "").lower()[:10]
    path = MIGRATION_PATHS.get((destination, selector))
    if not path:
        return {}

    receipt = rpc("eth_getTransactionReceipt", [tx_hash]) or {}
    matches = {}
    for receipt_log in receipt.get("logs") or []:
        topics = receipt_log.get("topics") or []
        token_index = path["token_topic_index"]
        if (
            str(receipt_log.get("address") or "").lower() != destination
            or len(topics) <= token_index
            or str(topics[0]).lower() != path["event_topic0"]
        ):
            continue
        token_ca = topic_address(topics[token_index])
        if token_ca:
            matches[token_ca] = {
                "router": destination,
                "selector": selector,
                "event_topic0": path["event_topic0"],
                "token_topic_index": token_index,
            }
    return matches


def block_timestamp(block_hex):
    if block_hex in block_time_cache:
        return block_time_cache[block_hex]

    block = rpc("eth_getBlockByNumber", [block_hex, False])
    timestamp = int(block["timestamp"], 16)
    block_time_cache[block_hex] = timestamp
    return timestamp


def fresh_dex_pair(token_ca, event_timestamp):
    """
    Strictly return a pair only when:
    - token is the base asset;
    - pair has an address and creation timestamp;
    - pair creation was close to this on-chain PoolManager event.
    """
    if token_ca in QUOTE_ASSET_ADDRESSES:
        return None

    url = f"https://api.dexscreener.com/tokens/v1/{CHAIN_NAME}/{token_ca}"
    response = session.get(url, timeout=15)
    response.raise_for_status()

    payload = response.json()
    if not isinstance(payload, list):
        return None

    eligible = []

    for pair in payload:
        base = pair.get("baseToken") or {}
        base_address = str(base.get("address") or "").lower()
        symbol = str(base.get("symbol") or "").upper()
        pair_created_ms = pair.get("pairCreatedAt")

        if base_address != token_ca:
            continue
        if symbol in QUOTE_SYMBOLS:
            continue
        if not pair.get("pairAddress") or not pair_created_ms:
            continue

        pair_timestamp = int(pair_created_ms) // 1000
        if abs(pair_timestamp - event_timestamp) > PAIR_TIME_TOLERANCE_SECONDS:
            continue

        eligible.append(pair)

    if not eligible:
        return None

    return max(
        eligible,
        key=lambda pair: float((pair.get("liquidity") or {}).get("usd") or 0),
    )


def add_new_pool_events():
    global last_block

    head = int(rpc("eth_blockNumber", []), 16)
    safe_head = max(0, head - CONFIRMATIONS)

    if last_block is None:
        last_block = max(0, safe_head - INITIAL_LOOKBACK_BLOCKS)

    if safe_head <= last_block:
        return 0

    logs = rpc("eth_getLogs", [{
        "fromBlock": hex(last_block + 1),
        "toBlock": hex(safe_head),
        "address": POOL_MANAGER,
        "topics": [INITIALIZE_TOPIC0],
    }]) or []

    added = 0

    for log in logs:
        tx_hash = str(log.get("transactionHash") or "").lower()
        log_index = str(log.get("logIndex") or "").lower()
        event_key = (tx_hash, log_index)

        if event_key in seen_logs:
            continue

        seen_logs.add(event_key)

        topics = log.get("topics") or []
        if len(topics) < 4:
            continue

        try:
            migration_evidence = migration_tokens_in_transaction(tx_hash)
        except Exception as exc:
            print(f"  Migration-proof check delayed for {tx_hash}: {exc}")
            continue

        addresses = {
            address
            for address in (topic_address(topics[2]), topic_address(topics[3]))
            if (
                address
                and address not in QUOTE_ASSET_ADDRESSES
                and address in migration_evidence
            )
        }
        if not addresses:
            continue

        pending_events[event_key] = {
            "tx_hash": tx_hash,
            "block_hex": log["blockNumber"],
            "block_number": int(log["blockNumber"], 16),
            "event_timestamp": block_timestamp(log["blockNumber"]),
            "addresses": addresses,
            "migration_evidence": migration_evidence,
            "first_seen": time.time(),
        }
        added += 1

    last_block = safe_head
    return added


def process_pending_events():
    confirmed = 0
    expired = 0

    for event_key, event in list(pending_events.items()):
        candidates = []

        for token_ca in event["addresses"]:
            try:
                pair = fresh_dex_pair(token_ca, event["event_timestamp"])
            except Exception as exc:
                print(f"  DEX check delayed for {token_ca}: {exc}")
                continue

            if pair:
                candidates.append((token_ca, pair))

        # A valid migration candidate has exactly one non-quote base token.
        if len(candidates) == 1:
            token_ca, pair = candidates[0]
            base = pair.get("baseToken") or {}
            liquidity = float((pair.get("liquidity") or {}).get("usd") or 0)
            market_cap = pair.get("marketCap") or pair.get("fdv") or 0
            pair_time = datetime.fromtimestamp(
                int(pair["pairCreatedAt"]) / 1000,
                timezone.utc,
            ).strftime("%Y-%m-%d %H:%M:%S UTC")

            print("\n" + "=" * 72)
            print("✅ VERIFIED ROBINHOOD MIGRATION CANDIDATE")
            print("=" * 72)
            print(f"Token                 : ${base.get('symbol', 'UNKNOWN')}")
            print(f"Contract address      : {token_ca}")
            print(f"Pair address          : {pair.get('pairAddress')}")
            print(f"DEX URL               : {pair.get('url')}")
            print(f"Pair created          : {pair_time}")
            print(f"Event block           : {event['block_number']}")
            print(f"Transaction           : {event['tx_hash']}")
            print(f"Current MC / FDV      : ${float(market_cap):,.2f}")
            print(f"Current liquidity     : ${liquidity:,.2f}")
            print("=" * 72)

            del pending_events[event_key]
            confirmed += 1
            continue

        # Ignore events where the pair never becomes verifiable.
        if time.time() - event["first_seen"] > DEX_INDEX_GRACE_SECONDS:
            del pending_events[event_key]
            expired += 1

    return confirmed, expired


print("🎧 Robinhood migration-only listener active.")
print("Requires a token-bound known migration event, V4 Initialize, and fresh DEX pair.\n")

while True:
    try:
        events_added = add_new_pool_events()
        confirmed, expired = process_pending_events()

        print(
            f"[{utc_now()}] working | "
            f"new pool events={events_added} | "
            f"pending index={len(pending_events)} | "
            f"verified migrations={confirmed} | "
            f"expired={expired}"
        )

    except Exception as exc:
        print(f"[{utc_now()}] ⚠️ Listener error: {type(exc).__name__}: {exc}")

    time.sleep(POLL_INTERVAL)