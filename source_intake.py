"""Parse forwarded token notices without trusting their marketing claims."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

ADDRESS_RE = re.compile(r"(?<![0-9a-fA-F])0x[0-9a-fA-F]{40}(?![0-9a-fA-F])")

DEV_LABEL_RE = re.compile(
    r"(?:dev(?:eloper)?|deployer|creator|owner)\s*(?:wallet|address)?\s*[:\-=]\s*(0x[0-9a-fA-F]{40})",
    re.IGNORECASE,
)
PAIR_LABEL_RE = re.compile(
    r"(?:pair|pool|lp)\s*(?:address)?\s*[:\-=]\s*(0x[0-9a-fA-F]{40})",
    re.IGNORECASE,
)
TOKEN_LABEL_RE = re.compile(
    r"(?:token|ca|contract)\s*(?:address)?\s*[:\-=]\s*(0x[0-9a-fA-F]{40})",
    re.IGNORECASE,
)

GMGN_TOKEN_URL_RE = re.compile(
    r"gmgn\.ai/[a-zA-Z0-9_\-]+/token/[^/?#]*?(0x[0-9a-fA-F]{40})",
    re.IGNORECASE,
)
DEXSCREENER_TOKEN_URL_RE = re.compile(
    r"dexscreener\.com/tokens/v1/[a-zA-Z0-9_\-]+/(0x[0-9a-fA-F]{40})",
    re.IGNORECASE,
)
DEXSCREENER_PAIR_URL_RE = re.compile(
    r"dexscreener\.com/(?!tokens/)[a-zA-Z0-9_\-]+/(0x[0-9a-fA-F]{40})",
    re.IGNORECASE,
)
EXPLORER_TOKEN_URL_RE = re.compile(
    r"(?:scan|explorer)[^/\s]+/token/(0x[0-9a-fA-F]{40})",
    re.IGNORECASE,
)

TICKER_LINE_RE = re.compile(
    r"^\s*([A-Za-z0-9$]{2,15})\s*\|\s*(.+)$",
    re.MULTILINE,
)
SYMBOL_LABEL_RE = re.compile(
    r"(?:symbol|ticker)\s*[:\-=]\s*[$]?([^\s,;|]{1,32})",
    re.IGNORECASE,
)
DOLLAR_TICKER_RE = re.compile(
    r"\$([^\s,;|]{1,32})",
)
PONS_QUOTE_RE = re.compile(r"\bQuote\s*:\s*([^\r\n]+)", re.IGNORECASE)
PONS_TAX_RE = re.compile(r"\bTax\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*%", re.IGNORECASE)
PONS_AGE_RE = re.compile(r"\bSince\s+Creation\s*:\s*([^\r\n]+)", re.IGNORECASE)
DURATION_PART_RE = re.compile(r"([0-9]+)\s*(seconds?|minutes?|hours?)\b", re.IGNORECASE)
BSC_NAME_RE = re.compile(r"\bName\s*:\s*(.+?)(?:\s*\(([^()\r\n]{1,32})\))?\s*(?:\r?\n|\bSymbol\s*:)", re.IGNORECASE)
URL_RE = re.compile(r"https?://[^\s\])>]+", re.IGNORECASE)


@dataclass(frozen=True)
class ForwardedToken:
    ca: str
    chain_id: int | None
    source_kind: str
    dev_wallet: str | None = None
    pair_address: str | None = None
    symbol: str | None = None
    name: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def extract_addresses(text: str) -> list[str]:
    """Return unique EVM addresses from prose, Markdown, or referral URLs."""
    seen: set[str] = set()
    result: list[str] = []
    for match in ADDRESS_RE.finditer(str(text or "")):
        address = match.group(0).lower()
        if address not in seen:
            seen.add(address)
            result.append(address)
    return result


def detect_chain(text: str) -> int | None:
    value = str(text or "").lower()
    bsc_markers = ("bscscan.com", "flap.sh/bnb", "dexscreener.com/bsc", "gmgn.ai/bsc", "chainid=56")
    rbh_markers = ("robinhood", "ponsfamily.com", "fomo.family", "chainid=4663")
    arc_markers = ("arc.io", "arc-scan", "dexscreener.com/arc", "gmgn.ai/arc", "chainid=5042")
    hits = [
        chain_id for chain_id, markers in ((56, bsc_markers), (4663, rbh_markers), (5042, arc_markers))
        if any(marker in value for marker in markers)
    ]
    return hits[0] if len(hits) == 1 else None


def is_flap_bsc_migration_template(text: str) -> bool:
    """Recognize the stable Flap BSC migration message shape, not its claims."""
    value = str(text or "").lower()
    return (
        "new token migration detected" in value
        and "flap.sh/bnb/" in value
        and "contract address" in value
    )


def source_kind(text: str, chain_id: int | None) -> str:
    value = str(text or "").lower()
    if chain_id == 4663 and "pons" in value and "migration" in value:
        return "forwarded_pons_migration"
    if chain_id == 5042:
        return "forwarded_arc_token"
    if chain_id == 56:
        return "forwarded_bsc_token"
    return "forwarded_unclassified"


def template_metadata(text: str) -> dict[str, Any]:
    """Extract supported forward-template fields as untrusted source provenance."""
    raw = str(text or "")
    metadata: dict[str, Any] = {}
    quote = PONS_QUOTE_RE.search(raw)
    if quote:
        metadata["reported_quote_asset"] = quote.group(1).strip()
    tax = PONS_TAX_RE.search(raw)
    if tax:
        metadata["reported_tax_percent"] = float(tax.group(1))
    age = PONS_AGE_RE.search(raw)
    if age:
        seconds = 0
        for amount, unit in DURATION_PART_RE.findall(age.group(1)):
            multiplier = 1 if unit.lower().startswith("second") else 60 if unit.lower().startswith("minute") else 3600
            seconds += int(amount) * multiplier
        if seconds:
            metadata["reported_age_seconds"] = seconds
    bsc_name = BSC_NAME_RE.search(raw)
    if bsc_name:
        metadata["reported_name"] = bsc_name.group(1).strip()
        if bsc_name.group(2):
            metadata["reported_symbol"] = bsc_name.group(2).upper()
    urls = []
    for url in URL_RE.findall(raw):
        clean = url.rstrip(".,;:}")
        if clean not in urls:
            urls.append(clean)
    for url in urls:
        lowered = url.lower()
        if "ponsfamily.com" in lowered:
            metadata.setdefault("pons_url", url)
        elif "gmgn.ai" in lowered:
            metadata.setdefault("gmgn_url", url)
        elif "dexscreener.com" in lowered:
            metadata.setdefault("dexscreener_url", url)
        elif "fomo.family" in lowered:
            metadata.setdefault("fomo_url", url)
        elif "x.com/" in lowered or "twitter.com/" in lowered:
            metadata.setdefault("source_x_url", url)
        elif not any(domain in lowered for domain in ("bscscan.com", "arc-scan", "explorer.arc.io", "blockscout.com", "okx.com", "padre.gg", "defined.fi", "dextools.io", "axiom.trade", "t.me/")):
            metadata.setdefault("source_website", url)
    return metadata

def parse_forwarded_tokens(text: str) -> list[ForwardedToken]:
    raw_text = str(text or "")
    chain_id = detect_chain(raw_text)
    kind = source_kind(raw_text, chain_id)
    all_addresses = extract_addresses(raw_text)
    if not all_addresses:
        return []

    # 1. Check for labeled dev addresses
    dev_wallets = [m.group(1).lower() for m in DEV_LABEL_RE.finditer(raw_text)]
    dev_wallet = dev_wallets[0] if dev_wallets else None

    # 2. Check for labeled pair addresses
    pair_addresses = [m.group(1).lower() for m in PAIR_LABEL_RE.finditer(raw_text)]
    chart_addresses = [m.group(1).lower() for m in DEXSCREENER_PAIR_URL_RE.finditer(raw_text)]

    # 3. Check for explicitly labeled token addresses or token URLs
    explicit_tokens = [m.group(1).lower() for m in TOKEN_LABEL_RE.finditer(raw_text)]
    for m in GMGN_TOKEN_URL_RE.finditer(raw_text):
        explicit_tokens.append(m.group(1).lower())
    for m in DEXSCREENER_TOKEN_URL_RE.finditer(raw_text):
        explicit_tokens.append(m.group(1).lower())
    for m in EXPLORER_TOKEN_URL_RE.finditer(raw_text):
        explicit_tokens.append(m.group(1).lower())

    # A DexScreener /chain/0x… link is ambiguous: it can represent either a
    # token page or a pair page. Treat it as a pair only when another address
    # identifies the token; otherwise keep the sole address eligible.
    if explicit_tokens or len(set(all_addresses) - set(chart_addresses)):
        pair_addresses.extend(address for address in chart_addresses if address not in explicit_tokens)
    pair_address = pair_addresses[0] if pair_addresses else None

    # 4. Extract symbol and name if present
    template = template_metadata(raw_text)
    symbol, name = None, None
    ticker_line_match = TICKER_LINE_RE.search(raw_text)
    if ticker_line_match:
        symbol = ticker_line_match.group(1).strip().lstrip("$").upper()
        name = ticker_line_match.group(2).strip()
    else:
        sym_match = SYMBOL_LABEL_RE.search(raw_text)
        if sym_match:
            symbol = sym_match.group(1).strip().upper()
        else:
            dollar_match = DOLLAR_TICKER_RE.search(raw_text)
            if dollar_match:
                symbol = dollar_match.group(1).strip().upper()
    symbol = symbol or template.get("reported_symbol")
    name = name or template.get("reported_name")

    # 5. Disambiguate token candidates
    non_token_addrs = set(dev_wallets) | set(pair_addresses)
    candidate_tokens: list[str] = []
    if explicit_tokens:
        for ca in explicit_tokens:
            if ca not in candidate_tokens:
                candidate_tokens.append(ca)
    else:
        for addr in all_addresses:
            if addr not in non_token_addrs and addr not in candidate_tokens:
                candidate_tokens.append(addr)

    # 6. If only dev address exists without other tokens, treat as dev-seeded
    if not candidate_tokens and dev_wallet:
        return [
            ForwardedToken(
                ca=dev_wallet,
                chain_id=chain_id,
                source_kind=kind,
                dev_wallet=dev_wallet,
                pair_address=pair_address,
                symbol=symbol,
                name=name,
                metadata={"is_dev_seed": True, "dev_wallet": dev_wallet},
            )
        ]

    results = []
    for ca in candidate_tokens:
        meta = dict(template)
        if dev_wallet:
            meta["dev_wallet"] = dev_wallet
        if pair_address:
            meta["pair_address"] = pair_address
        results.append(
            ForwardedToken(
                ca=ca,
                chain_id=chain_id,
                source_kind=kind,
                dev_wallet=dev_wallet,
                pair_address=pair_address,
                symbol=symbol,
                name=name,
                metadata=meta,
            )
        )
    return results


def forwarded_text(message: dict) -> str:
    """Extract text plus explicit Telegram text-link URLs from a forward or reply."""
    parts: list[str] = []
    for item in ((message.get("reply_to_message") or {}), message):
        for field in ("text", "caption"):
            value = item.get(field)
            if value:
                parts.append(str(value))
        for field in ("entities", "caption_entities"):
            for entity in item.get(field) or []:
                if isinstance(entity, dict) and entity.get("url"):
                    parts.append(str(entity["url"]))
    return "\n".join(parts)


def is_forwarded_message(message: dict) -> bool:
    """True only for Telegram-forwarded messages, not ordinary group chatter."""
    return bool(
        message.get("forward_origin")
        or message.get("forward_from")
        or message.get("forward_from_chat")
        or message.get("is_automatic_forward")
    )
