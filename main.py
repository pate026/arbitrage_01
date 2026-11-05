import argparse
import asyncio
import time
from src.config_loader import Config
from src.discovery.pool_finder import PoolFinder
from src.engine.payload import PayloadBuilder
from src.engine.route_builder import RouteBuilder
from src.engine.simulator import Simulator
from src.rpc.multicall import Multicall
from src.rpc.provider import RPCProvider
from src.utils.logger import ArbitrageLogger


async def run_scanner(chain: str) -> None:
    # 1. load configs and providers
    config = Config(chain)
    provider = RPCProvider(config)
    multicall = Multicall(provider)
    logger = ArbitrageLogger(config.settings["logging"]["log_dir"])

    print(f"=== Arbitrage_01 Scanner Started ===")
    print(f"chain: {config.chain_name.upper()} (id: {config.chain['chain_id']})")
    print(f"rpc: {config.rpc_http}")

    # 2. discover or load pools
    pool_finder = PoolFinder(config, multicall)
    pools = await pool_finder.find_pools(
        force_refresh=config.settings["cache"]["refresh_pools_on_start"]
    )
    total_pools = sum(len(p) for p in pools.values())
    print(f"loaded {total_pools} pools from cache")

    # 3. build routes
    route_builder = RouteBuilder(config)
    routes = route_builder.build_direct_routes()
    print(f"monitoring {len(routes)} 2-hop routes")

    # 4. setup simulator
    simulator = Simulator(config, multicall, provider)

    last_scanned_block = 0
    print("\n[INFO] listening for new blocks...\n")

    # 5. block scan loop
    while True:
        try:
            current_block = await provider.get_latest_block_number()

            if current_block > last_scanned_block:
                start_time = time.time()
                last_scanned_block = current_block

                gas_price = await provider.get_gas_price()
                opps = await simulator.scan_routes(routes, gas_price_wei=gas_price)
                scan_duration = time.time() - start_time

                print(
                    f"block #{current_block} scanned in {scan_duration:.2f}s | "
                    f"gas: {provider.w3.from_wei(gas_price, 'gwei'):.4f} gwei | "
                    f"opportunities: {len(opps)}"
                )

                for opp in opps:
                    payload = PayloadBuilder.build_execution_payload(opp, current_block)
                    logger.log_opportunity(opp, current_block)

                    print(
                        f"  -> [PROFITABLE] {opp['borrow_symbol']} | "
                        f"borrow: ${opp['borrow_amount_usd']} | "
                        f"net profit: ${opp['net_profit_usd']:.2f} | "
                        f"route: {opp['route']['step_1']['dex']} -> {opp['route']['step_2']['dex']}"
                    )

            # base has 2.0s block time; sleep briefly to avoid aggressive polling
            await asyncio.sleep(1.0)

        except KeyboardInterrupt:
            print("\n[INFO] scanner stopped by user.")
            break
        except Exception as e:
            print(f"[ERROR] scan cycle error: {e}")
            await asyncio.sleep(2.0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Arbitrage_01 Scanner")
    parser.add_argument("--chain", default="base", help="target chain (e.g., base, polygon, arbitrum)")
    args = parser.parse_args()

    try:
        asyncio.run(run_scanner(chain=args.chain))
    except KeyboardInterrupt:
        print("\n[INFO] program exited cleanly.")