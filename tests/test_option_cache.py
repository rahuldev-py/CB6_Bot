import time

from nse_options.option_cache import OptionTTLCache


def test_option_cache_ttl():
    cache = OptionTTLCache(ttl_seconds=1)
    cache.set("x", 1)
    assert cache.get("x") == 1
    time.sleep(1.05)
    assert cache.get("x") is None
