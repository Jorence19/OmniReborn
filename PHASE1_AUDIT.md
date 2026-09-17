# Phase 1 audit: qualified-token developer fingerprints

## Goal

Learn as many auditable developer fingerprints and repeatable habits as possible from explicitly qualified tokens, then apply those fingerprints to the wider token corpus. Phase 1 produces research leads; it does not treat a similarity score as proof of common ownership.

## Qualification boundary

Only rows from `ca_matching_RBH_*.csv` are marked qualified because that file is the labeled matching set. Telegram-scan and bundle rows remain unqualified unless an operator explicitly qualifies them. This prevents the pipeline from training on its own guesses.

Current corpus:

- 248 total tokens
- 75 explicitly qualified tokens
- 32 recurring qualified fingerprints
- 74 wider-corpus candidates at `WEAK`, `WATCH`, `PROBABLE_LEAD`, or `HIGH_LEAD` level

## What was corrected

- Missing gas, nonce, value, and bundle observations no longer become zero-valued habits.
- Direct developer-wallet reuse is now strong identity evidence.
- Common launchpad templates and method selectors are rarity-adjusted supporting evidence, not developer identity.
- Runtime hashes now hash bytecode bytes, not the printable hexadecimal string.
- A normalized runtime hash removes the Solidity CBOR metadata trailer; the raw hash and metadata hash are retained separately.
- Solidity compiler extraction now reads the CBOR `solc` field.
- Function selectors are parsed from EVM `PUSH4 ... EQ` instruction sequences instead of a loose regular expression.
- Address-shaped storage words are retained as candidates with slot provenance; they are no longer asserted to be fee receivers.
- The launch funder is the last positive inbound native transfer before deployment. First funding, funding count, total pre-launch funding, setup delay, and up to four lineage hops are retained separately.
- “Wallet age at deploy” now means first observed activity to deployment, rather than funding time to the present day.
- Initial token receipts are restricted to `Transfer` logs emitted by the deployed token and use the token's real decimals.
- Constructor IPFS, URLs, copy, hardcoded-address candidates, and printable-string hashes are bounded and preserved.
- Existing labeled matches are protected from bulk recomputation.
- SQLite connections are closed reliably on Windows.
- Embedded explorer credentials were removed; configure them through environment variables using `.env.example` as a reference.

## Fingerprints and habits captured

The indexed profile covers developer, funder, multi-hop lineage, funding amount/count/timing, creation method, nonce, gas profile, initial capital and snipe, factory, bundler/buyer wallets, raw and normalized bytecode, compiler metadata, selector set, website/domain/host/favicon, Telegram and X handles/naming patterns, description style, launch hour/day, holder/bundle ratios, boosts, ads, and other promotion behavior. A lossless JSON profile preserves raw clues and evidence coverage for future feature work.

## Current high-value findings

The report confirms several team-specific recurring patterns:

- `team ROBINARY`: one funder and one template recur across all 5 labeled tokens; a 1.9994 funding amount recurs in 4/5.
- `team gigalon`: method `ca20cf82` and one template recur across all 14 labeled tokens. Because these can be launchpad defaults, they remain supporting evidence.
- `team NCHIP`: method `c1d28584` recurs across all 4 tokens; one funder and a 1 ETH launch value recur in 3/4.
- `team astro`: method `434d13e2` appears in 30/31; a 0.0005 ETH launch value appears in 10/31; multiple funder clusters and gas habits recur.
- Two labeled tokens directly reuse a developer wallet, the strongest identity class in the current historical data.

Top unqualified leads include `LUCKY`, `XL`, `HIPPO`, and `WORM`, driven primarily by qualified funder reuse plus the 0.0005 ETH habit and matching launch execution. These are candidates for manual/chain validation, not confirmed team assignments.

## Commands

```powershell
# Rebuild the qualified-to-universe audit and all CSV exports
python phase1.py audit --output phase1_fingerprint_report.json

# Enrich one token from live chain/explorer data and preserve all evidence
$env:ROBIN_ETHERSCAN_API_KEY = "..."
python phase1.py enrich 0xTOKEN --chain-id 4663

# Only add --qualified after the token passes your independent qualification rule
python phase1.py enrich 0xTOKEN --chain-id 4663 --qualified

# Offline validation
python -m unittest discover -v
```

Generated outputs:

- `phase1_fingerprint_report.json`: complete machine-readable audit
- `phase1_fingerprint_report_fingerprints.csv`: recurring fingerprints, global prevalence, qualified precision, and team purity
- `phase1_fingerprint_report_candidates.csv`: unqualified token leads with tier and evidence
- `phase1_fingerprint_report_team_habits.csv`: recurring habits and coverage within each labeled team
