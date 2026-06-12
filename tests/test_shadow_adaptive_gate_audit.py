from tools.shadow_adaptive_gate_audit import build_report, classify_trade


def _trade(**overrides):
    trade = {
        "direction": "BULLISH",
        "h4_bias": "BULLISH",
        "score": 16,
        "mss_type": "CHOCH",
        "cascade": False,
        "targets": "T1,T2,T3",
        "total_r": 3.0,
    }
    trade.update(overrides)
    return trade


def test_aligned_high_score_choch_is_full_size():
    result = classify_trade(_trade())
    assert result["shadow_decision"] == "FULL_SIZE"
    assert result["adaptive_r"] == 3.0


def test_eligible_counter_h4_is_caution_half_size_t1_only():
    result = classify_trade(_trade(h4_bias="BEARISH"))
    assert result["shadow_decision"] == "CAUTION"
    assert result["trade_allowed"] is True
    assert result["size_multiplier"] == 0.5
    assert result["t1_only"] is True
    assert result["adaptive_r"] == 0.5


def test_bos_score_bands():
    high = classify_trade(_trade(mss_type="BOS", score=15))
    middle = classify_trade(_trade(mss_type="BOS", score=13))
    low = classify_trade(_trade(mss_type="BOS", score=11))

    assert (high["shadow_decision"], high["adaptive_r"]) == ("REDUCED_SIZE", 2.25)
    assert (middle["shadow_decision"], middle["adaptive_r"]) == ("T1_ONLY", 0.5)
    assert (low["shadow_decision"], low["trade_allowed"]) == ("CAUTION", False)


def test_primary_source_does_not_reduce_size_by_itself():
    result = classify_trade(_trade(cascade=False))
    assert result["shadow_decision"] == "FULL_SIZE"
    assert result["size_multiplier"] == 1.0


def test_category_report_keeps_original_and_adaptive_outcomes(tmp_path):
    report = build_report({"trades": [_trade(mss_type="BOS", score=13)]}, tmp_path / "x.json")
    category = report["categories"]["T1_ONLY"]
    assert category["original_outcomes"]["total_r"] == 3.0
    assert category["adaptive_outcomes"]["total_r"] == 0.5
    assert report["verdict"]["activation_supported"] is False
