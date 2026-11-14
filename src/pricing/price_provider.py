import time
from dataclasses import dataclass
from typing import Dict, Optional

from web3 import Web3

from src.config_loader import Config
from src.rpc.provider import RPCProvider


@dataclass(frozen=True)
class PriceData:
    """Normalized USD price information for one token."""

    symbol: str
    price_usd: float
    source: str
    updated_at: Optional[int] = None


class PriceProviderError(Exception):
    """Base exception for price provider errors."""


class PriceUnavailableError(PriceProviderError):
    """Raised when no price source is configured or available."""


class InvalidPriceError(PriceProviderError):
    """Raised when a price source returns invalid price data."""


class PriceProvider:
    """
    Provides normalized token/USD prices.

    Supported price sources:
      - fixed
      - chainlink

    Price source selection is configured per token
    in the current chain configuration.
    """

    # Minimal Chainlink AggregatorV3Interface ABI.
    CHAINLINK_ABI = [
        {
            "inputs": [],
            "name": "decimals",
            "outputs": [
                {
                    "internalType": "uint8",
                    "name": "",
                    "type": "uint8",
                }
            ],
            "stateMutability": "view",
            "type": "function",
        },
        {
            "inputs": [],
            "name": "latestRoundData",
            "outputs": [
                {
                    "internalType": "uint80",
                    "name": "roundId",
                    "type": "uint80",
                },
                {
                    "internalType": "int256",
                    "name": "answer",
                    "type": "int256",
                },
                {
                    "internalType": "uint256",
                    "name": "startedAt",
                    "type": "uint256",
                },
                {
                    "internalType": "uint256",
                    "name": "updatedAt",
                    "type": "uint256",
                },
                {
                    "internalType": "uint80",
                    "name": "answeredInRound",
                    "type": "uint80",
                },
            ],
            "stateMutability": "view",
            "type": "function",
        },
    ]

    def __init__(
        self,
        config: Config,
        provider: RPCProvider,
        cache_ttl: float = 30.0,
    ):
        self.config = config
        self.provider = provider
        self.cache_ttl = cache_ttl

        self._cache: Dict[str, PriceData] = {}
        self._cache_time: Dict[str, float] = {}

    async def get_usd_price(
        self,
        symbol: str,
        force_refresh: bool = False,
    ) -> float:
        """Return the USD price for one token."""

        data = await self.get_price_data(
            symbol,
            force_refresh=force_refresh,
        )

        return data.price_usd

    async def get_price_data(
        self,
        symbol: str,
        force_refresh: bool = False,
    ) -> PriceData:
        """Return normalized price data for one token."""

        symbol = symbol.upper()

        if not force_refresh:
            cached = self._get_cached(symbol)

            if cached is not None:
                return cached

        try:
            token = self.config.get_token(symbol)
        except KeyError as exc:
            raise PriceUnavailableError(
                f"Token '{symbol}' is not configured "
                f"for {self.config.chain_name}."
            ) from exc

        price_config = token.get("price")

        if not price_config:
            raise PriceUnavailableError(
                f"No price source configured for "
                f"{symbol} on {self.config.chain_name}."
            )

        source = price_config.get("source")

        if source == "fixed":
            data = self._get_fixed_price(
                symbol,
                price_config,
            )

        elif source == "chainlink":
            data = await self._get_chainlink_price(
                symbol,
                price_config,
            )

        else:
            raise PriceUnavailableError(
                f"Unsupported price source "
                f"'{source}' for {symbol}."
            )

        self._cache[symbol] = data
        self._cache_time[symbol] = time.monotonic()

        return data

    async def get_usd_prices(
        self,
        symbols: list[str],
        force_refresh: bool = False,
    ) -> Dict[str, float]:
        """
        Return USD prices for multiple tokens.

        Tokens whose prices cannot be resolved are omitted.
        """

        result: Dict[str, float] = {}

        unique_symbols = dict.fromkeys(
            symbol.upper() for symbol in symbols
        )

        for symbol in unique_symbols:
            try:
                result[symbol] = await self.get_usd_price(
                    symbol,
                    force_refresh=force_refresh,
                )
            except PriceProviderError:
                continue

        return result

    def _get_fixed_price(
        self,
        symbol: str,
        price_config: dict,
    ) -> PriceData:
        """Read a fixed USD price from configuration."""

        try:
            price = float(price_config["usd"])
        except (KeyError, TypeError, ValueError) as exc:
            raise InvalidPriceError(
                f"Invalid fixed USD price for {symbol}."
            ) from exc

        if price <= 0:
            raise InvalidPriceError(
                f"USD price for {symbol} must be positive."
            )

        return PriceData(
            symbol=symbol,
            price_usd=price,
            source="fixed",
            updated_at=None,
        )

    async def _get_chainlink_price(
        self,
        symbol: str,
        price_config: dict,
    ) -> PriceData:
        """Read token/USD price from a Chainlink price feed."""

        feed_address = price_config.get("feed")

        if not feed_address:
            raise PriceUnavailableError(
                f"Chainlink feed address missing for {symbol}."
            )

        try:
            feed_address = Web3.to_checksum_address(
                feed_address
            )
        except (TypeError, ValueError) as exc:
            raise PriceUnavailableError(
                f"Invalid Chainlink feed address for {symbol}."
            ) from exc

        contract = self.provider.w3.eth.contract(
            address=feed_address,
            abi=self.CHAINLINK_ABI,
        )

        try:
            decimals = await contract.functions.decimals().call()

            round_data = await (
                contract.functions.latestRoundData().call()
            )

        except Exception as exc:
            raise PriceUnavailableError(
                f"Failed to read Chainlink price "
                f"for {symbol}: {exc}"
            ) from exc

        _, answer, _, updated_at, _ = round_data

        if answer <= 0:
            raise InvalidPriceError(
                f"Chainlink returned an invalid "
                f"price for {symbol}: {answer}"
            )

        if updated_at <= 0:
            raise InvalidPriceError(
                f"Chainlink returned an invalid "
                f"update timestamp for {symbol}."
            )

        price_usd = answer / (10 ** decimals)

        if price_usd <= 0:
            raise InvalidPriceError(
                f"Calculated USD price for "
                f"{symbol} is invalid."
            )

        return PriceData(
            symbol=symbol,
            price_usd=float(price_usd),
            source="chainlink",
            updated_at=updated_at,
        )

    def _get_cached(
        self,
        symbol: str,
    ) -> Optional[PriceData]:
        """Return cached price if it is still within the local TTL."""

        data = self._cache.get(symbol)
        cached_at = self._cache_time.get(symbol)

        if data is None or cached_at is None:
            return None

        age = time.monotonic() - cached_at

        if age >= self.cache_ttl:
            self._cache.pop(symbol, None)
            self._cache_time.pop(symbol, None)
            return None

        return data