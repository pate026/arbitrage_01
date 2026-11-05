import asyncio
from typing import Optional
from web3 import AsyncWeb3
from web3.providers import AsyncHTTPProvider
from src.config_loader import Config


class RPCProvider:
    def __init__(self, config: Config):
        self.config = config
        self.w3: AsyncWeb3 = AsyncWeb3(AsyncHTTPProvider(config.rpc_http))

    async def check_connection(self) -> bool:
        """Check the network connection and return its status."""
        try:
            connected = await self.w3.is_connected()
            if connected:
                chain_id = await self.w3.eth.chain_id
                latest_block = await self.w3.eth.block_number
                return chain_id == self.config.chain["chain_id"] and latest_block > 0
            return False
        except Exception:
            return False

    async def get_latest_block_number(self) -> int:
        """Get the latest block number."""
        return await self.w3.eth.block_number

    async def get_gas_price(self) -> int:
        """Get the network's current base gas price in wei."""
        return await self.w3.eth.gas_price


if __name__ == "__main__":
    # test the RPC connection: python src/rpc/provider.py
    async def main():
        cfg = Config("base")
        provider = RPCProvider(cfg)
        ok = await provider.check_connection()
        if ok:
            block = await provider.get_latest_block_number()
            gas_wei = await provider.get_gas_price()
            gas_gwei = provider.w3.from_wei(gas_wei, "gwei")
            print(f"[OK] RPC connection to {cfg.chain_name.upper()} is working")
            print(f" - Latest block: {block}")
            print(f" - Gas price: {gas_gwei:.4f} Gwei")
        else:
            print("[ERROR] Could not establish an RPC connection.")

    asyncio.run(main())