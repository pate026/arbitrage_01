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
# uniswap v2: getPair(address tokenA, address tokenB)
V2_GET_PAIR_SELECTOR = bytes.fromhex("e6a43905")
# uniswap v3: getPool(address tokenA, address tokenB, uint24 fee)
V3_GET_POOL_SELECTOR = bytes.fromhex("1698ee82")

class PoolFinder:
    def __init__(self, config: Config, multicall: Multicall):
        self.config = config
        self.multicall = multicall
        self.cache_dir = Path(self.config.settings["cache"]["cache_dir"])
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_file = self.cache_dir / f"{self.config.chain_name}_pools.json"

    async def find_pools(self, force_refresh: bool = False) -> Dict[str, Any]:
        """Find active pools from factory contracts and save them to the cache."""
        
        # return the cached data if it exists and a refresh was not requested
        if not force_refresh and self.cache_file.exists():
            with open(self.cache_file, "r", encoding="utf-8") as f:
                return json.load(f)

        print("[INFO] Discovering active pools with a Multicall request...")
        tokens = list(self.config.chain["tokens"].values())
        token_addresses = [t["address"] for t in tokens]
        
        calls = []
        call_context = [] # stores the pair and dex type represented by each call

        # create all possible unique token pairs (A-B)
        for i in range(len(token_addresses)):
            for j in range(i + 1, len(token_addresses)):
                token_a = token_addresses[i]
                token_b = token_addresses[j]

                # build queries for each DEX
                for dex_name, dex_data in self.config.chain["dexes"].items():
                    factory = dex_data["factory"]
                    
                    if dex_data["type"] == "univ2":
                        # callData: selector + 2 addresses (tokenA, tokenB)
                        call_data = V2_GET_PAIR_SELECTOR + encode(["address", "address"], [token_a, token_b])
                        calls.append((factory, True, call_data))
                        call_context.append((dex_name, dex_data["type"], token_a, token_b, dex_data.get("fee_bps")))
                        
                    elif dex_data["type"] == "univ3":
                        # uniswap v3 also requires a fee tier parameter
                        for fee in dex_data["fee_tiers"]:
                            call_data = V3_GET_POOL_SELECTOR + encode(["address", "address", "uint24"], [token_a, token_b, fee])
                            calls.append((factory, True, call_data))
                            call_context.append((dex_name, dex_data["type"], token_a, token_b, fee))

        # execute all factory queries with a single RPC call
        results = await self.multicall.aggregate3(calls)
        
        pools = {}
        for ctx, (success, return_data) in zip(call_context, results):
            # if the call succeeded and returned 32 bytes (the address is padded to 32 bytes)
            if success and len(return_data) == 32:
                pool_address = Web3.to_checksum_address(decode(["address"], return_data)[0])
                
                # the zero address means no pool exists for this pair and fee
                if pool_address != "0x0000000000000000000000000000000000000000":
                    dex_name, dex_type, t_a, t_b, fee = ctx
                    
                    if dex_name not in pools:
                        pools[dex_name] = []
                        
                    pool_info = {
                        "pool_address": pool_address,
                        "type": dex_type,
                        "token0": t_a,
                        "token1": t_b,
                        "fee": fee
                    }
                    pools[dex_name].append(pool_info)

        # save the results to the JSON cache file
        with open(self.cache_file, "w", encoding="utf-8") as f:
            json.dump(pools, f, indent=4)
            
        return pools


if __name__ == "__main__":
    # test pool discovery: python -m src.discovery.pool_finder
    async def main():
        cfg = Config("base")
        prov = RPCProvider(cfg)
        mc = Multicall(prov)
        finder = PoolFinder(cfg, mc)
        
        # force_refresh=True fetches data from the blockchain and overwrites the cache
        pools = await finder.find_pools(force_refresh=True)
        
        total_pools = 0
        for dex, dex_pools in pools.items():
            count = len(dex_pools)
            total_pools += count
            print(f" - {dex}: {count} active pools")
            
        print(f"[OK] Found and cached {total_pools} pools in total ({finder.cache_file.name}).")
            
    asyncio.run(main())