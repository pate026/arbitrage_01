import argparse
import asyncio
import time
from typing import Any, Optional, Set
from src.config_loader import Config
from src.discovery.pool_finder import PoolFinder
from src.engine.payload import PayloadBuilder
from src.engine.route_builder import RouteBuilder
from src.engine.simulator import Simulator
from src.rpc.multicall import Multicall
from src.rpc.provider import RPCProvider
from src.utils.logger import ArbitrageLogger
from web3 import Web3

# DEX swap/sync topic0 signatures
# keccak256("Swap(address,uint256,uint256,uint256,uint256,address)")
V2_SWAP_TOPIC = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"
# keccak256("Sync(uint112,uint112)")
V2_SYNC_TOPIC = "0x1c411e9a96e071241c2f21f7726b17ae89e3cab4c78be50e062b03a9fffbbad1"
# keccak256("Swap(address,address,int256,int256,uint160,uint128,int24)")
V3_SWAP_TOPIC = "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"
DEX_EVENT_TOPICS = [V2_SWAP_TOPIC, V2_SYNC_TOPIC, V3_SWAP_TOPIC]


async def run_scanner(chain: str, mode: str = "polling") -> None:
    # 1. load configs and providers
    config = Config(chain)
    provider = RPCProvider(config)
    multicall = Multicall(provider)
    logger = ArbitrageLogger(config.settings["logging"]["log_dir"])

    def sanitize_err(err: Any) -> str:
        """Helper to hide the raw RPC API key from exception strings."""
        msg = str(err)
        if config.rpc_http and config.rpc_http in msg:
            return msg.replace(config.rpc_http, config.masked_rpc_http)
        return msg

    print(f"=== Arbitrage_01 Scanner Started ===")
    print(f"chain: {config.chain_name.upper()} (id: {config.chain['chain_id']})")
    print(f"rpc: {config.masked_rpc_http}")
    print(f"mode: {mode.upper()}")

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

    # Collect unique checksum addresses of all monitored pools
    monitored_pools = list({
        Web3.to_checksum_address(r["step_1"]["pool"]) for r in routes
    } | {
        Web3.to_checksum_address(r["step_2"]["pool"]) for r in routes
    })
    print(f"actively tracking {len(monitored_pools)} unique pools")

    # 4. setup simulator
    simulator = Simulator(config, multicall, provider)

    # Initialize last_scanned_block to current chain tip
    last_scanned_block = await provider.get_latest_block_number()
    current_mode = mode

    # Gas price cache to avoid unnecessary sequential RPC round-trips every block
    cached_gas_price = await provider.get_gas_price()
    last_gas_fetch_time = time.time()
    GAS_CACHE_TTL_SECONDS = 10.0

    # Max block window per getLogs query to stay safely within Alchemy limits
    MAX_LOG_BLOCK_WINDOW = 5

    print(f"\n[INFO] listening for new blocks ({current_mode}-mode)...\n")

    # 5. block scan loop
    while True:
        try:
            latest_block = await provider.get_latest_block_number()

            # Wait briefly if no new block has arrived yet
            if latest_block <= last_scanned_block:
                await asyncio.sleep(0.08)
                continue

            # Refresh cached gas price in background interval
            now = time.time()
            if now - last_gas_fetch_time > GAS_CACHE_TTL_SECONDS:
                cached_gas_price = await provider.get_gas_price()
                last_gas_fetch_time = now

            # Tip-lock: never look too far back; always stay glued to the tip
            from_block = max(last_scanned_block + 1, latest_block - MAX_LOG_BLOCK_WINDOW + 1)
            to_block = latest_block

            start_time = time.time()
            touched_pools: Optional[Set[str]] = None

            # Reactive event filtering
            if current_mode == "reactive":
                try:
                    logs = await provider.w3.eth.get_logs({
                        "fromBlock": from_block,
                        "toBlock": to_block,
                        "address": monitored_pools,
                        "topics": [DEX_EVENT_TOPICS],
                    })
                    touched_pools = {log["address"].lower() for log in logs}

                    # Fast path: skip simulation if none of our monitored pools had activity
                    if len(touched_pools) == 0:
                        check_duration = time.time() - start_time
                        batch_size = to_block - from_block + 1
                        block_label = f"#{to_block}" if batch_size == 1 else f"#{from_block}-{to_block} (+{batch_size})"
                        print(
                            f"block {block_label} checked in {check_duration:.3f}s | "
                            f"no pool activity | 0 opportunities"
                        )
                        last_scanned_block = to_block
                        continue

                except Exception as log_err:
                    err_msg = sanitize_err(log_err)
                    if "429" in err_msg or "Too Many Requests" in err_msg:
                        print("[WARN] RPC throttles eth_getLogs (429). Downgrading to multicall polling mode.")
                        current_mode = "polling"
                        touched_pools = None
                    else:
                        print(f"[WARN] log query failed on blocks #{from_block}-{to_block}: {err_msg}")
                        # On log error, avoid heavy full-scan to prevent death-spiral lag; advance block tip
                        last_scanned_block = to_block
                        continue

            opps = await simulator.scan_routes(
                routes,
                gas_price_wei=cached_gas_price,
                touched_pools=touched_pools,
            )
            scan_duration = time.time() - start_time

            pools_count = len(touched_pools) if touched_pools is not None else len(monitored_pools)
            batch_size = to_block - from_block + 1
            block_label = f"#{to_block}" if batch_size == 1 else f"#{from_block}-{to_block} (+{batch_size})"
            print(
                f"block {block_label} scanned in {scan_duration:.2f}s | "
                f"active pools: {pools_count} | "
                f"gas: {provider.w3.from_wei(cached_gas_price, 'gwei'):.4f} gwei | "
                f"opportunities: {len(opps)}"
            )

            for opp in opps:
                payload = PayloadBuilder.build_execution_payload(opp, to_block)
                logger.log_opportunity(opp, to_block)

                print(
                    f"  -> [PROFITABLE] {opp['borrow_symbol']} | "
                    f"borrow: ${opp['borrow_amount_usd']} | "
                    f"net profit: ${opp['net_profit_usd']:.2f} | "
                    f"route: {opp['route']['step_1']['dex']} -> {opp['route']['step_2']['dex']}"
                )

            last_scanned_block = to_block

        except KeyboardInterrupt:
            print("\n[INFO] scanner stopped by user.")
            break
        except Exception as e:
            clean_err = sanitize_err(e)
            print(f"[ERROR] scan cycle error: {clean_err}")
            await asyncio.sleep(1.0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Arbitrage_01 Scanner")
    parser.add_argument("--chain", default="base", help="target chain (e.g., base, polygon, arbitrum)")
    parser.add_argument(
        "--mode",
        default="polling",
        choices=["polling", "reactive"],
        help="scanning strategy: 'polling' (safe for public RPCs) or 'reactive' (requires paid/dedicated RPC)",
    )
    args = parser.parse_args()

    try:
        asyncio.run(run_scanner(chain=args.chain, mode=args.mode))
    except KeyboardInterrupt:
        print("\n[INFO] program exited cleanly.")