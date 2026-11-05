import json
from pathlib import Path
from typing import Any, Dict, List, Tuple
from src.config_loader import Config
from src.discovery.pool_finder import PoolFinder
from src.rpc.multicall import Multicall
from src.rpc.provider import RPCProvider


class RouteBuilder:
    def __init__(self, config: Config):
        self.config = config
        self.cache_dir = Path(self.config.settings["cache"]["cache_dir"])
        self.cache_file = self.cache_dir / f"{self.config.chain_name}_pools.json"

    def load_pools(self) -> Dict[str, List[Dict[str, Any]]]:
        # load pools from cached json file
        if not self.cache_file.exists():
            raise FileNotFoundError(
                f"pool cache file not found at {self.cache_file}. "
                "run pool_finder.py first."
            )
        with open(self.cache_file, "r", encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def _get_pair_key(token0: str, token1: str) -> Tuple[str, str]:
        # returns an order-independent tuple of token addresses
        t0 = token0.lower()
        t1 = token1.lower()
        return (t0, t1) if t0 < t1 else (t1, t0)

    def build_direct_routes(self) -> List[Dict[str, Any]]:
        # builds 2-hop cyclic arbitrage routes (token_a -> token_b -> token_a)
        pools_by_dex = self.load_pools()

        # 1. flatten and group pools by token pair
        pair_to_pools: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}

        for dex_name, pool_list in pools_by_dex.items():
            for pool in pool_list:
                pair_key = self._get_pair_key(pool["token0"], pool["token1"])
                if pair_key not in pair_to_pools:
                    pair_to_pools[pair_key] = []

                # attach dex name to pool object for routing
                pool_entry = dict(pool)
                pool_entry["dex"] = dex_name
                pair_to_pools[pair_key].append(pool_entry)

        routes: List[Dict[str, Any]] = []

        # 2. for pairs with at least 2 pools, generate bidirectional routes
        for (token_a, token_b), pools in pair_to_pools.items():
            if len(pools) < 2:
                continue

            for i in range(len(pools)):
                for j in range(i + 1, len(pools)):
                    pool_1 = pools[i]
                    pool_2 = pools[j]

                    # direction 1: borrow token_a -> swap to token_b in pool_1 -> swap back to token_a in pool_2
                    routes.append({
                        "route_type": "direct_2hop",
                        "borrow_token": token_a,
                        "target_token": token_b,
                        "step_1": {
                            "pool": pool_1["pool_address"],
                            "dex": pool_1["dex"],
                            "type": pool_1["type"],
                            "fee": pool_1["fee"],
                            "token_in": token_a,
                            "token_out": token_b,
                        },
                        "step_2": {
                            "pool": pool_2["pool_address"],
                            "dex": pool_2["dex"],
                            "type": pool_2["type"],
                            "fee": pool_2["fee"],
                            "token_in": token_b,
                            "token_out": token_a,
                        },
                    })

                    # direction 2: borrow token_a -> swap to token_b in pool_2 -> swap back to token_a in pool_1
                    routes.append({
                        "route_type": "direct_2hop",
                        "borrow_token": token_a,
                        "target_token": token_b,
                        "step_1": {
                            "pool": pool_2["pool_address"],
                            "dex": pool_2["dex"],
                            "type": pool_2["type"],
                            "fee": pool_2["fee"],
                            "token_in": token_a,
                            "token_out": token_b,
                        },
                        "step_2": {
                            "pool": pool_1["pool_address"],
                            "dex": pool_1["dex"],
                            "type": pool_1["type"],
                            "fee": pool_1["fee"],
                            "token_in": token_b,
                            "token_out": token_a,
                        },
                    })

                    # direction 3 & 4: borrow token_b instead of token_a
                    routes.append({
                        "route_type": "direct_2hop",
                        "borrow_token": token_b,
                        "target_token": token_a,
                        "step_1": {
                            "pool": pool_1["pool_address"],
                            "dex": pool_1["dex"],
                            "type": pool_1["type"],
                            "fee": pool_1["fee"],
                            "token_in": token_b,
                            "token_out": token_a,
                        },
                        "step_2": {
                            "pool": pool_2["pool_address"],
                            "dex": pool_2["dex"],
                            "type": pool_2["type"],
                            "fee": pool_2["fee"],
                            "token_in": token_a,
                            "token_out": token_b,
                        },
                    })

                    routes.append({
                        "route_type": "direct_2hop",
                        "borrow_token": token_b,
                        "target_token": token_a,
                        "step_1": {
                            "pool": pool_2["pool_address"],
                            "dex": pool_2["dex"],
                            "type": pool_2["type"],
                            "fee": pool_2["fee"],
                            "token_in": token_b,
                            "token_out": token_a,
                        },
                        "step_2": {
                            "pool": pool_1["pool_address"],
                            "dex": pool_1["dex"],
                            "type": pool_1["type"],
                            "fee": pool_1["fee"],
                            "token_in": token_a,
                            "token_out": token_b,
                        },
                    })

        return routes


if __name__ == "__main__":
    # test route construction: python -m src.engine.route_builder
    cfg = Config("base")
    builder = RouteBuilder(cfg)
    all_routes = builder.build_direct_routes()

    print("--- Route Builder Test ---")
    print(f"total direct routes constructed: {len(all_routes)}")

    if all_routes:
        sample = all_routes[0]
        print("\nexample route:")
        print(f" - borrow token: {sample['borrow_token']}")
        print(f" - target token: {sample['target_token']}")
        print(f" - step 1: {sample['step_1']['dex']} (pool: {sample['step_1']['pool'][:10]}..., fee: {sample['step_1']['fee']})")
        print(f" - step 2: {sample['step_2']['dex']} (pool: {sample['step_2']['pool'][:10]}..., fee: {sample['step_2']['fee']})")