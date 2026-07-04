from backend.app.performance import build_symbol_performance, last_buy_dates


def test_fifo_realized_pnl():
    fills = [
        {"symbol": "NVDA", "side": "buy", "qty": "10", "price": "100", "transaction_time": "2026-06-01T14:30:00Z"},
        {"symbol": "NVDA", "side": "buy", "qty": "5", "price": "110", "transaction_time": "2026-06-02T14:30:00Z"},
        {"symbol": "NVDA", "side": "sell", "qty": "12", "price": "120", "transaction_time": "2026-06-05T14:30:00Z"},
    ]
    perf = build_symbol_performance(fills)
    nvda = perf["NVDA"]
    # FIFO: 10 shares +$20 each, then 2 shares +$10 each = $220
    assert nvda["realized_pnl"] == 220.0
    assert nvda["closed_trades"] == 1
    assert nvda["wins"] == 1
    assert nvda["win_rate_pct"] == 100.0
    assert nvda["bought_qty"] == 15.0
    assert nvda["sold_qty"] == 12.0


def test_losing_trade_counts_against_win_rate():
    fills = [
        {"symbol": "AMZN", "side": "buy", "qty": "10", "price": "200", "transaction_time": "2026-06-01T14:30:00Z"},
        {"symbol": "AMZN", "side": "sell", "qty": "10", "price": "190", "transaction_time": "2026-06-03T14:30:00Z"},
        {"symbol": "AMZN", "side": "buy", "qty": "10", "price": "185", "transaction_time": "2026-06-08T14:30:00Z"},
        {"symbol": "AMZN", "side": "sell", "qty": "10", "price": "195", "transaction_time": "2026-06-10T14:30:00Z"},
    ]
    perf = build_symbol_performance(fills)
    amzn = perf["AMZN"]
    assert amzn["realized_pnl"] == 0.0  # -100 then +100
    assert amzn["closed_trades"] == 2
    assert amzn["wins"] == 1
    assert amzn["win_rate_pct"] == 50.0


def test_last_buy_dates_parses_timestamps():
    fills = [
        {"symbol": "NVDA", "side": "buy", "qty": "10", "price": "100", "transaction_time": "2026-06-01T14:30:00Z"},
        {"symbol": "NVDA", "side": "buy", "qty": "5", "price": "104", "transaction_time": "2026-06-09T15:00:00Z"},
    ]
    dates = last_buy_dates(build_symbol_performance(fills))
    assert dates["NVDA"].isoformat().startswith("2026-06-09")


def test_malformed_fills_are_skipped():
    fills = [
        {"symbol": "", "side": "buy", "qty": "10", "price": "100"},
        {"symbol": "NVDA", "side": "buy", "qty": "bad", "price": "100"},
        {"symbol": "NVDA", "side": "buy", "qty": "10", "price": "100", "transaction_time": "2026-06-01T14:30:00Z"},
    ]
    perf = build_symbol_performance(fills)
    assert perf["NVDA"]["bought_qty"] == 10.0
