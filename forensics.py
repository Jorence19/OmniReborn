import hashlib
import json
import requests
import re
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Tuple, List
from config import KNOWN_EXCHANGES, RPC_ENDPOINTS, ETHERSCAN_API_KEY, BASESCAN_API_KEY, ROBIN_ETHERSCAN_API_KEY

def rpc_call(rpc_url: str, method: str, params: list, req_id: int = 1):
    """Executes a JSON-RPC request to the specified EVM node."""
    headers = {'Content-Type': 'application/json'}
    payload = {
        "jsonrpc": "2.0",
        "method": method,
        "params": params,
        "id": req_id
    }
    try:
        response = requests.post(rpc_url, headers=headers, json=payload, timeout=8)
        if response.status_code == 200:
            res_json = response.json()
            return res_json.get('result')
    except Exception as e:
        print(f"[RPC Error] {method} on {rpc_url}: {e}")
    return None

def fetch_bytecode(rpc_url: str, contract_address: str) -> Optional[str]:
    """Fetches runtime bytecode for a given contract address."""
    result = rpc_call(rpc_url, "eth_getCode", [contract_address, "latest"])
    if result and result != '0x' and result != '0x0':
        return result
    return None

def _hex_to_bytes(hex_data: str) -> bytes:
    """Decode EVM hex safely; hashing printable hex is not bytecode hashing."""
    if not hex_data:
        return b""
    clean_hex = hex_data[2:] if hex_data.startswith("0x") else hex_data
    if len(clean_hex) % 2:
        clean_hex = "0" + clean_hex
    return bytes.fromhex(clean_hex)


def strip_solidity_metadata(bytecode_hex: str) -> Tuple[bytes, bytes]:
    """Return executable bytecode and its Solidity CBOR metadata trailer."""
    try:
        raw = _hex_to_bytes(bytecode_hex)
    except ValueError:
        return b"", b""
    if len(raw) < 3:
        return raw, b""
    metadata_length = int.from_bytes(raw[-2:], byteorder="big")
    trailer_length = metadata_length + 2
    if metadata_length <= 0 or trailer_length > len(raw):
        return raw, b""
    metadata = raw[-trailer_length:-2]
    if not metadata or metadata[0] >> 5 != 5:
        return raw, b""
    return raw[:-trailer_length], metadata


def compute_bytecode_hashes(bytecode_hex: str) -> Tuple[Optional[str], Optional[str]]:
    """Compute SHA-256 and legacy MD5 over actual runtime bytecode bytes."""
    if not bytecode_hex:
        return None, None
    try:
        raw = _hex_to_bytes(bytecode_hex)
    except ValueError:
        return None, None
    return hashlib.sha256(raw).hexdigest(), hashlib.md5(raw).hexdigest()


def compute_normalized_bytecode_hash(bytecode_hex: str) -> Optional[str]:
    """Hash executable runtime bytecode with compiler metadata removed."""
    executable, _ = strip_solidity_metadata(bytecode_hex)
    return hashlib.sha256(executable).hexdigest() if executable else None

def extract_function_selectors(bytecode_hex: str) -> Tuple[Optional[str], Optional[str]]:
    """Extract PUSH4 selectors only when followed by an EQ dispatcher opcode."""
    if not bytecode_hex:
        return None, None
    try:
        code, _ = strip_solidity_metadata(bytecode_hex)
    except ValueError:
        return None, None

    instructions = []
    offset = 0
    while offset < len(code):
        opcode = code[offset]
        push_size = opcode - 0x5F if 0x60 <= opcode <= 0x7F else 0
        data = code[offset + 1:offset + 1 + push_size]
        instructions.append((opcode, data))
        offset += 1 + push_size

    selectors = set()
    for index, (opcode, data) in enumerate(instructions):
        if opcode != 0x63 or len(data) != 4:
            continue
        following = [item[0] for item in instructions[index + 1:index + 6]]
        if 0x14 in following:  # EQ in the local comparison sequence
            selectors.add(data.hex())
    if not selectors:
        return None, None
    selectors_str = ",".join(sorted(selectors))
    return hashlib.md5(selectors_str.encode('utf-8')).hexdigest()[:8], selectors_str

def extract_compiler_version(bytecode_hex: str) -> Optional[str]:
    """Extract the three-byte Solidity version from the CBOR metadata trailer."""
    if not bytecode_hex:
        return None
    _, metadata = strip_solidity_metadata(bytecode_hex)
    match = re.search(rb"\x64solc\x43(.)(.)(.)", metadata, re.DOTALL)
    if match:
        v_major, v_minor, v_patch = (part[0] for part in match.groups())
        return f"solc {v_major}.{v_minor}.{v_patch}"
    return None

def decode_string_or_bytes32(hex_data: str) -> Optional[str]:
    """Decodes string or bytes32 return data from eth_call."""
    if not hex_data or hex_data == '0x':
        return None
    clean_hex = hex_data[2:] if hex_data.startswith('0x') else hex_data
    try:
        if len(clean_hex) >= 128:
            length = int(clean_hex[64:128], 16)
            data_bytes = clean_hex[128:128 + length * 2]
            return bytes.fromhex(data_bytes).decode('utf-8', errors='ignore').strip()
        data_bytes = bytes.fromhex(clean_hex)
        decoded = data_bytes.rstrip(b'\x00').decode('utf-8', errors='ignore').strip()
        return decoded if decoded else None
    except Exception:
        return None

def call_erc20_views(rpc_url: str, contract_address: str) -> Dict[str, Any]:
    """Calls standard ERC-20 view methods via eth_call."""
    info = {
        'total_supply': None,
        'decimals': None,
        'name': None,
        'symbol': None,
        'owner': None
    }
    # totalSupply() (0x18160ddd)
    ts_res = rpc_call(rpc_url, "eth_call", [{"to": contract_address, "data": "0x18160ddd"}, "latest"])
    if ts_res and ts_res != '0x':
        try:
            info['total_supply'] = str(int(ts_res, 16))
        except Exception:
            pass

    # decimals() (0x313ce567)
    dec_res = rpc_call(rpc_url, "eth_call", [{"to": contract_address, "data": "0x313ce567"}, "latest"])
    if dec_res and dec_res != '0x':
        try:
            info['decimals'] = int(dec_res, 16)
        except Exception:
            pass

    # symbol() (0x95d89b41)
    sym_res = rpc_call(rpc_url, "eth_call", [{"to": contract_address, "data": "0x95d89b41"}, "latest"])
    if sym_res and sym_res != '0x':
        info['symbol'] = decode_string_or_bytes32(sym_res)

    # name() (0x06fdde03)
    name_res = rpc_call(rpc_url, "eth_call", [{"to": contract_address, "data": "0x06fdde03"}, "latest"])
    if name_res and name_res != '0x':
        info['name'] = decode_string_or_bytes32(name_res)

    # owner() (0x8da5cb5b)
    owner_res = rpc_call(rpc_url, "eth_call", [{"to": contract_address, "data": "0x8da5cb5b"}, "latest"])
    if owner_res and len(owner_res) >= 26:
        owner_addr = "0x" + owner_res[-40:].lower()
        if owner_addr != "0x0000000000000000000000000000000000000000":
            info['owner'] = owner_addr

    return info

def inspect_storage_addresses(rpc_url: str, contract_address: str, owner_address: str = None) -> List[Dict[str, Any]]:
    """Collect address-shaped storage values without falsely naming fee receivers."""
    candidates = []
    for slot in range(10):
        val = rpc_call(rpc_url, "eth_getStorageAt", [contract_address, hex(slot), "latest"])
        if val and len(val) >= 42:
            extracted_addr = "0x" + val[-40:].lower()
            if extracted_addr in ["0x0000000000000000000000000000000000000000",
                                  "0x000000000000000000000000000000000000dead"]:
                continue
            if owner_address and extracted_addr == owner_address.lower():
                continue
            if extracted_addr == contract_address.lower():
                continue
            candidates.append({"slot": slot, "address": extracted_addr})
    return candidates

def match_exchange_name(wallet_address: str) -> str:
    """Matches a wallet address against known exchange/bridge wallets."""
    if not wallet_address:
        return "Unknown"
    wallet_lower = wallet_address.lower()
    for exchange, addresses in KNOWN_EXCHANGES.items():
        if wallet_lower in [addr.lower() for addr in addresses]:
            return exchange
    return "Unknown"

def get_time_difference_text(timestamp: int) -> Optional[str]:
    """Formats relative age (e.g., '2H 15M ago')."""
    if not timestamp:
        return None
    now = datetime.now(timezone.utc).timestamp()
    delta_seconds = int(now - timestamp)
    if delta_seconds < 0:
        return "Just now"
    hours, remainder = divmod(delta_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours > 24:
        return f"{hours // 24}D {hours % 24}H ago"
    if hours > 0:
        return f"{hours}H {minutes}M ago"
    return f"{minutes}M {seconds}S ago"

def decode_creation_bytecode_args(creation_bytecode: str) -> Dict[str, Any]:
    """Extract bounded printable constructor clues without consuming bytecode junk."""
    result = {
        'ipfs_metadata': None,
        'description': None,
        'token_name': None,
        'token_symbol': None,
        'total_supply': None,
        'twitter': None,
        'website': None,
        'hardcoded_addresses': [],
        'printable_strings': [],
        'embedded_strings_hash': None,
    }
    if not creation_bytecode or len(creation_bytecode) < 100:
        return result
    try:
        raw = _hex_to_bytes(creation_bytecode)
    except ValueError:
        return result
    clean_hex = raw.hex()

    # ABI strings are generally NULL padded; printable runs recover URLs and copy.
    tail = raw[-12000:]
    strings = []
    for match in re.findall(rb"[\x20-\x7e]{4,}", tail):
        text = match.decode('utf-8', errors='ignore').strip()
        if text and text not in strings:
            strings.append(text)
    result['printable_strings'] = strings[:100]
    if strings:
        canonical = "\n".join(sorted(strings)).encode('utf-8')
        result['embedded_strings_hash'] = hashlib.sha256(canonical).hexdigest()

    ipfs_pattern = re.compile(r"ipfs://(?:bafy[a-z2-7]{20,}|Qm[1-9A-HJ-NP-Za-km-z]{40,})", re.IGNORECASE)
    url_pattern = re.compile(r"https?://[^\s\x00\"'<>]+", re.IGNORECASE)
    descriptions = []
    for text in strings:
        ipfs_match = ipfs_pattern.search(text)
        if ipfs_match and result['ipfs_metadata'] is None:
            result['ipfs_metadata'] = ipfs_match.group(0)
        for url in url_pattern.findall(text):
            url = url.rstrip(').,;]')
            lower = url.lower()
            if ('x.com/' in lower or 'twitter.com/' in lower) and result['twitter'] is None:
                result['twitter'] = url
            elif result['website'] is None:
                result['website'] = url
        if len(text) >= 40 and not url_pattern.search(text):
            printable_ratio = sum(ch.isalnum() or ch.isspace() or ch in '.,!?-_:;()' for ch in text) / len(text)
            if printable_ratio >= 0.85:
                descriptions.append(text)
    if descriptions:
        result['description'] = max(descriptions, key=len)[:4000]

    # ABI-encoded addresses are left padded to 32 bytes. Retain as candidates only.
    addresses = []
    for address in re.findall(r'000000000000000000000000([0-9a-fA-F]{40})', clean_hex[-12000:]):
        full = '0x' + address.lower()
        if full not in {
            '0x0000000000000000000000000000000000000000',
            '0x000000000000000000000000000000000000dead',
        } and full not in addresses:
            addresses.append(full)
    result['hardcoded_addresses'] = addresses

    if '033b2e3c9fd0803ce8000000' in clean_hex:
        result['total_supply'] = '1000000000000000000000000000'
    return result

def _duration_text(seconds: Optional[int]) -> Optional[str]:
    if seconds is None:
        return None
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}D")
    if hours or days:
        parts.append(f"{hours}H")
    if minutes or hours or days:
        parts.append(f"{minutes}M")
    parts.append(f"{secs}S")
    return " ".join(parts)


def _api_key_for_chain(chain_id: int) -> str:
    if chain_id == 4663:
        return ROBIN_ETHERSCAN_API_KEY
    if chain_id == 5042:
        # Etherscan V2 keys are unified across supported chains. Keep a
        # dedicated override, but reuse the configured Robinhood V2 key.
        return ETHERSCAN_API_KEY or ROBIN_ETHERSCAN_API_KEY
    if chain_id == 8453:
        return BASESCAN_API_KEY or ETHERSCAN_API_KEY
    return ETHERSCAN_API_KEY


def trace_creator_and_funding(
    chain_id: int,
    contract_address: str,
    token_decimals: int = 18,
    lineage_depth: int = 4,
) -> Dict[str, Any]:
    """Trace creation execution plus pre-launch native-funding behavior.

    The launch funder is the *last* positive inbound transfer before deployment,
    not blindly the wallet's first ever inbound transfer.  First-funding facts
    are retained separately, and lineage evidence records the transaction used
    at every hop so downstream clustering remains auditable.
    """
    details = {
        'deployer_address': None,
        'funder_address': None,
        'funder_name': 'Unknown',
        'funder_hop2_address': None,
        'funder_hop2_name': None,
        'funding_lineage': [],
        'eth_received': None,
        'funding_tx_hash': None,
        'funded_timestamp': None,
        'first_funder_address': None,
        'first_funded_timestamp': None,
        'funding_count_before_deploy': 0,
        'funding_total_eth_before_deploy': 0.0,
        'wallet_age_at_deploy': None,
        'wallet_age_at_deploy_seconds': None,
        'setup_time_seconds': None,
        'creation_timestamp': None,
        'creation_gas_used': None,
        'creation_tx_fee': None,
        'creation_tx_hash': None,
        'creation_block': None,
        'creation_method': None,
        'creation_nonce': None,
        'gas_price_gwei': None,
        'max_fee_gwei': None,
        'priority_fee_gwei': None,
        'deployer_initial_capital': None,
        'deployer_initial_snipe': None,
        'deployer_initial_snipe_raw': None,
        'contract_factory': None,
        'constructor_data': {},
        'deployer_balance_eth': None,
    }

    base_url = "https://api.etherscan.io/v2/api"
    api_key = _api_key_for_chain(chain_id)

    def api_get(**params):
        params.update({'chainid': chain_id, 'apikey': api_key})
        response = requests.get(base_url, params=params, timeout=12)
        response.raise_for_status()
        return response.json()

    def normal_transactions(address: str) -> List[Dict[str, Any]]:
        payload = api_get(
            module='account', action='txlist', address=address,
            startblock=0, endblock=99999999, page=1, offset=10000, sort='asc'
        )
        result = payload.get('result')
        return result if payload.get('status') == '1' and isinstance(result, list) else []

    try:
        payload = api_get(
            module='contract', action='getcontractcreation',
            contractaddresses=contract_address,
        )
        if payload.get('status') == '1' and payload.get('result'):
            item = payload['result'][0]
            details['deployer_address'] = (item.get('contractCreator') or '').lower() or None
            details['creation_tx_hash'] = item.get('txHash')
            details['creation_block'] = int(item['blockNumber']) if item.get('blockNumber') else None
            details['contract_factory'] = item.get('contractFactory') or None
            details['constructor_data'] = decode_creation_bytecode_args(item.get('creationBytecode', ''))
    except Exception as exc:
        print(f"[Explorer Error] Contract creation for {contract_address}: {exc}")

    deployer = details['deployer_address']
    if not deployer:
        return details

    try:
        payload = api_get(module='account', action='balance', address=deployer, tag='latest')
        if payload.get('status') == '1' and payload.get('result'):
            details['deployer_balance_eth'] = int(payload['result']) / 1e18
    except Exception:
        pass

    if details['creation_tx_hash']:
        try:
            payload = api_get(
                module='proxy', action='eth_getTransactionByHash',
                txhash=details['creation_tx_hash'],
            )
            tx = payload.get('result') or {}
            input_data = tx.get('input') or ''
            if input_data.startswith('0x') and len(input_data) >= 10:
                details['creation_method'] = input_data[2:10].lower()
            details['deployer_initial_capital'] = int(tx.get('value', '0x0'), 16) / 1e18
            details['creation_nonce'] = int(tx.get('nonce', '0x0'), 16)
            if tx.get('gasPrice'):
                details['gas_price_gwei'] = int(tx['gasPrice'], 16) / 1e9
            if tx.get('maxFeePerGas'):
                details['max_fee_gwei'] = int(tx['maxFeePerGas'], 16) / 1e9
            if tx.get('maxPriorityFeePerGas'):
                details['priority_fee_gwei'] = int(tx['maxPriorityFeePerGas'], 16) / 1e9
        except Exception:
            pass

        try:
            payload = api_get(
                module='proxy', action='eth_getTransactionReceipt',
                txhash=details['creation_tx_hash'],
            )
            receipt = payload.get('result') or {}
            gas_used = int(receipt.get('gasUsed', '0x0'), 16)
            effective_gas_price = int(receipt.get('effectiveGasPrice', '0x0'), 16)
            details['creation_gas_used'] = gas_used
            details['creation_tx_fee'] = (gas_used * effective_gas_price) / 1e18

            transfer_topic = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
            sniped_raw = 0
            for log in receipt.get('logs') or []:
                topics = log.get('topics') or []
                if (
                    (log.get('address') or '').lower() == contract_address.lower()
                    and len(topics) >= 3
                    and topics[0].lower() == transfer_topic
                    and "0x" + topics[2][-40:].lower() == deployer
                ):
                    sniped_raw += int(log.get('data', '0x0'), 16)
            if sniped_raw:
                details['deployer_initial_snipe_raw'] = str(sniped_raw)
                details['deployer_initial_snipe'] = sniped_raw / (10 ** int(token_decimals or 18))
        except Exception:
            pass

    try:
        txs = normal_transactions(deployer)
        creation_hash = (details['creation_tx_hash'] or '').lower()
        creation_tx = next((t for t in txs if (t.get('hash') or '').lower() == creation_hash), None)
        if creation_tx:
            details['creation_timestamp'] = int(creation_tx['timeStamp']) if creation_tx.get('timeStamp') else None
            if not details['creation_method']:
                input_data = creation_tx.get('input') or ''
                details['creation_method'] = input_data[2:10].lower() if input_data.startswith('0x') and len(input_data) >= 10 else None
            if details['deployer_initial_capital'] is None:
                details['deployer_initial_capital'] = int(creation_tx.get('value') or 0) / 1e18

        creation_ts = details['creation_timestamp']
        timestamped = [t for t in txs if t.get('timeStamp')]
        first_activity_ts = min((int(t['timeStamp']) for t in timestamped), default=None)
        incoming = [
            t for t in timestamped
            if (t.get('to') or '').lower() == deployer
            and (t.get('from') or '').lower() != deployer
            and int(t.get('value') or 0) > 0
            and (creation_ts is None or int(t['timeStamp']) <= creation_ts)
        ]
        incoming.sort(key=lambda t: (int(t['timeStamp']), int(t.get('blockNumber') or 0), int(t.get('transactionIndex') or 0)))
        details['funding_count_before_deploy'] = len(incoming)
        details['funding_total_eth_before_deploy'] = sum(int(t.get('value') or 0) for t in incoming) / 1e18

        if incoming:
            first_funding = incoming[0]
            launch_funding = incoming[-1]
            details['first_funder_address'] = (first_funding.get('from') or '').lower() or None
            details['first_funded_timestamp'] = int(first_funding['timeStamp'])
            details['funder_address'] = (launch_funding.get('from') or '').lower() or None
            details['funding_tx_hash'] = launch_funding.get('hash')
            details['eth_received'] = int(launch_funding.get('value') or 0) / 1e18
            details['funded_timestamp'] = int(launch_funding['timeStamp'])
            details['funder_name'] = match_exchange_name(details['funder_address'])
            if creation_ts is not None:
                details['setup_time_seconds'] = max(0, creation_ts - details['funded_timestamp'])

        if creation_ts is not None and first_activity_ts is not None:
            age = max(0, creation_ts - first_activity_ts)
            details['wallet_age_at_deploy_seconds'] = age
            details['wallet_age_at_deploy'] = _duration_text(age)

        current = details['funder_address']
        cutoff = details['funded_timestamp']
        visited = {deployer}
        for hop in range(1, max(1, lineage_depth) + 1):
            if not current or current in visited:
                break
            visited.add(current)
            label = match_exchange_name(current)
            evidence = {'hop': hop, 'address': current, 'label': label}
            details['funding_lineage'].append(evidence)
            if label != 'Unknown':
                break
            parent_txs = normal_transactions(current)
            parents = [
                t for t in parent_txs
                if (t.get('to') or '').lower() == current
                and (t.get('from') or '').lower() != current
                and int(t.get('value') or 0) > 0
                and t.get('timeStamp')
                and (cutoff is None or int(t['timeStamp']) <= cutoff)
            ]
            if not parents:
                break
            parents.sort(key=lambda t: (int(t['timeStamp']), int(t.get('blockNumber') or 0), int(t.get('transactionIndex') or 0)))
            parent = parents[-1]
            evidence.update({
                'funding_tx_hash': parent.get('hash'),
                'received_eth': int(parent.get('value') or 0) / 1e18,
                'received_timestamp': int(parent['timeStamp']),
            })
            current = (parent.get('from') or '').lower() or None
            cutoff = int(parent['timeStamp'])
            if hop == 1:
                details['funder_hop2_address'] = current
                details['funder_hop2_name'] = match_exchange_name(current)
    except Exception as exc:
        print(f"[Explorer Error] Funding tracing for {deployer}: {exc}")

    return details

def extract_full_token_metadata(contract_address: str, chain_id: int = 4663) -> Dict[str, Any]:
    """
    Consolidated master function: Extracts the complete 35+ parameter forensic profile
    by combining RPC disassembly, Etherscan Multichain V2, and constructor decoders.
    """
    rpc_url = RPC_ENDPOINTS.get(chain_id)
    if not rpc_url:
        raise ValueError(f"no RPC endpoint configured for chain {chain_id}")
    clean_ca = contract_address.strip().lower()

    # 1. Bytecode Forensics
    bytecode = fetch_bytecode(rpc_url, clean_ca)
    sha256_hash, md5_hash = compute_bytecode_hashes(bytecode)
    normalized_hash = compute_normalized_bytecode_hash(bytecode)
    executable, cbor_metadata = strip_solidity_metadata(bytecode)
    method_ids_hash, selectors_str = extract_function_selectors(bytecode)
    compiler_version = extract_compiler_version(bytecode)

    # 2. ERC-20 State Calls
    erc20_data = call_erc20_views(rpc_url, clean_ca)
    storage_addresses = inspect_storage_addresses(rpc_url, clean_ca, erc20_data.get('owner'))

    # 3. Etherscan Multichain V2 & Funder Lineage
    creation_data = trace_creator_and_funding(
        chain_id, clean_ca, token_decimals=erc20_data.get('decimals') or 18
    )
    c_args = creation_data.get('constructor_data') or {}

    # Merge into complete profile
    metadata = {
        'ca': clean_ca,
        'chain_id': chain_id,
        'token_name': erc20_data.get('name') or c_args.get('token_name'),
        'token_symbol': erc20_data.get('symbol') or c_args.get('token_symbol'),
        'total_supply': erc20_data.get('total_supply') or c_args.get('total_supply'),
        'decimals': erc20_data.get('decimals', 18),
        'contract_owner': erc20_data.get('owner'),
        'fee_receiver': None,  # only populate from a verified named getter
        'storage_address_candidates': storage_addresses,
        
        # Bytecode Forensics
        'compiler_version': compiler_version,
        'bytecode_sha256': sha256_hash,
        'bytecode_md5': md5_hash,
        'normalized_bytecode_sha256': normalized_hash,
        'bytecode_size_bytes': len(_hex_to_bytes(bytecode)) if bytecode else None,
        'metadata_hash': hashlib.sha256(cbor_metadata).hexdigest() if cbor_metadata else None,
        'metadata_length_bytes': len(cbor_metadata) if cbor_metadata else 0,
        'method_ids_hash': method_ids_hash,
        'function_selectors': selectors_str,
        'function_selector_count': len(selectors_str.split(',')) if selectors_str else 0,
        
        # Execution & Creation
        'deployer_address': creation_data.get('deployer_address'),
        'creation_tx_hash': creation_data.get('creation_tx_hash'),
        'creation_block': creation_data.get('creation_block'),
        'creation_gas_used': creation_data.get('creation_gas_used'),
        'creation_tx_fee': creation_data.get('creation_tx_fee'),
        'creation_method': creation_data.get('creation_method'),
        'creation_timestamp': creation_data.get('creation_timestamp'),
        'creation_nonce': creation_data.get('creation_nonce'),
        'gas_price_gwei': creation_data.get('gas_price_gwei'),
        'max_fee_gwei': creation_data.get('max_fee_gwei'),
        'priority_fee_gwei': creation_data.get('priority_fee_gwei'),
        'deployer_initial_capital': creation_data.get('deployer_initial_capital'),
        'deployer_initial_snipe': creation_data.get('deployer_initial_snipe'),
        'deployer_initial_snipe_raw': creation_data.get('deployer_initial_snipe_raw'),
        'contract_factory': creation_data.get('contract_factory'),
        'deployer_balance_eth': creation_data.get('deployer_balance_eth'),
        
        # Funder Lineage
        'funder_address': creation_data.get('funder_address'),
        'funder_name': creation_data.get('funder_name'),
        'funder_hop2_address': creation_data.get('funder_hop2_address'),
        'funder_hop2_name': creation_data.get('funder_hop2_name'),
        'funding_lineage': creation_data.get('funding_lineage', []),
        'funding_tx_hash': creation_data.get('funding_tx_hash'),
        'first_funder_address': creation_data.get('first_funder_address'),
        'first_funded_timestamp': creation_data.get('first_funded_timestamp'),
        'funding_count_before_deploy': creation_data.get('funding_count_before_deploy'),
        'funding_total_eth_before_deploy': creation_data.get('funding_total_eth_before_deploy'),
        'eth_received': creation_data.get('eth_received'),
        'funded_timestamp': creation_data.get('funded_timestamp'),
        'wallet_age_at_deploy': creation_data.get('wallet_age_at_deploy'),
        'wallet_age_at_deploy_seconds': creation_data.get('wallet_age_at_deploy_seconds'),
        'setup_time_seconds': creation_data.get('setup_time_seconds'),
        
        # Embedded Socials & IPFS
        'ipfs_metadata': c_args.get('ipfs_metadata'),
        'description': c_args.get('description'),
        'twitter_url': c_args.get('twitter'),
        'website_url': c_args.get('website'),
        'hardcoded_addresses': c_args.get('hardcoded_addresses', []),
        'printable_constructor_strings': c_args.get('printable_strings', []),
        'embedded_strings_hash': c_args.get('embedded_strings_hash')
    }

    return metadata
