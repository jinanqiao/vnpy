from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[3]
GATEWAY_PATH = ROOT / "infra" / "qmt_gateway" / "qmt_gateway.py"


def load_gateway() -> ModuleType:
    spec = importlib.util.spec_from_file_location("qmt_gateway_server_under_test", GATEWAY_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_mock_gateway_extended_routes() -> None:
    gateway = load_gateway()
    config = gateway.GatewayConfig(
        api_token="secret",
        mock_mode=True,
        dry_run=True,
        account_id="MOCK",
        enforce_trading_hours=False,
    )
    app = gateway.create_app(config)
    client = app.test_client()
    headers = {"X-API-Token": "secret"}

    status = client.get("/status", headers=headers)
    assert status.status_code == 200
    assert status.get_json()["gateway"]["connected"] is True

    order = client.post(
        "/orders",
        headers=headers,
        json={"client_order_id": "c1", "symbol": "600000.SH", "side": "buy", "quantity": 100, "price": 10.5},
    )
    assert order.status_code == 200
    first_order_id = order.get_json()["order_id"]

    replay = client.post(
        "/orders",
        headers=headers,
        json={"client_order_id": "c1", "symbol": "600000.SH", "side": "buy", "quantity": 100, "price": 10.5},
    )
    assert replay.status_code == 200
    assert replay.get_json()["order_id"] == first_order_id
    assert replay.get_json()["idempotent_replay"] is True

    batch = client.post(
        "/orders/batch",
        headers=headers,
        json={
            "orders": [
                {"client_order_id": "b1", "symbol": "600000.SH", "side": "buy", "quantity": 100, "price": 10.5}
            ]
        },
    )
    assert batch.status_code == 200
    assert batch.get_json()["results"][0]["ok"] is True

    cancel_all = client.post("/orders/cancel-all", headers=headers, json={"symbol": "600000.SH"})
    assert cancel_all.status_code == 200

    events = client.post("/events/poll", headers=headers, json={"after": 0, "limit": 20})
    assert events.status_code == 200
    assert events.get_json()["last_seq"] >= 1

    quotes = client.post("/market/quotes", headers=headers, json={"symbols": ["600000.SH", "000001.SZ"]})
    assert quotes.status_code == 200
    assert set(quotes.get_json()["quotes"]) == {"600000.SH", "000001.SZ"}


def test_raw_calls_are_disabled_by_default() -> None:
    gateway = load_gateway()
    config = gateway.GatewayConfig(api_token="secret", mock_mode=True)
    app = gateway.create_app(config)
    client = app.test_client()

    response = client.post(
        "/debug/trader/call/query_stock_orders",
        headers={"X-API-Token": "secret"},
        json={"args": [], "kwargs": {}},
    )

    assert response.status_code == 400
    assert "disabled" in response.get_json()["error"]


def test_token_is_required_when_configured() -> None:
    gateway = load_gateway()
    config = gateway.GatewayConfig(api_token="", mock_mode=True, require_api_token=True)
    app = gateway.create_app(config)
    client = app.test_client()

    health = client.get("/health")
    assert health.status_code == 200

    account = client.get("/account")
    assert account.status_code == 500
    assert "api_token" in account.get_json()["error"]
