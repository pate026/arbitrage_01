import os
import asyncio
from typing import Any, Dict
from web3 import AsyncWeb3
from eth_account import Account
from src.config_loader import Config
from src.rpc.provider import RPCProvider

# minimal abi containing only the function we need to call
EXECUTOR_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"internalType": "address", "name": "borrowAsset", "type": "address"},
                    {"internalType": "uint256", "name": "borrowAmount", "type": "uint256"},
                    {
                        "components": [
                            {"internalType": "address", "name": "router", "type": "address"},
                            {"internalType": "uint8", "name": "dexType", "type": "uint8"},
                            {"internalType": "address", "name": "tokenIn", "type": "address"},
                            {"internalType": "address", "name": "tokenOut", "type": "address"},
                            {"internalType": "uint24", "name": "fee", "type": "uint24"}
                        ],
                        "internalType": "struct ArbitrageExecutor.SwapStep",
                        "name": "step1",
                        "type": "tuple"
                    },
                    {
                        "components": [
                            {"internalType": "address", "name": "router", "type": "address"},
                            {"internalType": "uint8", "name": "dexType", "type": "uint8"},
                            {"internalType": "address", "name": "tokenIn", "type": "address"},
                            {"internalType": "address", "name": "tokenOut", "type": "address"},
                            {"internalType": "uint24", "name": "fee", "type": "uint24"}
                        ],
                        "internalType": "struct ArbitrageExecutor.SwapStep",
                        "name": "step2",
                        "type": "tuple"
                    }
                ],
                "internalType": "struct ArbitrageExecutor.FlashloanPayload",
                "name": "payload",
                "type": "tuple"
            }
        ],
        "name": "executeArbitrage",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    }
]

class TransactionSender:
    def __init__(self, config: Config, provider: RPCProvider):
        self.config = config
        self.w3 = provider.w3
        
        # load private key securely from environment
        private_key = os.getenv("PRIVATE_KEY")
        if not private_key:
            print("[warn] private_key not found in .env. transactions cannot be sent.")
            self.account = None
        else:
            self.account = Account.from_key(private_key)
            
        # load executor contract address based on the current chain
        prefix = self.config.chain.get("rpc_env_prefix", self.config.chain_name.upper())
        contract_address = os.getenv(f"{prefix}_EXECUTOR_ADDRESS")
        
        if not contract_address:
            print(f"[warn] {prefix}_executor_address not found in .env. dry-run mode only.")
            self.contract = None
        else:
            checksummed_address = AsyncWeb3.to_checksum_address(contract_address)
            self.contract = self.w3.eth.contract(address=checksummed_address, abi=EXECUTOR_ABI)

    def _map_dex_type(self, dex_type_str: str) -> int:
        """converts python string types to solidity uint8 enums."""
        if dex_type_str in ["univ2", "aerodrome_v2", "quickswap_v2"]:
            return 2
        elif dex_type_str in ["univ3", "aerodrome_cl", "pancakeswap_v3"]:
            return 3
        return 0

    async def send_arbitrage_tx(self, payload_dict: Dict[str, Any], gas_price_wei: int) -> str:
        """
        builds, signs, and sends the arbitrage transaction to the mempool.
        """
        if not self.account or not self.contract:
            return "skipped (missing keys/address)"

        # 1. format payload for solidity
        # payloadbuilder outputs route_hops. we map these to swapstep structs.
        hop1 = payload_dict["route_hops"][0]
        hop2 = payload_dict["route_hops"][1]
        
        # we need the router address from the config for these dexes
        dex1_config = self.config.chain["dexes"][hop1["dex"]]
        dex2_config = self.config.chain["dexes"][hop2["dex"]]
        
        router1 = AsyncWeb3.to_checksum_address(dex1_config["router"])
        router2 = AsyncWeb3.to_checksum_address(dex2_config["router"])

        solidity_payload = (
            AsyncWeb3.to_checksum_address(payload_dict["borrow_asset"]), # borrowAsset
            int(payload_dict["borrow_amount"]),                          # borrowAmount
            (                                                            # step1
                router1,
                self._map_dex_type(hop1["type"]),
                AsyncWeb3.to_checksum_address(hop1["token_in"]),
                AsyncWeb3.to_checksum_address(hop1["token_out"]),
                int(hop1["fee"])
            ),
            (                                                            # step2
                router2,
                self._map_dex_type(hop2["type"]),
                AsyncWeb3.to_checksum_address(hop2["token_in"]),
                AsyncWeb3.to_checksum_address(hop2["token_out"]),
                int(hop2["fee"])
            )
        )

        try:
            # 2. build transaction
            nonce = await self.w3.eth.get_transaction_count(self.account.address)
            
            tx = await self.contract.functions.executeArbitrage(solidity_payload).build_transaction({
                'from': self.account.address,
                'nonce': nonce,
                'gas': self.config.settings["scanner"]["gas_limit_estimate"],
                'gasPrice': gas_price_wei,
            })

            # 3. sign transaction
            signed_tx = self.w3.eth.account.sign_transaction(tx, private_key=self.account.key)

            # 4. send transaction
            print(f"[tx] broadcasting transaction for {payload_dict['borrow_symbol']} arbitrage...")
            tx_hash = await self.w3.eth.send_raw_transaction(signed_tx.raw_transaction)
            
            hex_hash = self.w3.to_hex(tx_hash)
            print(f"[tx] success! hash: {hex_hash}")
            return hex_hash

        except Exception as e:
            print(f"[tx error] failed to send transaction: {str(e)}")
            return "error"