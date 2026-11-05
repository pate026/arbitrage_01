import time
from typing import Any, Dict


class PayloadBuilder:
    @staticmethod
    def build_execution_payload(opportunity: Dict[str, Any], block_number: int) -> Dict[str, Any]:
        # structures the profitable opportunity for a future smart contract caller
        route = opportunity["route"]
        s1 = route["step_1"]
        s2 = route["step_2"]

        # format strict payload matching aave flashloan receiver parameters
        payload = {
            "timestamp": int(time.time()),
            "block_number": block_number,
            "borrow_asset": route["borrow_token"],
            "borrow_symbol": opportunity["borrow_symbol"],
            "borrow_amount": opportunity["borrow_amount"],
            "borrow_amount_usd": opportunity["borrow_amount_usd"],
            "expected_net_profit_usd": opportunity["net_profit_usd"],
            "route_hops": [
                {
                    "step": 1,
                    "pool": s1["pool"],
                    "dex": s1["dex"],
                    "type": s1["type"],
                    "fee": s1["fee"],
                    "token_in": s1["token_in"],
                    "token_out": s1["token_out"],
                },
                {
                    "step": 2,
                    "pool": s2["pool"],
                    "dex": s2["dex"],
                    "type": s2["type"],
                    "fee": s2["fee"],
                    "token_in": s2["token_in"],
                    "token_out": s2["token_out"],
                },
            ],
            # minimum amount out required to protect against frontrunning / extra slippage
            "min_final_amount": opportunity["borrow_amount"] + int(
                opportunity["gross_profit_usd"] * 0.9
            ),
        }

        return payload


if __name__ == "__main__":
    # simple test payload
    mock_opp = {
        "borrow_symbol": "USDC",
        "borrow_amount": 500 * 10**6,
        "borrow_amount_usd": 500,
        "net_profit_usd": 219.30,
        "gross_profit_usd": 220.00,
        "route": {
            "borrow_token": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
            "step_1": {
                "pool": "0xpool1",
                "dex": "uniswap_v3",
                "type": "univ3",
                "fee": 3000,
                "token_in": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
                "token_out": "0x4200000000000000000000000000000000000006",
            },
            "step_2": {
                "pool": "0xpool2",
                "dex": "uniswap_v3",
                "type": "univ3",
                "fee": 10000,
                "token_in": "0x4200000000000000000000000000000000000006",
                "token_out": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
            },
        },
    }

    res = PayloadBuilder.build_execution_payload(mock_opp, block_number=53525369)
    print("--- payload builder test ---")
    print(f"borrow asset: {res['borrow_symbol']}")
    print(f"borrow amount: {res['borrow_amount']}")
    print(f"hops count: {len(res['route_hops'])}")
    print(f"min final amount out: {res['min_final_amount']}")