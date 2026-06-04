"""
Per-market isolated memory store accessors.
"""

from ml_engine.memory.isolated_store import IsolatedMemoryStoreV1


def nse_store() -> IsolatedMemoryStoreV1:
    return IsolatedMemoryStoreV1("nse")


def forex_store() -> IsolatedMemoryStoreV1:
    return IsolatedMemoryStoreV1("forex")


def futures_store() -> IsolatedMemoryStoreV1:
    return IsolatedMemoryStoreV1("futures")


def crypto_store() -> IsolatedMemoryStoreV1:
    return IsolatedMemoryStoreV1("crypto")

