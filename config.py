import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env", override=False)
except ImportError:
    pass

# Blockchain API Keys
ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY", "")
BASESCAN_API_KEY = os.getenv("BASESCAN_API_KEY", "")
ROBIN_ETHERSCAN_API_KEY = os.getenv("ROBIN_ETHERSCAN_API_KEY", "")
BSCSCAN_API_KEY = os.getenv("BSCSCAN_API_KEY", "")

# RPC Endpoints by Chain ID
# 4663: Robinhood Chain (EVM)
# 8453: Base L2
# 1: Ethereum Mainnet
RPC_ENDPOINTS = {
    56: os.getenv("BSC_RPC_URL", "https://bsc-dataseed.bnbchain.org"),
    4663: os.getenv("ROBINHOOD_RPC_URL", "https://rpc.mainnet.chain.robinhood.com"),
    5042: os.getenv("ARC_RPC_URL", "https://rpc.mainnet.arc.io"),
    8453: os.getenv("BASE_RPC_URL", "https://mainnet.base.org"),
    1: os.getenv("ETH_RPC_URL", "https://cloudflare-eth.com"),
}

CHAIN_METADATA = {
    56: {
        "name": "BNB Smart Chain", "tag": "BSC", "dex_chain_id": "bsc",
        "explorer_url": "https://bscscan.com",
        "native_symbol": "BNB",
        "public_rpc": "https://bsc-dataseed.bnbchain.org",
    },
    4663: {
        "name": "Robinhood Chain", "tag": "RBH", "dex_chain_id": "robinhood",
        "explorer_url": "https://robinhoodchain.blockscout.com",
        "native_symbol": "ETH",
        "public_rpc": "https://rpc.mainnet.chain.robinhood.com",
    },
    5042: {
        "name": "Arc", "tag": "ARC", "dex_chain_id": "arc",
        "explorer_url": "https://explorer.arc.io",
        "explorer_api_url": os.getenv("ARC_EXPLORER_API_URL", "https://api.arc-scan.org/api"),
        "native_symbol": "USDC",
        "public_rpc": "https://rpc.mainnet.arc.io",
    },
    8453: {
        "name": "Base", "tag": "BASE", "dex_chain_id": "base",
        "explorer_url": "https://basescan.org", "native_symbol": "ETH",
        "public_rpc": "https://mainnet.base.org",
    },
    1: {
        "name": "Ethereum", "tag": "ETH", "dex_chain_id": "ethereum",
        "explorer_url": "https://etherscan.io", "native_symbol": "ETH",
        "public_rpc": "https://cloudflare-eth.com",
    },
}

DEFAULT_CHAIN_ID = 4663

# Known Centralized Exchanges, Bridges, and Identified Wallets
KNOWN_EXCHANGES = {
    'Binance': [
        '0x4976a4a02f38326660d17bf34b431dc6e2eb2327', '0x3f5ce5fbfe3e9af3971dd833d26ba9b5c936f0be',
        '0xd551234ae421e3bcba99a0da6d736074f22192ff', '0x564286362092d8e7936f0549571a803b203aaced',
        '0x0681d8db095565fe8a346fa0277bffde9c0edbbf', '0xfe9e8709d3215310075d67e3ed32a380ccf451c8',
        '0x4e9ce36e442e55ecd9025b9a6e0d88485d628a67', '0xbe0eb53f46cd790cd13851d5eff43d12404d33e8',
        '0xf977814e90da44bfa03b6295a0616a897441acec', '0x001866ae5b3de6caa5a51543fd9fb64f524f5478',
        '0x85b931a32a0725be14285b66f1a22178c672d69b', '0x708396f17127c42383e3b9014072679b2f60b82f',
        '0xe0f0cfde7ee664943906f17f7f14342e76a5cec7', '0x8f22f2063d253846b53609231ed80fa571bc0c8f',
        '0x28c6c06298d514db089934071355e5743bf21d60', '0x21a31ee1afc51d94c2efccaa2092ad1028285549',
        '0xdfd5293d8e347dfe59e90efd55b2956a1343963d', '0x56eddb7aa87536c09ccc2793473599fd21a8b17f',
        '0x9696f59e4d72e237be84ffd425dcad154bf96976', '0x4d9ff50ef4da947364bb9650892b2554e7be5e2b'
    ],
    'Coinbase': [
        '0x20FE51A9229EEf2cF8Ad9E89d91CAb9312cF3b7A', '0x97b9D2102A9a65A26E1EE82D59e42d1B73B68689',
        '0xB4807865A786E9E9E26E6A9610F2078e7fc507fB', '0xb739D0895772DBB71A89A3754A160269068f0D45',
        '0xb5d85cbf7cb3ee0d56b3bb207d5fc4b82f43f511'
    ],
    'Bybit': [
        '0xf89d7b9c864f589bbf53a82105107622b35eaa40', '0xBaeD383EDE0e5d9d72430661f3285DAa77E9439F'
    ],
    'Kucoin': [
        '0x2b5634c42055806a59e9107ed44d43c426e58258', '0x689c56aef474df92d44a1b70850f808488f9769c',
        '0xa1d8d972560c2f8144af871db508f0b0b10a3fbf', '0x4ad64983349c49defe8d7a4686202d24b25d0ce8',
        '0x1692e170361cefd1eb7240ec13d048fd9af6d667', '0xd6216fc19db775df9774a6e33526131da7d19a2c',
        '0xe59cd29be3be4461d79c0881d238cbe87d64595a', '0x899b5d52671830f567bf43a14684eb14e1f945fe',
        '0xf16e9b0d03470827a95cdfd0cb8a8a3b46969b91', '0xcad621da75a66c7a8f4ff86d30a2bf981bfc8fdd',
        '0xec30d02f10353f8efc9601371f56e808751f396f', '0x738cf6903e6c4e699d1c2dd9ab8b67fcdb3121ea',
        '0xd89350284c7732163765b23338f2ff27449e0bf5', '0x45300136662dD4e58fc0DF61E6290DFfD992B785'
    ],
    'MEXC': [
        '0x75e89d5979e4f6fba9f97c104c2f0afb3f1dcb88'
    ],
    'Kraken': [
        '0x267be1c1d684f78cb4f6a176c4911b741e4ffdc0'
    ],
    'Gate.io': [
        '0x0d0707963952f2fba59dd06f2b425ace40b492fe'
    ],
    'FixedFloat': [
        '0x4e5b2e1dc63f6b91cb6cd759936495434c7e972f'
    ],
    'SideShift': [
        '0xcDd37Ada79F589c15bD4f8fD2083dc88E34A2af2'
    ],
    'ChangeNow': [
        '0x077D360f11D220E4d5D831430c81C26c9be7C4A4', '0xeba88149813bec1cccccfdb0dacefaaa5de94cb1'
    ],
    'RhinoFi': [
        '0x8D5324A55a912E9F9dbE9eFbCE50dFEac8e48F71', '0xc3CA38091061e3E5358A52d74730F16C60cA9c26'
    ],
    'BaseL2Bridge': [
        '0x4200000000000000000000000000000000000010'
    ],
    'Bitget': [
        '0x97b9D2102A9a65A26E1EE82D59e42d1B73B68689'
    ],
    'OrbFi': [
        '0x80C67432656d59144cEFf962E8fAF8926599bCF8', '0xE4eDb277e41dc89aB076a1F049f4a3EfA700bCE8'
    ]
}
