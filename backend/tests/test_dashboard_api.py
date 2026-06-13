from fastapi.testclient import TestClient

import backend.app.main as main_module
from backend.app.main import app, scheduler


client = TestClient(app)


def test_dashboard_served_at_root():
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "BurgerBot Dashboard" in response.text


def test_connections_report_shape():
    response = client.get("/connections")
    assert response.status_code == 200
    report = response.json()
    assert report["overall"] in {"ok", "warn", "error"}
    component_ids = {component["id"] for component in report["components"]}
    assert {"alpaca_trading", "market_data", "analyst", "news", "telegram", "scheduler"} <= component_ids
    for component in report["components"]:
        assert component["status"] in {"ok", "warn", "error", "off"}
        assert component["detail"]


def test_get_settings_returns_runtime_values():
    response = client.get("/settings")
    assert response.status_code == 200
    settings = response.json()["settings"]
    assert settings["min_confidence"] == scheduler.risk_engine.min_confidence
    assert settings["max_open_positions"] == scheduler.risk_engine.max_open_positions
    assert "enable_auto_execution" in settings


def test_patch_settings_applies_to_live_engines():
    original_max = scheduler.risk_engine.max_open_positions
    original_conf = scheduler.risk_engine.min_confidence
    try:
        response = client.patch(
            "/settings",
            json={"max_open_positions": 22, "min_confidence": 0.55, "buy_threshold": 0.4},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["applied"] == {"max_open_positions": 22, "min_confidence": 0.55, "buy_threshold": 0.4}
        assert scheduler.risk_engine.max_open_positions == 22
        assert scheduler.risk_engine.min_confidence == 0.55
        assert scheduler.decision_engine.buy_threshold == 0.4
        assert body["settings"]["max_open_positions"] == 22
    finally:
        client.patch(
            "/settings",
            json={"max_open_positions": original_max, "min_confidence": original_conf, "buy_threshold": 0.35},
        )


def test_patch_settings_rejects_out_of_range_values():
    response = client.patch("/settings", json={"min_confidence": 2.0})
    assert response.status_code == 422


def test_patch_settings_rejects_empty_payload():
    response = client.patch("/settings", json={})
    assert response.status_code == 400


def test_patch_settings_rejects_stop_loss_above_take_profit():
    response = client.patch("/settings", json={"stop_loss_pct": 0.1, "take_profit_pct": 0.05})
    assert response.status_code == 400


def test_patch_settings_requires_token_when_configured():
    original_token = main_module.settings.botpress_verify_token
    main_module.settings.botpress_verify_token = "grill-secret"
    try:
        denied = client.patch("/settings", json={"min_confidence": 0.7})
        assert denied.status_code == 401
        allowed = client.patch(
            "/settings",
            json={"min_confidence": scheduler.risk_engine.min_confidence},
            headers={"X-Botpress-Token": "grill-secret"},
        )
        assert allowed.status_code == 200
    finally:
        main_module.settings.botpress_verify_token = original_token


def test_auto_execution_toggle_applies_to_trader():
    original = scheduler.trader.enabled
    try:
        response = client.patch("/settings", json={"enable_auto_execution": False})
        assert response.status_code == 200
        assert scheduler.trader.enabled is False
        assert response.json()["settings"]["enable_auto_execution"] is False
    finally:
        client.patch("/settings", json={"enable_auto_execution": original})
