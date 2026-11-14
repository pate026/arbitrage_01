import json
import asyncio
from pathlib import Path
from typing import Dict, List, Any
from eth_abi import encode, decode
from web3 import Web3

from src.config_loader import Config
from src.rpc.provider import RPCProvider
from src.rpc.multicall import Multicall

# factory contract function selectors
# uniswap v2: getPair(address tokenA, address tokenB) -> address
V2_GET_PAIR_SELECTOR = bytes.fromhex("e6a43905")
# uniswap v3: getPool(address tokenA, address tokenB, uint24 fee) -> address
V3_GET_POOL_SELECTOR = bytes.fromhex("1698ee82")
# aerodrome classic / solidly: getPool(address tokenA, address tokenB, bool stable) -> address
# keccak256("getPool(address,address,bool)")[:4] -> 0x16b0d389
AERO_V2_GET_POOL_SELECTOR = bytes.fromhex("16b0d389")
# aerodrome slipstream cl: getPool(address tokenA, address tokenB, int24 tickSpacing) -> address
# keccak256("getPool(address,address,int24)")[:4] -> 0xb43126f2
AERO_CL_GET_POOL_SELECTOR = bytes.fromhex("b43126f2")

# typical fee mappings for aerodrome slipstream tickspacings (in uniswap v3 fee units: millionths)
AERO_TICKSPACING_FEES: Dict[int, int] = {
    1: 100,      # 0.01%
    10: 500,     # 0.05%
    50: 2500,    # 0.25%
    100: 3000,   # 0.30%
    200: 10000,  # 1.00%
    2000: 30000, # 3.00%
}


class PoolFinder:
    def __init__(self, config: Config, multicall: Multicall):
        self.config = config
        self.multicall = multicall
        self.cache_dir = Path(self.config.settings["cache"]["cache_dir"])
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_file = self.cache_dir / f"{self.config.chain_name}_pools.json"

    async def find_pools(self, force_refresh: bool = False) -> Dict[str, Any]:
        """find active pools from factory contracts and save them to the cache."""
        if not force_refresh and self.cache_file.exists():
            with open(self.cache_file, "r", encoding="utf-8") as f:
                return json.load(f)

        print("[info] discovering active pools with a multicall request...")
        tokens = list(self.config.chain["tokens"].values())
        token_addresses = [t["address"] for t in tokens]

        calls = []
        call_context = []  # metadata attached to each call

        # generate unique token pair permutations
        for i in range(len(token_addresses)):
            for j in range(i + 1, len(token_addresses)):
                token_a = token_addresses[i]
                token_b = token_addresses[j]

                for dex_name, dex_data in self.config.chain["dexes"].items():
                    factory = dex_data["factory"]
                    dex_type = dex_data["type"]

                    if dex_type == "univ2":
                        call_data = V2_GET_PAIR_SELECTOR + encode(["address", "address"], [token_a, token_b])
                        calls.append((factory, True, call_data))
                        call_context.append((dex_name, "univ2", token_a, token_b, dex_data.get("fee_bps", 30)))

                    elif dex_type == "aerodrome_v2":
                        stable = dex_data.get("stable", False)
                        fee_bps = dex_data.get("fee_bps", 30)
                        call_data = AERO_V2_GET_POOL_SELECTOR + encode(["address", "address", "bool"], [token_a, token_b, stable])
                        calls.append((factory, True, call_data))
                        # internal simulation for volatile aerodrome behaves identically to univ2 (x * y = k)
                        call_context.append((dex_name, "univ2", token_a, token_b, fee_bps))

                    elif dex_type == "univ3":
                        for fee in dex_data.get("fee_tiers", []):
                            call_data = V3_GET_POOL_SELECTOR + encode(["address", "address", "uint24"], [token_a, token_b, fee])
                            calls.append((factory, True, call_data))
                            call_context.append((dex_name, "univ3", token_a, token_b, fee))

                    elif dex_type == "aerodrome_cl":
                        for tick_spacing in dex_data.get("tick_spacings", [1, 10, 50, 100, 200, 2000]):
                            call_data = AERO_CL_GET_POOL_SELECTOR + encode(["address", "address", "int24"], [token_a, token_b, tick_spacing])
                            fee = AERO_TICKSPACING_FEES.get(tick_spacing, 3000)
                            calls.append((factory, True, call_data))
                            # aerodrome cl uses identical slot0/liquidity/swap formulas as univ3
                            call_context.append((dex_name, "univ3", token_a, token_b, fee))

        results = await self.multicall.aggregate3(calls)

        pools: Dict[str, List[Dict[str, Any]]] = {}
        zero_address = "0x0000000000000000000000000000000000000000"

        for ctx, (success, return_data) in zip(call_context, results):
            if success and len(return_data) == 32:
                pool_address = Web3.to_checksum_address(decode(["address"], return_data)[0])
                if pool_address != zero_address:
                    dex_name, normalized_type, t_a, t_b, fee = ctx
                    if dex_name not in pools:
                        pools[dex_name] = []

                    pool_info = {
                        "pool_address": pool_address,
                        "type": normalized_type,
                        "token0": t_a,
                        "token1": t_b,
                        "fee": fee,
                    }
                    pools[dex_name].append(pool_info)

        with open(self.cache_file, "w", encoding="utf-8") as f:
            json.dump(pools, f, indent=4)

        return pools


if __name__ == "__main__":
    async def main():
        cfg = Config("base")
        prov = RPCProvider(cfg)
        mc = Multicall(prov)
        finder = PoolFinder(cfg, mc)

        pools = await finder.find_pools(force_refresh=True)

        total_pools = 0
        for dex, dex_pools in pools.items():
            count = len(dex_pools)
            total_pools += count
            print(f" - {dex}: {count} active pools")

        print(f"[ok] found and cached {total_pools} pools in total ({finder.cache_file.name}).")

    asyncio.run(main())