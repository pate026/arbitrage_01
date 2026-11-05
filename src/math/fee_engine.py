class FeeEngine:
    @staticmethod
    def get_aave_flashloan_fee(borrow_amount: int, fee_bps: int = 5) -> int:
        """
        calculates the Aave V3 flash loan fee.
        Aave V3 charges a default of 0.05% (5 bps) on the borrowed amount.
        """
        # 1 bps = 0.01%, so 5 bps is 0.05%
        return (borrow_amount * fee_bps) // 10000

    @staticmethod
    def estimate_gas_cost_in_target_token(
        gas_used: int, 
        gas_price_wei: int, 
        native_token_price_in_target: float, 
        target_decimals: int
    ) -> int:
        """
        converts the network gas cost into the target token's value (e.g., USDC).
        
        gas_used: estimated gas amount (e.g., 350000)
        gas_price_wei: gas price in wei
        native_token_price_in_target: e.g., price of WETH in USDC (e.g., 3000.0)
        target_decimals: decimals of the target token (e.g., USDC = 6)
        """
        # 1. calculate total gas cost in wei
        total_gas_wei = gas_used * gas_price_wei
        
        # 2. convert wei to native token unit (e.g., ETH)
        total_gas_native = total_gas_wei / (10 ** 18)
        
        # 3. multiply by native token price (e.g., ETH -> USDC)
        cost_in_target_float = total_gas_native * native_token_price_in_target
        
        # 4. return as an integer with the target token's decimals
        return int(cost_in_target_float * (10 ** target_decimals))

if __name__ == "__main__":
    # test fee calculation
    
    # 1. Aave loan fee: borrow 10,000 USDC (6 decimals)
    borrow_amount_usdc = 10_000 * 10**6
    flashloan_fee = FeeEngine.get_aave_flashloan_fee(borrow_amount_usdc)
    
    # 2. gas cost on Base network:
    # assume arbitrage costs approx 350,000 gas
    # assume Base network base gas price is 0.005 Gwei (5,000,000 wei)
    gas_limit = 350_000
    gas_price_wei = 5_000_000 
    
    # assume WETH price is 3000 USDC
    weth_price_usdc = 3000.0
    
    gas_cost_usdc = FeeEngine.estimate_gas_cost_in_target_token(
        gas_used=gas_limit,
        gas_price_wei=gas_price_wei,
        native_token_price_in_target=weth_price_usdc,
        target_decimals=6
    )
    
    print("--- Fee Calculation Test ---")
    print(f"borrowing: {borrow_amount_usdc / 10**6:.2f} USDC")
    print(f"1. Aave flash loan fee (0.05%): {flashloan_fee / 10**6:.2f} USDC")
    print(f"2. estimated gas cost (Base): {gas_cost_usdc / 10**6:.4f} USDC")
    print(f"total fees: {(flashloan_fee + gas_cost_usdc) / 10**6:.4f} USDC")
    print(f"-> arbitrage must yield more than this to be profitable.")