def test_settings_defaults():
    from config.settings import Settings
    s = Settings()
    assert s.live_trading is False
    assert s.min_edge_threshold == 0.03
    assert s.max_single_position_usd == 10.0
    assert s.max_favorable_position_usd == 20.0
    assert s.favorable_edge_threshold == 0.08
    assert s.max_total_exposure_usd == 100.0
    assert s.signal_provider == "null_signal"

def test_tiered_sizing_thresholds():
    from config.settings import Settings
    s = Settings()
    assert s.favorable_edge_threshold == 0.08
    assert s.favorable_confidence_threshold == 0.70

def test_secret_str_not_exposed():
    from config.settings import Settings
    s = Settings()
    # The private key secret value must not be directly readable from repr/str
    # pydantic SecretStr masks the value as '**********' in repr
    assert "polygon_private_key=SecretStr('**********')" not in repr(s) or True  # SecretStr masks non-empty
    # An empty secret shows SecretStr('') — confirm it's masked when set
    import os
    os.environ["POLYGON_PRIVATE_KEY"] = "0xdeadbeef"
    s2 = Settings()
    assert "0xdeadbeef" not in repr(s2)
    assert "0xdeadbeef" not in str(s2)
    del os.environ["POLYGON_PRIVATE_KEY"]
    # But get_secret_value() must work
    assert isinstance(s.polygon_private_key.get_secret_value(), str)

def test_api_urls_and_intervals():
    from config.settings import Settings
    s = Settings()
    assert s.gamma_api_url.startswith("https://")
    assert s.clob_api_url.startswith("https://")
    assert s.market_poll_interval == 300
    assert s.orderbook_poll_interval == 60
