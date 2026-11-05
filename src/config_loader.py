import json
import os
from pathlib import Path
from typing import Any, Dict
from dotenv import load_dotenv
from web3 import Web3

# project root path relative to this file
PROJECT_ROOT = Path(__file__).resolve().parent.parent

class Config:
    def __init__(self, chain_name: str = "base"):
        load_dotenv(PROJECT_ROOT / ".env")
        
        self.chain_name = chain_name.lower()
        self.settings = self._load_json(PROJECT_ROOT / "configs" / "settings.json")
        self.chain = self._load_json(PROJECT_ROOT / "configs" / "chains" / f"{self.chain_name}.json")
        
        self._validate_and_setup()

    def _load_json(self, path: Path) -> Dict[str, Any]:
        if not path.exists():
            raise FileNotFoundError(f"Configuration file not found: {path}")
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _validate_and_setup(self) -> None:
        prefix = self.chain.get("rpc_env_prefix", self.chain_name.upper())
        self.rpc_http = os.getenv(f"{prefix}_RPC_HTTP")
        self.rpc_wss = os.getenv(f"{prefix}_RPC_WSS")

        if not self.rpc_http:
            raise ValueError(f"Environment variable {prefix}_RPC_HTTP is not set in the .env file.")

        # convert key addresses to the safe EIP-55 checksum format
        self.multicall3 = Web3.to_checksum_address(self.chain["multicall3_address"])
        self.aave_pool = Web3.to_checksum_address(self.chain["aave_v3_pool"])
        self.wrapped_native = Web3.to_checksum_address(self.chain["wrapped_native_token"])

        for dex_name, dex_data in self.chain["dexes"].items():
            dex_data["factory"] = Web3.to_checksum_address(dex_data["factory"])

        for token_symbol, token_data in self.chain["tokens"].items():
            token_data["address"] = Web3.to_checksum_address(token_data["address"])

    def get_token(self, symbol: str) -> Dict[str, Any]:
        symbol_upper = symbol.upper()
        if symbol_upper not in self.chain["tokens"]:
            raise KeyError(f"Token '{symbol_upper}' not found in the {self.chain_name} network configuration.")
        return self.chain["tokens"][symbol_upper]

if __name__ == "__main__":
    # quick test to run from the terminal: python src/config_loader.py
    import argparse
    parser = argparse.ArgumentParser(description="Test configuration loading")
    parser.add_argument("--chain", default="base", help="Network name (for example, base)")
    args = parser.parse_args()

    cfg = Config(chain_name=args.chain)
    print(f"[OK] Configuration loaded successfully for network: {cfg.chain_name.upper()}")
    print(f" - Chain ID: {cfg.chain['chain_id']}")
    print(f" - RPC HTTP: {cfg.rpc_http}")
    print(f" - Multicall3: {cfg.multicall3}")
    print(f" - Tracked tokens: {len(cfg.chain['tokens'])}")
    print(f" - DEX factories: {len(cfg.chain['dexes'])}")