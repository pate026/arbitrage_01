import asyncio
from typing import Any, List, Tuple
from eth_abi import decode, encode
from web3 import Web3
from src.config_loader import Config
from src.rpc.provider import RPCProvider

# multicall3: aggregate3(Call3[] calls) returns (Result[] returnData)
# struct Call3 { address target; bool allowFailure; bytes callData; }
# struct Result { bool success; bytes returnData; }
MULTICALL3_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"internalType": "address", "name": "target", "type": "address"},
                    {"internalType": "bool", "name": "allowFailure", "type": "bool"},
                    {"internalType": "bytes", "name": "callData", "type": "bytes"},
                ],
                "internalType": "struct Multicall3.Call3[]",
                "name": "calls",
                "type": "tuple[]",
            }
        ],
        "name": "aggregate3",
        "outputs": [
            {
                "components": [
                    {"internalType": "bool", "name": "success", "type": "bool"},
                    {"internalType": "bytes", "name": "returnData", "type": "bytes"},
                ],
                "internalType": "struct Multicall3.Result[]",
                "name": "returnData",
                "type": "tuple[]",
            }
        ],
        "stateMutability": "payable",
        "type": "function",
    }
]


class Multicall:
    def __init__(self, provider: RPCProvider):
        self.provider = provider
        self.config = provider.config
        self.contract = self.provider.w3.eth.contract(
            address=self.config.multicall3,
            abi=MULTICALL3_ABI,
        )

    async def aggregate3(self, calls: List[Tuple[str, bool, bytes]]) -> List[Tuple[bool, bytes]]:
        """
        Execute multiple calls with a single RPC request.
        Argument calls: a list of tuples (target_address, allow_failure, call_data_bytes)
        Return a list of tuples (success: bool, return_data: bytes).
        """
        call_tuples = [
            (Web3.to_checksum_address(target), allow_failure, data)
            for target, allow_failure, data in calls
        ]
        return await self.contract.functions.aggregate3(call_tuples).call()


if __name__ == "__main__":
    # test Multicall3 by querying the decimals of two tokens in a single request:
    # python src/rpc/multicall.py
    async def main():
        cfg = Config("base")
        prov = RPCProvider(cfg)
        mc = Multicall(prov)

        # the decimals() function selector is keccak256("decimals()")[:4] -> 0x313ce567
        decimals_selector = bytes.fromhex("313ce567")

        weth = cfg.get_token("WETH")["address"]
        usdc = cfg.get_token("USDC")["address"]

        calls = [
            (weth, True, decimals_selector),
            (usdc, True, decimals_selector),
        ]

        results = await mc.aggregate3(calls)

        # decode the responses (uint8 is ABI-encoded as uint256)
        weth_dec = decode(["uint8"], results[0][1])[0]
        usdc_dec = decode(["uint8"], results[1][1])[0]

        print("[OK] Multicall3 response received successfully from the blockchain:")
        print(f" - WETH decimals: {weth_dec} (success: {results[0][0]})")
        print(f" - USDC decimals: {usdc_dec} (success: {results[1][0]})")

    asyncio.run(main())