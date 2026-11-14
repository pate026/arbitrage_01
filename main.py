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
# -> UniV2 / Sushi / Aero Classic
V2_SWAP_TOPIC = (
    "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"
)

# keccak256("Sync(uint112,uint112)")
V2_SYNC_TOPIC = (
    "0x1c411e9a96e071241c2f21f7726b17ae89e3cab4c78be50e062b03a9fffbbad1"
)

# keccak256("Swap(address,address,int256,int256,uint160,uint128,int24)")
# -> UniV3 / Aero Slipstream
V3_SWAP_TOPIC = (
    "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"
)

DEX_EVENT_TOPICS = [
    V2_SWAP_TOPIC,
    V2_SYNC_TOPIC,
    V3_SWAP_TOPIC,
]


async def run_scanner(
    chain: str,
    mode: str = "polling",
    refresh_pools: bool = False,
) -> None:

    # ------------------------------------------------------------------
    # 1. Load configuration and providers
    # ------------------------------------------------------------------

    config = Config(chain)
    provider = RPCProvider(config)
    multicall = Multicall(provider)
    logger = ArbitrageLogger(config.settings["logging"]["log_dir"])

    def sanitize_err(err: Any) -> str:
        """Hide the raw RPC API key from exception strings."""
        msg = str(err)

        if config.rpc_http and config.rpc_http in msg:
            return msg.replace(
                config.rpc_http,
                config.masked_rpc_http,
            )

        return msg

    print()
    print("=== Arbitrage Scanner ===")
    print(f"Chain:  {config.chain_name.upper()} ({config.chain['chain_id']})")
    print(f"Mode:   {mode.upper()}")
    print(f"RPC:    {config.masked_rpc_http}")

    # ------------------------------------------------------------------
    # 2. Discover/load pools
    # ------------------------------------------------------------------

    pool_finder = PoolFinder(config, multicall)

    force_refresh = (
        refresh_pools
        or config.settings["cache"].get(
            "refresh_pools_on_start",
            False,
        )
    )

    pools = await pool_finder.find_pools(
        force_refresh=force_refresh,
    )

    total_pools = sum(len(p) for p in pools.values())

    # ------------------------------------------------------------------
    # 3. Build routes
    # ------------------------------------------------------------------

    route_builder = RouteBuilder(config)
    routes = route_builder.build_direct_routes()

    # Collect unique checksum addresses of all monitored pools
    monitored_pools = list(
        {
            Web3.to_checksum_address(r["step_1"]["pool"])
            for r in routes
        }
        | {
            Web3.to_checksum_address(r["step_2"]["pool"])
            for r in routes
        }
    )

    print(
        f"Pools:  {total_pools} across {len(pools)} DEX venues"
    )
    print(
        f"Routes: {len(routes)} cross-DEX routes"
    )
    print(
        f"Track:  {len(monitored_pools)} unique liquidity pools"
    )

    # ------------------------------------------------------------------
    # 4. Setup simulator
    # ------------------------------------------------------------------

    simulator = Simulator(
        config,
        multicall,
        provider,
    )

    # Initialize scanner at current chain tip
    last_scanned_block = (
        await provider.get_latest_block_number()
    )

    current_mode = mode

    # Gas price cache
    cached_gas_price = await provider.get_gas_price()
    last_gas_fetch_time = time.time()

    GAS_CACHE_TTL_SECONDS = 10.0

    # Max block window per getLogs query
    MAX_LOG_BLOCK_WINDOW = 5

    # Console heartbeat
    STATUS_INTERVAL_SECONDS = 30.0
    last_status_time = time.time()
    quiet_batches = 0

    print()
    print(
        f"[READY] Listening for new blocks "
        f"({current_mode} mode)"
    )
    print()

    # ------------------------------------------------------------------
    # 5. Block scan loop
    # ------------------------------------------------------------------

    while True:
        try:
            latest_block = (
                await provider.get_latest_block_number()
            )

            if latest_block <= last_scanned_block:
                await asyncio.sleep(0.08)
                continue

            now = time.time()

            # Refresh cached gas price
            if (
                now - last_gas_fetch_time
                > GAS_CACHE_TTL_SECONDS
            ):
                cached_gas_price = (
                    await provider.get_gas_price()
                )
                last_gas_fetch_time = now

            from_block = max(
                last_scanned_block + 1,
                latest_block
                - MAX_LOG_BLOCK_WINDOW
                + 1,
            )

            to_block = latest_block

            start_time = time.time()

            touched_pools: Optional[Set[str]] = None

            # ----------------------------------------------------------
            # Reactive mode
            # ----------------------------------------------------------

            if current_mode == "reactive":
                try:
                    logs = await provider.w3.eth.get_logs(
                        {
                            "fromBlock": from_block,
                            "toBlock": to_block,
                            "address": monitored_pools,
                            "topics": [DEX_EVENT_TOPICS],
                        }
                    )

                    touched_pools = {
                        log["address"].lower()
                        for log in logs
                    }

                    # No monitored pool activity.
                    # Do not spam one line per quiet batch.
                    if len(touched_pools) == 0:
                        quiet_batches += 1

                        now = time.time()

                        if (
                            now - last_status_time
                            >= STATUS_INTERVAL_SECONDS
                        ):
                            gas_gwei = (
                                provider.w3.from_wei(
                                    cached_gas_price,
                                    "gwei",
                                )
                            )

                            print(
                                f"[STATUS] block #{to_block} | "
                                f"running | "
                                f"{quiet_batches} quiet batches | "
                                f"gas {gas_gwei:.4f} gwei"
                            )

                            last_status_time = now
                            quiet_batches = 0

                        last_scanned_block = to_block
                        continue

                except Exception as log_err:
                    err_msg = sanitize_err(log_err)

                    if (
                        "429" in err_msg
                        or "Too Many Requests" in err_msg
                    ):
                        print(
                            "[WARN] RPC throttled eth_getLogs "
                            "(429). Switching to polling mode."
                        )

                        current_mode = "polling"
                        touched_pools = None

                    else:
                        print(
                            f"[WARN] log query failed on "
                            f"blocks #{from_block}-{to_block}: "
                            f"{err_msg}"
                        )

                        last_scanned_block = to_block
                        continue

            # ----------------------------------------------------------
            # Scan routes
            # ----------------------------------------------------------

            opps = await simulator.scan_routes(
                routes,
                gas_price_wei=cached_gas_price,
                touched_pools=touched_pools,
            )

            scan_duration = time.time() - start_time

            if touched_pools is not None:
                pools_count = len(touched_pools)
            else:
                pools_count = len(monitored_pools)

            batch_size = (
                to_block - from_block + 1
            )

            if batch_size == 1:
                block_label = f"#{to_block}"
            else:
                block_label = (
                    f"#{from_block}-{to_block} "
                    f"(+{batch_size})"
                )

            gas_gwei = provider.w3.from_wei(
                cached_gas_price,
                "gwei",
            )

            # Important activity gets a normal full line.
            # No carriage-return / end="" tricks.
            print(
                f"[ACTIVE] block {block_label} | "
                f"pools {pools_count} | "
                f"{scan_duration:.2f}s | "
                f"gas {gas_gwei:.4f} gwei | "
                f"opportunities {len(opps)}"
            )

            # Reset heartbeat counter because we just printed activity.
            quiet_batches = 0
            last_status_time = time.time()

            # ----------------------------------------------------------
            # Opportunities
            # ----------------------------------------------------------

            if opps:
                for opp in opps:
                    payload = (
                        PayloadBuilder
                        .build_execution_payload(
                            opp,
                            to_block,
                        )
                    )

                    logger.log_opportunity(
                        opp,
                        to_block,
                    )

                    print(
                        f"[PROFITABLE] "
                        f"{opp['borrow_symbol']} | "
                        f"borrow "
                        f"${opp['borrow_amount_usd']:.2f} | "
                        f"net "
                        f"${opp['net_profit_usd']:.2f} | "
                        f"{opp['route']['step_1']['dex']} "
                        f"-> "
                        f"{opp['route']['step_2']['dex']}"
                    )

            last_scanned_block = to_block

        except KeyboardInterrupt:
            print()
            print("[INFO] Scanner stopped by user.")
            break

        except Exception as e:
            clean_err = sanitize_err(e)

            print(
                f"[ERROR] Scan cycle error: "
                f"{clean_err}"
            )

            await asyncio.sleep(1.0)


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Arbitrage Scanner"
    )

    parser.add_argument(
        "--chain",
        default="base",
        help="target chain (e.g. base, polygon, arbitrum)",
    )

    parser.add_argument(
        "--mode",
        default="polling",
        choices=[
            "polling",
            "reactive",
        ],
        help=(
            "scanning strategy: "
            "'polling' or 'reactive'"
        ),
    )

    parser.add_argument(
        "--refresh-pools",
        action="store_true",
        help=(
            "force query on-chain factories "
            "to refresh local pools cache"
        ),
    )

    args = parser.parse_args()

    try:
        asyncio.run(
            run_scanner(
                chain=args.chain,
                mode=args.mode,
                refresh_pools=args.refresh_pools,
            )
        )

    except KeyboardInterrupt:
        print()
        print("[INFO] Program exited cleanly.")
