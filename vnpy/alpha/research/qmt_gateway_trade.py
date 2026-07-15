"""Trading-side HTTP client for the Windows QMT Gateway."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import requests

from vnpy.alpha.research.qmt_gateway_data import QmtGatewayConfig


class QmtGatewayTradeError(RuntimeError):
    """Raised when the QMT trade gateway cannot complete a request."""


@dataclass(frozen=True)
class QmtOrderRequest:
    """Order request accepted by the Windows QMT Gateway."""

    symbol: str
    side: str
    quantity: int
    price: float
    price_type: str = "limit"

    def to_payload(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "quantity": int(self.quantity),
            "price": float(self.price),
            "price_type": self.price_type,
        }


class QmtTradeGatewayClient:
    """Small adapter around the Windows-side QMT trading endpoints."""

    def __init__(self, config: QmtGatewayConfig):
        self.config = config

    def health(self) -> dict[str, Any]:
        """Return gateway process health. This endpoint does not require auth."""
        return self._request("GET", "/health", auth=False)

    def connect(self) -> dict[str, Any]:
        """Ask the gateway to connect and subscribe to the configured QMT account."""
        return self._request("POST", "/connect")

    def debug_trader(self) -> dict[str, Any]:
        return self._request("GET", "/debug/trader")

    def version(self) -> dict[str, Any]:
        return self._request("GET", "/version")

    def gateway_status(self) -> dict[str, Any]:
        return self._request("GET", "/status")

    def audit_requests(self, limit: int = 200) -> list[dict[str, Any]]:
        response = self._request("GET", "/audit/requests", params={"limit": int(limit)})
        return list(response.get("requests") or [])

    def events(self, after: int = 0, limit: int = 200) -> dict[str, Any]:
        return self._request("GET", "/events", params={"after": int(after), "limit": int(limit)})

    def poll_events(self, after: int = 0, limit: int = 200) -> dict[str, Any]:
        return self._request("POST", "/events/poll", json={"after": int(after), "limit": int(limit)})

    def accounts(self) -> list[dict[str, Any]]:
        return list(self._request("GET", "/accounts").get("accounts") or [])

    def account_status(self) -> list[dict[str, Any]]:
        return list(self._request("GET", "/account/status").get("status") or [])

    def account(self) -> dict[str, Any]:
        return dict(self._request("GET", "/account").get("account") or {})

    def positions(self) -> list[dict[str, Any]]:
        return list(self._request("GET", "/positions").get("positions") or [])

    def position(self, symbol: str) -> dict[str, Any]:
        return dict(self._request("GET", f"/positions/{symbol}").get("position") or {})

    def orders(self, cancelable_only: bool = False) -> list[dict[str, Any]]:
        response = self._request(
            "GET",
            "/orders",
            params={"cancelable_only": "true" if cancelable_only else "false"},
        )
        return list(response.get("orders") or [])

    def order(self, order_id: str) -> dict[str, Any]:
        return dict(self._request("GET", f"/orders/{order_id}").get("order") or {})

    def trades(self) -> list[dict[str, Any]]:
        return list(self._request("GET", "/trades").get("trades") or [])

    def place_order(self, request: QmtOrderRequest | dict[str, Any]) -> dict[str, Any]:
        payload = request.to_payload() if isinstance(request, QmtOrderRequest) else dict(request)
        return self._request("POST", "/orders", json=payload)

    def place_orders(self, orders: list[QmtOrderRequest | dict[str, Any]]) -> dict[str, Any]:
        payload = [order.to_payload() if isinstance(order, QmtOrderRequest) else dict(order) for order in orders]
        return self._request("POST", "/orders/batch", json={"orders": payload})

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        return self._request("POST", f"/orders/{order_id}/cancel")

    def cancel_orders(self, orders: list[dict[str, Any]]) -> dict[str, Any]:
        return self._request("POST", "/orders/cancel", json={"orders": orders})

    def cancel_all_orders(self, symbol: str = "", side: str = "") -> dict[str, Any]:
        return self._request("POST", "/orders/cancel-all", json={"symbol": symbol, "side": side})

    def cancel_order_by_sysid(self, market: str, sysid: str) -> dict[str, Any]:
        return self._request("POST", "/orders/cancel-by-sysid", json={"market": market, "sysid": sysid})

    def history(self, kind: str, **params: Any) -> Any:
        return self._request("GET", f"/history/{kind}", params=params).get("data")

    def ipo_data(self) -> dict[str, Any]:
        return dict(self._request("GET", "/ipo/data").get("ipo") or {})

    def ipo_purchase_limit(self) -> dict[str, Any]:
        return dict(self._request("GET", "/ipo/purchase-limit").get("limits") or {})

    def purchase_ipo(self, symbol: str, quantity: int) -> dict[str, Any]:
        return self._request("POST", "/ipo/purchase", json={"symbol": symbol, "quantity": int(quantity)})

    def position_statistics(self) -> list[dict[str, Any]]:
        return list(self._request("GET", "/positions/statistics").get("statistics") or [])

    def credit(self, kind: str) -> Any:
        return self._request("GET", f"/credit/{kind}").get("data")

    def credit_action(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", f"/credit/actions/{action}", json=payload)

    def raw_trader_call(self, method_name: str, args: list[Any] | None = None, kwargs: dict[str, Any] | None = None) -> Any:
        response = self._request(
            "POST",
            f"/debug/trader/call/{method_name}",
            json={"args": args or [], "kwargs": kwargs or {}},
        )
        return response.get("result")

    def market_quotes(self, symbols: list[str]) -> dict[str, Any]:
        return self._request("POST", "/market/quotes", json={"symbols": symbols}).get("quotes") or {}

    def market_subscribe(self, symbols: list[str], period: str = "1m") -> dict[str, Any]:
        return self._request("POST", "/market/subscribe", json={"symbols": symbols, "period": period})

    def market_unsubscribe(self, symbols: list[str]) -> dict[str, Any]:
        return self._request("POST", "/market/unsubscribe", json={"symbols": symbols})

    def raw_market_call(self, method_name: str, args: list[Any] | None = None, kwargs: dict[str, Any] | None = None) -> Any:
        response = self._request(
            "POST",
            f"/market/debug/call/{method_name}",
            json={"args": args or [], "kwargs": kwargs or {}},
        )
        return response.get("result")

    def state(self) -> dict[str, Any]:
        """Return a normalized account snapshot suitable for paper/live orchestration."""
        account = self.account()
        positions = self.positions()
        orders = self.orders()
        trades = self.trades()
        health = self.health()

        normalized_positions = _normalize_positions(positions)
        normalized_orders = [_normalize_order(order) for order in orders]
        normalized_trades = [_normalize_trade(trade) for trade in trades]
        available_cash = _as_float(account.get("available_cash", account.get("cash", 0)))
        market_value = _as_float(account.get("market_value"))
        total_asset = _as_float(account.get("total_asset"))
        if total_asset <= 0:
            total_asset = available_cash + market_value

        return {
            "account_id": account.get("account_id") or health.get("account_id"),
            "available_cash": available_cash,
            "cash": _as_float(account.get("cash", available_cash)),
            "frozen_cash": _as_float(account.get("frozen_cash")),
            "market_value": market_value,
            "total_asset": total_asset,
            "positions": normalized_positions,
            "orders": normalized_orders,
            "trades": normalized_trades,
            "broker_connected": bool(health.get("connected")),
            "broker_dry_run": bool(health.get("dry_run")),
            "broker_gateway_url": self.config.base_url,
            "updated_at": datetime.now().isoformat(),
        }

    def _request(self, method: str, path: str, auth: bool = True, **kwargs: Any) -> dict[str, Any]:
        headers = dict(kwargs.pop("headers", {}) or {})
        if auth:
            if not self.config.token:
                raise QmtGatewayTradeError("QMT_GATEWAY_TOKEN is required for trading endpoints")
            headers["X-API-Token"] = self.config.token

        try:
            response = requests.request(
                method,
                f"{self.config.base_url.rstrip('/')}{path}",
                headers=headers,
                timeout=self.config.timeout,
                **kwargs,
            )
        except requests.RequestException as exc:
            raise QmtGatewayTradeError(f"QMT Gateway request failed: {exc}") from exc

        try:
            data = response.json()
        except ValueError:
            data = {"error": response.text}

        if not response.ok:
            raise QmtGatewayTradeError(data.get("error") or f"QMT Gateway HTTP {response.status_code}")
        if isinstance(data, dict) and data.get("error"):
            raise QmtGatewayTradeError(str(data["error"]))
        return data if isinstance(data, dict) else {"data": data}


def _normalize_positions(positions: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for position in positions:
        symbol = str(position.get("symbol") or "").upper()
        if not symbol:
            continue
        quantity = _as_int(position.get("quantity"))
        available_quantity = _as_int(position.get("available_quantity", position.get("quantity")))
        avg_price = _as_float(position.get("avg_price", position.get("cost_price")))
        profit = _as_float(position.get("profit", position.get("position_profit")))
        result[symbol] = {
            "quantity": quantity,
            "available_quantity": available_quantity,
            "frozen_quantity": max(quantity - available_quantity, 0),
            "avg_price": avg_price,
            "cost_price": avg_price,
            "market_value": _as_float(position.get("market_value")),
            "profit": profit,
            "position_profit": profit,
            "raw": position,
        }
    return result


def _normalize_order(order: dict[str, Any]) -> dict[str, Any]:
    quantity = _as_int(order.get("quantity"))
    traded_quantity = _as_int(order.get("traded_quantity"))
    return {
        "id": str(order.get("id") or order.get("order_id") or ""),
        "sysid": str(order.get("sysid") or ""),
        "symbol": str(order.get("symbol") or "").upper(),
        "side": _normalize_side(order.get("side") or order.get("action")),
        "quantity": quantity,
        "price": _as_float(order.get("price")),
        "status": _normalize_status(order.get("status"), quantity, traded_quantity),
        "time": str(order.get("time") or ""),
        "traded_quantity": traded_quantity,
        "message": str(order.get("message") or ""),
        "raw": order,
    }


def _normalize_trade(trade: dict[str, Any]) -> dict[str, Any]:
    trade_id = trade.get("trade_id") or trade.get("id") or trade.get("order_id")
    return {
        "id": str(trade_id or ""),
        "trade_id": str(trade_id or ""),
        "order_id": str(trade.get("order_id") or ""),
        "symbol": str(trade.get("symbol") or "").upper(),
        "side": _normalize_side(trade.get("side") or trade.get("action")),
        "quantity": _as_int(trade.get("quantity")),
        "price": _as_float(trade.get("price")),
        "amount": _as_float(trade.get("amount")),
        "time": str(trade.get("time") or trade.get("timestamp") or ""),
        "raw": trade,
    }


def _normalize_side(value: Any) -> str:
    text = str(value or "").lower()
    if "sell" in text or "卖" in text:
        return "sell"
    return "buy"


def _normalize_status(value: Any, quantity: int, traded_quantity: int) -> str:
    text = str(value or "").lower()
    if text in {"56", "rejected", "reject", "failed", "废单"} or "reject" in text or "废" in text or "拒" in text:
        return "rejected"
    if text in {"54", "cancelled", "canceled"} or "cancel" in text or "撤" in text:
        return "cancelled"
    if quantity > 0 and traded_quantity >= quantity:
        return "executed"
    if traded_quantity > 0:
        return "partial"
    if text in {"55", "executed", "filled", "done"} or "done" in text or "filled" in text or "已成" in text:
        return "executed"
    return "pending"


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
