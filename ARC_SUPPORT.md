# Arc mainnet support

## Status

Arc mainnet support is implemented for chain ID `5042`. The same collector can run Robinhood (`4663`) and Arc concurrently with `ENABLED_CHAIN_IDS=4663,5042`.

A live read-only probe of `0x2ECd91a0bc0F23670dfE947fd1490Ed8D26BB4d5` verified token metadata, deployer, funder, creation transaction, and bytecode extraction through Arc RPC plus Etherscan V2.

Production deployment is blocked until private/archive-capable RPC endpoints are configured for both enabled chains. The official public RPCs are accepted for smoke tests but rejected by `streamer.py --preflight`.

## Implemented path

- Official Arc mainnet identity: chain ID `5042`, public RPC `https://rpc.mainnet.arc.io`, explorer `https://explorer.arc.io`, native gas asset USDC.
- Chain-aware durable jobs and event provenance.
- DexScreener Arc paid/profile/ads and dated-search discovery.
- Etherscan V2 creation and transaction history using `chainid=5042`.
- Arc RPC bytecode and contract calls.
- Chain-aware database rows, Phase 1 reports, API validation, dashboard filter, explorer links, and DexScreener links.
- Cross-chain scoring excludes native-denominated fields because Robinhood uses ETH and Arc uses USDC.
- Live candidates never become qualified training anchors automatically.
- Retry, lease recovery, dead-letter, atomic snapshot, watchdog, and backup behavior is unchanged.

## Coverage boundary

This is not an exhaustive Arc token or contract index.

Arc discovery currently begins with DexScreener paid/profile/ads feeds and dated search. Tokens absent from those surfaces may not be discovered. Exhaustive coverage requires a verified Arc factory or PoolManager event source, or a full contract-creation indexer.

The legacy forensic profile tables are keyed by contract address. If the identical 20-byte address appears on Robinhood and Arc, the registry rejects the second row and the job retries/dead-letters instead of overwriting evidence. That is corruption-safe, but literal all-address coverage requires a future composite `(chain_id, ca)` schema migration.

## Production configuration

```dotenv
ENABLED_CHAIN_IDS=4663,5042
ROBINHOOD_RPC_URL=https://YOUR_PRIVATE_ROBINHOOD_ARCHIVE_RPC
ARC_RPC_URL=https://YOUR_PRIVATE_ARC_ARCHIVE_RPC
ROBIN_ETHERSCAN_API_KEY=YOUR_ETHERSCAN_V2_KEY
# Optional dedicated override. Arc otherwise reuses the unified key above.
ETHERSCAN_API_KEY=
```

Validate before enabling services:

```bash
python streamer.py --preflight
python -m unittest discover -v
python streamer.py --backfill 10 --max-jobs 5
python streamer.py --status
```

The preflight must show both RPC chain checks and both production RPC checks passing.

## Official references

- Arc network parameters: https://docs.arc.io/arc/references/connect-to-arc
- Arc node providers: https://docs.arc.io/arc/tools/node-providers
- ArcScan and Etherscan API support: https://info.etherscan.com/what-is-arcscan/
