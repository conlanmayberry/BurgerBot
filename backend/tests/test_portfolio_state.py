from backend.app.risk.engine import PortfolioState


def test_portfolio_state_tracks_open_buy_orders_as_pending_exposure():
    state = PortfolioState.from_api_payload(
        {
            "equity": 100_000,
            "cash": 80_000,
            "day_pnl_pct": 0.0,
            "positions": [{"symbol": "MSFT", "qty": "3"}],
            "open_orders": [
                {"symbol": "AMZN", "side": "buy", "qty": "24", "status": "accepted"},
                {"symbol": "AMZN", "side": "buy", "qty": "24", "status": "new"},
                {"symbol": "AAPL", "side": "sell", "qty": "1", "status": "accepted"},
            ],
        }
    )

    assert state.open_symbols == {"MSFT"}
    assert state.pending_order_symbols == {"AMZN", "AAPL"}
    assert state.pending_buy_symbols == {"AMZN"}
    assert state.pending_sell_symbols == {"AAPL"}
    assert state.pending_buy_quantities == {"AMZN": 48}
    assert state.has_pending_buy("amzn")
    assert state.total_buy_exposure_symbols() == {"MSFT", "AMZN"}
