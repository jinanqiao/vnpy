from __future__ import annotations

from typing import Any

import pytest

from vnpy.alpha.research.qmt_gateway_data import QmtGatewayConfig
from vnpy.alpha.research.qmt_gateway_trade import (
    QmtGatewayTradeError,
    QmtOrderRequest,
    QmtTradeGatewayClient,
)


class FakeResponse:
    def __init__(self, payload: dict[str, Any], ok: bool = True, status_code: int = 200):
        self.payload = payload
        self.ok = ok
        self.status_code = status_code
        self.text = str(payload)

    def json(self) -> dict[str, Any]:
        return self.payload


def test_place_order_sends_token_and_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_request(method: str, url: str, **kwargs: Any) -> FakeResponse:
        calls.append({"method": method, "url": url, **kwargs})
        return FakeResponse({"dry_run": True, "order_id": "DRY_1"})

    monkeypatch.setattr("vnpy.alpha.research.qmt_gateway_trade.requests.request", fake_request)

    client = QmtTradeGatewayClient(QmtGatewayConfig(base_url="http://127.0.0.1:8710", token="secret"))
    result = client.place_order(QmtOrderRequest(symbol="600000.SH", side="buy", quantity=100, price=10.5))

    assert result["order_id"] == "DRY_1"
    assert calls[0]["method"] == "POST"
    assert calls[0]["url"] == "http://127.0.0.1:8710/orders"
    assert calls[0]["headers"] == {"X-API-Token": "secret"}
    assert calls[0]["json"] == {
        "symbol": "600000.SH",
        "side": "buy",
        "quantity": 100,
        "price": 10.5,
        "price_type": "limit",
    }


def test_trading_endpoint_requires_token() -> None:
    client = QmtTradeGatewayClient(QmtGatewayConfig(base_url="http://127.0.0.1:8710", token=""))

    with pytest.raises(QmtGatewayTradeError, match="QMT_GATEWAY_TOKEN"):
        client.account()


def test_state_normalizes_account_positions_orders_and_trades(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = {
        "/account": {
            "account": {
                "account_id": "A1",
                "available_cash": 9000,
                "cash": 10000,
                "frozen_cash": 1000,
                "market_value": 2100,
                "total_asset": 12100,
            }
        },
        "/positions": {
            "positions": [
                {
                    "symbol": "600000.SH",
                    "quantity": 200,
                    "available_quantity": 100,
                    "avg_price": 10.0,
                    "market_value": 2100.0,
                    "profit": 100.0,
                }
            ]
        },
        "/orders": {
            "orders": [
                {
                    "id": "O1",
                    "symbol": "600000.SH",
                    "side": "buy",
                    "quantity": 200,
                    "traded_quantity": 50,
                    "price": 10.5,
                    "status": "已报",
                }
            ]
        },
        "/trades": {
            "trades": [
                {
                    "id": "T1",
                    "order_id": "O1",
                    "symbol": "600000.SH",
                    "side": "buy",
                    "quantity": 50,
                    "price": 10.5,
                    "amount": 525,
                }
            ]
        },
        "/health": {"connected": True, "dry_run": True, "account_id": "A1"},
    }

    def fake_request(method: str, url: str, **kwargs: Any) -> FakeResponse:
        path = url.removeprefix("http://127.0.0.1:8710")
        return FakeResponse(responses[path])

    monkeypatch.setattr("vnpy.alpha.research.qmt_gateway_trade.requests.request", fake_request)

    client = QmtTradeGatewayClient(QmtGatewayConfig(base_url="http://127.0.0.1:8710", token="secret"))
    state = client.state()

    assert state["account_id"] == "A1"
    assert state["broker_connected"] is True
    assert state["broker_dry_run"] is True
    assert state["positions"]["600000.SH"]["frozen_quantity"] == 100
    assert state["orders"][0]["status"] == "partial"
    assert state["trades"][0]["amount"] == 525.0


def test_gateway_http_error_is_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_request(method: str, url: str, **kwargs: Any) -> FakeResponse:
        return FakeResponse({"error": "unauthorized"}, ok=False, status_code=401)

    monkeypatch.setattr("vnpy.alpha.research.qmt_gateway_trade.requests.request", fake_request)

    client = QmtTradeGatewayClient(QmtGatewayConfig(base_url="http://127.0.0.1:8710", token="bad"))

    with pytest.raises(QmtGatewayTradeError, match="unauthorized"):
        client.account()
