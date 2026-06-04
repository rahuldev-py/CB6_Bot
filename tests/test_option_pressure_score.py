from nse_options.option_pressure_engine import calculate_option_pressure


def test_option_pressure_bullish_pcr():
    ctx = {
        "strikes": [100],
        "ce": {100: {"oi": 100, "volume": 100}},
        "pe": {100: {"oi": 150, "volume": 150}},
    }
    out = calculate_option_pressure(ctx)
    assert out["option_bias"] == "BULLISH"


def test_option_pressure_missing_fields_safe():
    out = calculate_option_pressure({"strikes": [100], "ce": {}, "pe": {}})
    assert out["option_bias"] == "NEUTRAL"
