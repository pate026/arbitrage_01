import asyncio
from typing import Any, Dict, List
from web3 import Web3

from src.config_loader import Config
from src.engine.route_builder import RouteBuilder
from src.math.fee_engine import FeeEngine
from src.math.univ2_math import UniV2Math, V2_GET_RESERVES_SELECTOR
from src.math.univ3_math import (
    UniV3Math,
    v3_liquidity_selector,
    v3_slot0_selector,
)
from src.rpc.multicall import Multicall
from src.rpc.provider import RPCProvider


class Simulator:
    def __init__(self, config: Config, multicall: Multicall, provider: RPCProvider):
        self.config = config
        self.multicall = multicall
        self.provider = provider
        self.settings = self.config.settings["scanner"]
        self.min_profit_usd = self.settings["min_profit_usd"]
        self.test_amounts_usd = self.settings["test_amounts_usd"]
        self.gas_limit = self.settings["gas_limit_estimate"]

        # resolve token decimals and symbols by address
        self.token_meta: Dict[str, Dict[str, Any]] = {}
        for symbol, data in self.config.chain["tokens"].items():
            self.token_meta[data["address"].lower()] = {
                "symbol": symbol,
                "decimals": data["decimals"],
            }

    async def fetch_pool_states(self, pool_addresses_and_types: List[tuple[str, str]]) -> Dict[str, Dict[str, Any]]:
        # batch-fetches on-chain state for all unique pools using multicall3
        calls = []
        call_map = []

        for address, pool_type in pool_addresses_and_types:
            addr_cs = Web3.to_checksum_address(address)
            if pool_type == "univ2":
                calls.append((addr_cs, True, V2_GET_RESERVES_SELECTOR))
                call_map.append((address.lower(), "v2_reserves"))
            elif pool_type == "univ3":
                calls.append((addr_cs, True, v3_slot0_selector))
                call_map.append((address.lower(), "v3_slot0"))
                calls.append((addr_cs, True, v3_liquidity_selector))
                call_map.append((address.lower(), "v3_liquidity"))

        results = await self.multicall.aggregate3(calls)

        pool_states: Dict[str, Dict[str, Any]] = {}
        for (pool_addr, data_type), (success, raw_bytes) in zip(call_map, results):
            if pool_addr not in pool_states:
                pool_states[pool_addr] = {}

            if not success or not raw_bytes:
                continue

            if data_type == "v2_reserves":
                r0, r1 = UniV2Math.decode_reserves(raw_bytes)
                pool_states[pool_addr]["reserve0"] = r0
                pool_states[pool_addr]["reserve1"] = r1
            elif data_type == "v3_slot0":
                sqrt_p, tick = UniV3Math.decode_slot0(raw_bytes)
                pool_states[pool_addr]["sqrt_price_x96"] = sqrt_p
                pool_states[pool_addr]["tick"] = tick
            elif data_type == "v3_liquidity":
                liq = UniV3Math.decode_liquidity(raw_bytes)
                pool_states[pool_addr]["liquidity"] = liq

        return pool_states

    def simulate_swap(
        self,
        pool_type: str,
        pool_state: Dict[str, Any],
        fee: int,
        token_in: str,
        token_out: str,
        amount_in: int,
    ) -> int:
        # calculates exact output amount for a single swap step
        if amount_in <= 0:
            return 0

        if pool_type == "univ2":
            r0 = pool_state.get("reserve0", 0)
            r1 = pool_state.get("reserve1", 0)
            if r0 == 0 or r1 == 0:
                return 0
            is_token0_in = token_in.lower() < token_out.lower()
            res_in = r0 if is_token0_in else r1
            res_out = r1 if is_token0_in else r0
            return UniV2Math.get_amount_out(amount_in, res_in, res_out, fee)

        elif pool_type == "univ3":
            sqrt_p = pool_state.get("sqrt_price_x96", 0)
            liq = pool_state.get("liquidity", 0)
            if sqrt_p == 0 or liq == 0:
                return 0
            zero_for_one = token_in.lower() < token_out.lower()
            return UniV3Math.get_amount_out(amount_in, sqrt_p, liq, fee, zero_for_one)

        return 0

    async def scan_routes(
        self,
        routes: List[Dict[str, Any]],
        gas_price_wei: int,
        eth_price_usd: float = 3000.0,
    ) -> List[Dict[str, Any]]:
        # extracts unique pools needed across all routes
        unique_pools = {}
        for r in routes:
            s1 = r["step_1"]
            s2 = r["step_2"]
            unique_pools[s1["pool"].lower()] = (s1["pool"], s1["type"])
            unique_pools[s2["pool"].lower()] = (s2["pool"], s2["type"])

        pool_states = await self.fetch_pool_states(list(unique_pools.values()))

        profitable_opportunities: List[Dict[str, Any]] = []

        for route in routes:
            borrow_token = route["borrow_token"].lower()
            meta = self.token_meta.get(borrow_token)
            if not meta:
                continue

            decimals = meta["decimals"]
            symbol = meta["symbol"]

            s1 = route["step_1"]
            s2 = route["step_2"]
            state_1 = pool_states.get(s1["pool"].lower(), {})
            state_2 = pool_states.get(s2["pool"].lower(), {})

            for amount_usd in self.test_amounts_usd:
                if symbol in ["USDC", "DAI"]:
                    amount_in = int(amount_usd * (10 ** decimals))
                elif symbol in ["WETH", "cbETH"]:
                    amount_in = int((amount_usd / eth_price_usd) * (10 ** decimals))
                else:
                    continue

                # step 1: borrow_token -> target_token
                amount_mid = self.simulate_swap(
                    s1["type"], state_1, s1["fee"], s1["token_in"], s1["token_out"], amount_in
                )
                if amount_mid <= 0:
                    continue

                # step 2: target_token -> borrow_token
                amount_out = self.simulate_swap(
                    s2["type"], state_2, s2["fee"], s2["token_in"], s2["token_out"], amount_mid
                )
                if amount_out <= amount_in:
                    continue

                # gross profit
                gross_profit = amount_out - amount_in

                # aave flashloan fee
                aave_fee = FeeEngine.get_aave_flashloan_fee(
                    amount_in, self.config.chain.get("aave_flashloan_fee_bps", 5)
                )

                # gas cost
                gas_cost_in_token = FeeEngine.estimate_gas_cost_in_target_token(
                    gas_used=self.gas_limit,
                    gas_price_wei=gas_price_wei,
                    native_token_price_in_target=eth_price_usd if symbol in ["USDC", "DAI"] else 1.0,
                    target_decimals=decimals,
                )

                net_profit = gross_profit - aave_fee - gas_cost_in_token

                if symbol in ["USDC", "DAI"]:
                    net_profit_usd = net_profit / (10 ** decimals)
                else:
                    net_profit_usd = (net_profit / (10 ** decimals)) * eth_price_usd

                if net_profit_usd >= self.min_profit_usd:
                    profitable_opportunities.append({
                        "route": route,
                        "borrow_symbol": symbol,
                        "borrow_amount": amount_in,
                        "borrow_amount_usd": amount_usd,
                        "expected_amount_out": amount_out,
                        "gross_profit_usd": (gross_profit / (10 ** decimals)) * (1.0 if symbol in ["USDC", "DAI"] else eth_price_usd),
                        "aave_fee_usd": (aave_fee / (10 ** decimals)) * (1.0 if symbol in ["USDC", "DAI"] else eth_price_usd),
                        "gas_cost_usd": (gas_cost_in_token / (10 ** decimals)) * (1.0 if symbol in ["USDC", "DAI"] else eth_price_usd),
                        "net_profit_usd": net_profit_usd,
                    })

        return profitable_opportunities


if __name__ == "__main__":
    # test scanner simulator directly: python -m src.engine.simulator
    async def main():
        cfg = Config("base")
        prov = RPCProvider(cfg)
        mc = Multicall(prov)
        builder = RouteBuilder(cfg)
        sim = Simulator(cfg, mc, prov)

        routes = builder.build_direct_routes()
        print(f"[INFO] scanning {len(routes)} routes against current live state...")

        gas_price = await prov.get_gas_price()
        opps = await sim.scan_routes(routes, gas_price_wei=gas_price)

        print(f"--- scan results ---")
        print(f"profitable opportunities found: {len(opps)}")

        for op in opps[:3]:
            print(f"\n[OPPORTUNITY] {op['borrow_symbol']} route:")
            print(f" - borrowed: {op['borrow_amount_usd']} usd")
            print(f" - step 1: {op['route']['step_1']['dex']} (fee {op['route']['step_1']['fee']})")
            print(f" - step 2: {op['route']['step_2']['dex']} (fee {op['route']['step_2']['fee']})")
            print(f" - net profit: ${op['net_profit_usd']:.2f} usd")

    asyncio.run(main())