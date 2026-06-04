from nse_options.atm_strike_engine import nearest_atm_from_price, nearby_strikes, strike_gap


def test_nifty_atm():
    assert strike_gap("NIFTY") == 50
    assert nearest_atm_from_price("NIFTY", 24876) == 24900
    assert nearby_strikes(24900, 50, 1) == [24850, 24900, 24950]
