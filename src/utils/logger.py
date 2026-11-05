import csv
from datetime import datetime
from pathlib import Path
from typing import Any, Dict


class ArbitrageLogger:
    def __init__(self, log_dir: str = "data/logs"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.csv_path = self.log_dir / "opportunities.csv"
        self._init_csv()

    def _init_csv(self) -> None:
        # creates the csv file with headers if it does not exist
        if not self.csv_path.exists():
            with open(self.csv_path, mode="w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "timestamp",
                    "block_number",
                    "borrow_symbol",
                    "borrow_amount_usd",
                    "step1_dex",
                    "step1_fee",
                    "step2_dex",
                    "step2_fee",
                    "gross_profit_usd",
                    "aave_fee_usd",
                    "gas_cost_usd",
                    "net_profit_usd",
                ])

    def log_opportunity(self, opp: Dict[str, Any], block_number: int) -> None:
        # appends a profitable arbitrage opportunity into the csv
        s1 = opp["route"]["step_1"]
        s2 = opp["route"]["step_2"]
        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        with open(self.csv_path, mode="a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                now_str,
                block_number,
                opp["borrow_symbol"],
                opp["borrow_amount_usd"],
                s1["dex"],
                s1["fee"],
                s2["dex"],
                s2["fee"],
                f"{opp['gross_profit_usd']:.2f}",
                f"{opp['aave_fee_usd']:.2f}",
                f"{opp['gas_cost_usd']:.4f}",
                f"{opp['net_profit_usd']:.2f}",
            ])


if __name__ == "__main__":
    # test logger directly
    logger = ArbitrageLogger()
    mock_opp = {
        "borrow_symbol": "USDC",
        "borrow_amount_usd": 1000,
        "gross_profit_usd": 15.50,
        "aave_fee_usd": 0.50,
        "gas_cost_usd": 0.005,
        "net_profit_usd": 14.995,
        "route": {
            "step_1": {"dex": "uniswap_v3", "fee": 3000},
            "step_2": {"dex": "uniswap_v3", "fee": 10000},
        },
    }
    logger.log_opportunity(mock_opp, block_number=53525369)
    print("--- logger test ---")
    print(f"written mock entry to {logger.csv_path}")