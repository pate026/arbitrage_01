# selector for slot0(): keccak256("slot0()")[:4] -> 0x3850c7bd
v3_slot0_selector = bytes.fromhex("3850c7bd")
# selector for liquidity(): keccak256("liquidity()")[:4] -> 0x1a686502
v3_liquidity_selector = bytes.fromhex("1a686502")


class UniV3Math:
    @staticmethod
    def get_amount_out(
        amount_in: int,
        sqrt_price_x96: int,
        liquidity: int,
        fee: int,
        zero_for_one: bool,
    ) -> int:
        # single-tick model output calculation for uniswap v3
        if amount_in <= 0 or liquidity <= 0 or sqrt_price_x96 <= 0:
            return 0

        # fee deduction (1_000_000 = 100%)
        amount_in_less_fee = amount_in * (1_000_000 - fee) // 1_000_000
        q96 = 2**96

        if zero_for_one:
            # sell token0, buy token1
            numerator = liquidity * q96 * sqrt_price_x96
            denominator = (liquidity * q96) + (amount_in_less_fee * sqrt_price_x96)
            if denominator == 0:
                return 0
            sqrt_price_new = numerator // denominator
            amount_out = (liquidity * (sqrt_price_x96 - sqrt_price_new)) // q96
        else:
            # sell token1, buy token0
            sqrt_price_new = sqrt_price_x96 + ((amount_in_less_fee * q96) // liquidity)
            numerator = liquidity * q96 * (sqrt_price_new - sqrt_price_x96)
            denominator = sqrt_price_new * sqrt_price_x96
            if denominator == 0:
                return 0
            amount_out = numerator // denominator

        return amount_out

    @staticmethod
    def decode_slot0(return_data: bytes) -> tuple[int, int]:
        # raw bytes decoding: slot0 returns at least 64 bytes (sqrtPriceX96 + tick)
        if len(return_data) < 64:
            return 0, 0
        sqrt_price_x96 = int.from_bytes(return_data[0:32], byteorder="big")
        tick = int.from_bytes(return_data[32:64], byteorder="big", signed=True)
        return sqrt_price_x96, tick

    @staticmethod
    def decode_liquidity(return_data: bytes) -> int:
        # liquidity (uint128) is packed in the first 32-byte slot
        if len(return_data) < 32:
            return 0
        return int.from_bytes(return_data[0:32], byteorder="big")


if __name__ == "__main__":
    # test uniswap v3 math directly
    mock_sqrt_price = 1438965586616641882103445831623861
    mock_liquidity = 15000000000000000000
    mock_fee = 500
    sell_amount_usdc = 3000 * 10**6

    amount_out_weth = UniV3Math.get_amount_out(
        amount_in=sell_amount_usdc,
        sqrt_price_x96=mock_sqrt_price,
        liquidity=mock_liquidity,
        fee=mock_fee,
        zero_for_one=True,
    )

    print("--- uniswap v3 math test ---")
    print(f"sold: 3000 usdc (token0)")
    print(f"pool fee: {mock_fee / 10000}%")
    print(f"received: {amount_out_weth / 10**18:.4f} weth (token1)")