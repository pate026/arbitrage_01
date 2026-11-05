# arbitrage_01

a high-performance, real-time multi-chain dex arbitrage scanner built in python. the system discovers active liquidity pools, monitors price discrepancies across decentralized exchanges, and simulates cross-dex flash loan arbitrage opportunities using multicall3 and exact off-chain swap math.

---

## architecture & features

- **chain-agnostic rpc & multicall3 layer:** batches hundreds of pool queries into a single rpc call per block (`aggregate3`), minimizing network bandwidth and eliminating rpc rate limits.
- **automatic pool discovery:** inspects dex factory contracts (`univ2` and `univ3`) for registered token pairs and caches verified addresses locally.
- **accurate off-chain swap math:**
  - **uniswap v2:** constant product formula ($x \cdot y = k$) including fee deductions and price impact / slippage calculation.
  - **uniswap v3:** single-tick concentrated liquidity model using $q96$ fixed-point arithmetic (`sqrtpricex96` and `liquidity`).
- **fee & net profit modeling:** automatically accounts for aave v3 flash loan fees (0.05%) and converts dynamic gas costs into the borrowed token denomination.
- **opportunity simulator:** evaluates multiple borrowing tiers per block (e.g. $500, $1000, $2500, $5000) to account for pool depth and slippage saturation.
- **data persistence & payload formatting:** logs all profitable opportunities to csv (`data/logs/opportunities.csv`) and builds standardized execution payloads for future smart contract execution.

---

## project structure

```text
arbitrage_01/
├── configs/
│   ├── settings.json            # general scanner limits, gas limits, and test amounts
│   └── chains/
│       └── base.json            # chain-specific addresses (multicall, tokens, factories)
├── data/
│   ├── cache/                   # cached discovered pool addresses
│   └── logs/                    # recorded arbitrage opportunities (.csv)
├── src/
│   ├── config_loader.py         # configuration and environment validator
│   ├── discovery/
│   │   └── pool_finder.py       # on-chain factory discovery and pool cache builder
│   ├── engine/
│   │   ├── payload.py           # execution payload generator for smart contracts
│   │   ├── route_builder.py     # constructs 2-hop cyclic trading paths
│   │   └── simulator.py         # batch state fetching and profit simulation
│   ├── math/
│   │   ├── fee_engine.py        # aave loan fee and gas cost calculator
│   │   ├── univ2_math.py        # v2 constant product reserves calculation
│   │   └── univ3_math.py        # v3 concentrated liquidity tick & sqrtprice math
│   ├── rpc/
│   │   ├── multicall.py         # multicall3 aggregate3 execution wrapper
│   │   └── provider.py          # async web3 rpc provider
│   └── utils/
│       └── logger.py            # csv persistent opportunity logger
├── .env.example
├── .gitignore
├── main.py                      # live block scanning loop
├── requirements.txt
└── README.md
```

---

## setup & installation

### 1. clone repository & create virtual environment

```bash
git clone https://github.com/pate026/arbitrage_01.git
cd arbitrage_01

python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 2. configure environment variables

copy the example environment file and fill in your rpc endpoints:

```bash
cp .env.example .env
```

edit `.env`:
```ini
BASE_RPC_HTTP=https://mainnet.base.org
BASE_RPC_WSS=wss://base-rpc.publicnode.com
```

---

## usage

### discover pools
discover active dex pools on the target chain and save them to local cache:

```bash
python -m src.discovery.pool_finder
```

### run direct route simulation
run a single batch scan over current chain state:

```bash
python -m src.engine.simulator
```

### start live scanner
start the real-time block listener:

```bash
python main.py --chain base
```

output sample:
```text
block #53550162 scanned in 1.31s | gas: 0.0066 gwei | opportunities: 5
  -> [PROFITABLE] USDC | borrow: $1000 | net profit: $318.18 | route: uniswap_v3 -> uniswap_v3
  -> [PROFITABLE] cbETH | borrow: $1000 | net profit: $236.21 | route: uniswap_v3 -> uniswap_v3
```

---

## license

mit