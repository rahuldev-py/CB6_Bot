"""
TrueData provider package for CB6 Quantum.

Public API — import from here in application code::

    from provider.truedata import (
        TrueDataConfig,
        TrueDataAuth,
        TrueDataRestClient,
        TrueDataWebSocketClient,
        TrueDataHistoricalClient,
        TrueDataSymbolMaster,
        TrueDataOptionChain,
        TrueDataGreeksClient,
        load_config,
    )
"""

from .auth import TrueDataAuth
from .config import TrueDataConfig, load_config
from .exceptions import (
    TrueDataAPIError,
    TrueDataAuthError,
    TrueDataConnectionError,
    TrueDataError,
    TrueDataRateLimitError,
    TrueDataSymbolNotFoundError,
    TrueDataTimeoutError,
)
from .greeks_client import TrueDataGreeksClient
from .historical_client import TrueDataHistoricalClient
from .models import (
    FeedLatencyStats,
    GreeksSnapshot,
    MarketBar,
    MarketTick,
    OptionChainRow,
    ProviderHealth,
    SymbolInfo,
    TrialResult,
)
from .option_chain import TrueDataOptionChain
from .rest_client import TrueDataRestClient
from .symbol_master import TrueDataSymbolMaster
from .websocket_client import TrueDataWebSocketClient

__all__ = [
    # Config
    "TrueDataConfig",
    "load_config",
    # Auth
    "TrueDataAuth",
    # Clients
    "TrueDataRestClient",
    "TrueDataWebSocketClient",
    "TrueDataHistoricalClient",
    "TrueDataSymbolMaster",
    "TrueDataOptionChain",
    "TrueDataGreeksClient",
    # Models
    "MarketTick",
    "MarketBar",
    "OptionChainRow",
    "GreeksSnapshot",
    "SymbolInfo",
    "ProviderHealth",
    "FeedLatencyStats",
    "TrialResult",
    # Exceptions
    "TrueDataError",
    "TrueDataAuthError",
    "TrueDataConnectionError",
    "TrueDataTimeoutError",
    "TrueDataRateLimitError",
    "TrueDataSymbolNotFoundError",
    "TrueDataAPIError",
]
