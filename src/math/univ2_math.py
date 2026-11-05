from eth_abi import decode

# getReserves() returns (uint112 reserve0, uint112 reserve1, uint32 blockTimestampLast)
V2_GET_RESERVES_SELECTOR = bytes.fromhex("0902f1ac")

class UniV2Math:
    @staticmethod
    def get_amount_out(amount_in: int, reserve_in: int, reserve_out: int, fee_bps: int) -> int:
        """
        Calculate the output token amount using the Uniswap V2 formula (x * y = k).
        Accounts for actual slippage (because amount_in affects the denominator)
        and the pool's fee (for example, 30 bps = 0.3%).
        """
        if amount_in <= 0 or reserve_in <= 0 or reserve_out <= 0:
            return 0

        # example: 10,000 - 30 = 9,970 (99.7% of the amount reaches the pool reserve)
        amount_in_with_fee = amount_in * (10000 - fee_bps)
        
        numerator = amount_in_with_fee * reserve_out
        denominator = (reserve_in * 10000) + amount_in_with_fee
        
        return numerator // denominator

    @staticmethod
    def decode_reserves(return_data: bytes) -> tuple[int, int]:
        """
        Decode getReserves() data returned by Multicall3.
        Return (reserve0, reserve1).
        """
        if len(return_data) != 96: # 3 x 32 bytes
            return 0, 0
            
        decoded = decode(["uint112", "uint112", "uint32"], return_data)
        return decoded[0], decoded[1]

if __name__ == "__main__":
    # test the math directly in this file
    # example: the pool has 10 ETH (18 decimals) and 30,000 USDC (6 decimals)
    reserve_eth = 10 * 10**18
    reserve_usdc = 30_000 * 10**6
    
    # try selling 1 ETH into the pool with a 0.3% fee (30 bps)
    sell_amount_eth = 1 * 10**18
    fee = 30
    
    # if tokenIn is ETH, it goes in the reserve_in position
    amount_out_usdc = UniV2Math.get_amount_out(
        amount_in=sell_amount_eth,
        reserve_in=reserve_eth,
        reserve_out=reserve_usdc,
        fee_bps=fee
    )
    
    print("--- Uniswap V2 Math Test ---")
    print("Selling: 1.0 ETH")
    print(f"Pool fee: {fee / 100}%")
    print(f"Received: {amount_out_usdc / 10**6:.2f} USDC")
    
    # the price should be slightly below 3,000 USDC due to fees and slippage