import asyncio
from typing import Any, Dict, List, Optional, Set
from web3 import Web3
from src.config_loader import Config
from src.engine.route_builder import RouteBuilder
from src.math.fee_engine import FeeEngine
from src.math.optimizer import ArbitrageOptimizer
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
        self.test_amounts_usd = self.settings.get("test_amounts_usd", [500, 1000, 2500])
        self.gas_limit = self.settings["gas_limit_estimate"]
        # minimum active liquidity threshold for Uniswap V3 pools.
        self.min_univ3_liquidity = self.settings.get("min_univ3_liquidity", 10**16)

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
            # filter out pools without sufficient active liquidity
            if sqrt_p == 0 or liq < self.min_univ3_liquidity:
                return 0
            zero_for_one = token_in.lower() < token_out.lower()
            return UniV3Math.get_amount_out(amount_in, sqrt_p, liq, fee, zero_for_one)

        return 0

    def compute_net_profit(
        self,
        amount_in: int,
        route: Dict[str, Any],
        state_1: Dict[str, Any],
        state_2: Dict[str, Any],
        gas_cost_in_token: int,
        aave_fee_bps: int,
    ) -> int:
        """
        Calculates raw token net profit (amount_out - amount_in - aave_fee - gas_cost).
        Used as the objective function for optimization.
        """
        s1 = route["step_1"]
        s2 = route["step_2"]

        amount_mid = self.simulate_swap(
            s1["type"], state_1, s1["fee"], s1["token_in"], s1["token_out"], amount_in
        )
        if amount_mid <= 0:
            return -amount_in

        amount_out = self.simulate_swap(
            s2["type"], state_2, s2["fee"], s2["token_in"], s2["token_out"], amount_mid
        )
        if amount_out <= amount_in:
            return -amount_in

        gross_profit = amount_out - amount_in
        aave_fee = FeeEngine.get_aave_flashloan_fee(amount_in, aave_fee_bps)
        return gross_profit - aave_fee - gas_cost_in_token

    async def scan_routes(
        self,
        routes: List[Dict[str, Any]],
        gas_price_wei: int,
        eth_price_usd: float = 3000.0,
        touched_pools: Optional[Set[str]] = None,
    ) -> List[Dict[str, Any]]:
        # reactive filtering: only evaluate routes where at least one pool was touched in this block
        if touched_pools is not None:
            routes = [
                r for r in routes
                if r["step_1"]["pool"].lower() in touched_pools
                or r["step_2"]["pool"].lower() in touched_pools
            ]
            if not routes:
                return []

        # extracts unique pools needed across all routes
        unique_pools = {}
        for r in routes:
            s1 = r["step_1"]
            s2 = r["step_2"]
            unique_pools[s1["pool"].lower()] = (s1["pool"], s1["type"])
            unique_pools[s2["pool"].lower()] = (s2["pool"], s2["type"])

        pool_states = await self.fetch_pool_states(list(unique_pools.values()))
        profitable_opportunities: List[Dict[str, Any]] = []
        aave_fee_bps = self.config.chain.get("aave_flashloan_fee_bps", 5)

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

            # gas cost in borrow token units
            gas_cost_in_token = FeeEngine.estimate_gas_cost_in_target_token(
                gas_used=self.gas_limit,
                gas_price_wei=gas_price_wei,
                native_token_price_in_target=eth_price_usd if symbol in ["USDC", "DAI"] else 1.0,
                target_decimals=decimals,
            )

            # define objective function for optimizer
            def profit_func(amount: int) -> int:
                return self.compute_net_profit(
                    amount_in=amount,
                    route=route,
                    state_1=state_1,
                    state_2=state_2,
                    gas_cost_in_token=gas_cost_in_token,
                    aave_fee_bps=aave_fee_bps,
                )

            # determine search bounds: $100 to $10,000 USD
            is_stable = symbol in ["USDC", "DAI"]
            multiplier = 1.0 if is_stable else eth_price_usd

            min_bound_usd = 100.0
            max_bound_usd = 10000.0

            if is_stable:
                min_amount = int(min_bound_usd * (10 ** decimals))
                max_amount = int(max_bound_usd * (10 ** decimals))
                probe_amount = int(500.0 * (10 ** decimals))
            else:
                min_amount = int((min_bound_usd / eth_price_usd) * (10 ** decimals))
                max_amount = int((max_bound_usd / eth_price_usd) * (10 ** decimals))
                probe_amount = int((500.0 / eth_price_usd) * (10 ** decimals))

            # fast probe: if 500 USD yields no gross profit at all, skip optimization
            amount_mid = self.simulate_swap(
                s1["type"], state_1, s1["fee"], s1["token_in"], s1["token_out"], probe_amount
            )
            if amount_mid <= 0:
                continue
            amount_out_probe = self.simulate_swap(
                s2["type"], state_2, s2["fee"], s2["token_in"], s2["token_out"], amount_mid
            )
            if amount_out_probe <= probe_amount:
                continue

            # run Golden-Section optimizer to find optimal trade size
            opt_borrow_amount, opt_net_profit = ArbitrageOptimizer.optimize_borrow_amount(
                profit_func=profit_func,
                min_amount=min_amount,
                max_amount=max_amount,
                tolerance=max(1, int(10 * (10 ** decimals) if is_stable else 0.005 * (10 ** decimals))),
                max_iterations=20,
            )

            if opt_net_profit <= 0:
                continue

            net_profit_usd = (opt_net_profit / (10 ** decimals)) * multiplier

            if net_profit_usd >= self.min_profit_usd:
                # recalculate component figures for reporting
                mid_opt = self.simulate_swap(
                    s1["type"], state_1, s1["fee"], s1["token_in"], s1["token_out"], opt_borrow_amount
                )
                final_out = self.simulate_swap(
                    s2["type"], state_2, s2["fee"], s2["token_in"], s2["token_out"], mid_opt
                )
                gross_profit = final_out - opt_borrow_amount
                aave_fee = FeeEngine.get_aave_flashloan_fee(opt_borrow_amount, aave_fee_bps)

                borrow_usd = (opt_borrow_amount / (10 ** decimals)) * multiplier
                profitable_opportunities.append({
                    "route": route,
                    "borrow_symbol": symbol,
                    "borrow_amount": opt_borrow_amount,
                    "borrow_amount_usd": round(borrow_usd, 2),
                    "expected_amount_out": final_out,
                    "gross_profit_usd": (gross_profit / (10 ** decimals)) * multiplier,
                    "aave_fee_usd": (aave_fee / (10 ** decimals)) * multiplier,
                    "gas_cost_usd": (gas_cost_in_token / (10 ** decimals)) * multiplier,
                    "net_profit_usd": net_profit_usd,
                    "s1_liq": state_1.get("liquidity", 0),
                    "s2_liq": state_2.get("liquidity", 0),
                    "s1_sqrt_price": state_1.get("sqrt_price_x96", 0),
                    "s2_sqrt_price": state_2.get("sqrt_price_x96", 0),
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
            print(f" - step 1: {op['route']['step_1']['dex']} (fee {op['route']['step_1']['fee']}) | pool: {op['route']['step_1']['pool']} | liq: {op.get('s1_liq', 0)}")
            print(f" - step 2: {op['route']['step_2']['dex']} (fee {op['route']['step_2']['fee']}) | pool: {op['route']['step_2']['pool']} | liq: {op.get('s2_liq', 0)}")
            print(f" - net profit: ${op['net_profit_usd']:.2f} usd")

    asyncio.run(main())