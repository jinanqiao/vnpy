"""
Lightweight Windows-side gateway for Guojin QMT / miniQMT.

Run this process on the Windows machine where QMT is installed and logged in.
It exposes a small HTTP API for vn.py or other services.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from flask import Flask, jsonify, request


LOGGER = logging.getLogger("qmt_gateway")


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _pick(obj: Any, names: Iterable[str], default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        for name in names:
            if name in obj:
                return obj[name]
        return default
    for name in names:
        if hasattr(obj, name):
            return getattr(obj, name)
    return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            pass
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _normalize_symbol(symbol: str) -> str:
    """Accept 600000, 600000.SH, 000001.SZ and return QMT style code."""
    value = (symbol or "").strip().upper()
    if re.fullmatch(r"\d{6}", value):
        if value.startswith(("6", "5", "9")):
            return f"{value}.SH"
        return f"{value}.SZ"
    if re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", value):
        return value
    raise ValueError("symbol must be like 600000, 600000.SH or 000001.SZ")


def _normalize_period(period: str) -> str:
    value = (period or "1m").strip().lower()
    mapping = {
        "1min": "1m",
        "1m": "1m",
        "5min": "5m",
        "5m": "5m",
        "15min": "15m",
        "15m": "15m",
        "30min": "30m",
        "30m": "30m",
        "1h": "1h",
        "60m": "1h",
        "1d": "1d",
        "day": "1d",
    }
    if value not in mapping:
        raise ValueError("period must be one of 1m, 5m, 15m, 30m, 1h, 1d")
    return mapping[value]


def _normalize_adjust_type(adjust: str) -> str:
    value = (adjust or "none").strip().lower()
    mapping = {
        "none": "none",
        "no": "none",
        "front": "front",
        "qfq": "front",
        "back": "back",
        "hfq": "back",
        "front_ratio": "front_ratio",
        "back_ratio": "back_ratio",
    }
    if value not in mapping:
        raise ValueError("adjust must be one of none, front, back, front_ratio, back_ratio")
    return mapping[value]


def _format_qmt_time(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (int, float)):
        text = str(int(value))
        try:
            if len(text) >= 13:
                return datetime.fromtimestamp(int(value) / 1000).isoformat()
            if len(text) == 14:
                return datetime.strptime(text, "%Y%m%d%H%M%S").isoformat()
            if len(text) == 8:
                return datetime.strptime(text, "%Y%m%d").date().isoformat()
        except Exception:
            return text
    return str(value)


def _history_start_time(period: str, count: int) -> str:
    if period == "1d":
        days = max(30, min(365 * 20, count * 2 + 30))
        return (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")

    minutes_per_bar = {
        "1m": 1,
        "5m": 5,
        "15m": 15,
        "30m": 30,
        "1h": 60,
    }.get(period, 1)
    days = max(5, min(365, int(count * minutes_per_bar / 240) + 5))
    return (datetime.now() - timedelta(days=days)).strftime("%Y%m%d%H%M%S")


def _bars_from_frame(frame: Any) -> List[Dict[str, Any]]:
    if frame is None:
        return []
    try:
        records = frame.reset_index().to_dict("records")
    except Exception:
        if isinstance(frame, list):
            records = frame
        elif isinstance(frame, dict):
            records = [frame]
        else:
            return []

    bars = []
    for row in records:
        time_value = _pick(row, ["time", "timetag", "datetime", "date", "index"])
        bars.append(
            {
                "time": _format_qmt_time(time_value),
                "open": _as_float(_pick(row, ["open", "Open"])),
                "high": _as_float(_pick(row, ["high", "High"])),
                "low": _as_float(_pick(row, ["low", "Low"])),
                "close": _as_float(_pick(row, ["close", "Close"])),
                "volume": _as_int(_pick(row, ["volume", "vol", "Volume"])),
                "amount": _as_float(_pick(row, ["amount", "turnover", "Amount"])),
            }
        )
    return bars


def _records_from_value(value: Any, sample_rows: int = 0) -> Any:
    """Convert pandas/numpy-heavy QMT values to JSON-safe records."""
    if value is None:
        return None
    if hasattr(value, "reset_index") and hasattr(value, "to_dict"):
        try:
            frame = value.reset_index()
            if sample_rows and hasattr(frame, "head"):
                frame = frame.head(sample_rows)
            return _json_safe(frame.to_dict("records"))
        except Exception:
            pass
    if isinstance(value, dict):
        return {str(k): _records_from_value(v, sample_rows=sample_rows) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        rows = list(value[:sample_rows]) if sample_rows else list(value)
        return [_records_from_value(v, sample_rows=sample_rows) for v in rows]
    return _json_safe(value)


def _qmt_object_dict(obj: Any) -> Dict[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    out: Dict[str, Any] = {}
    for name in dir(obj):
        if name.startswith("_"):
            continue
        try:
            value = getattr(obj, name)
        except Exception:
            continue
        if callable(value):
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            out[name] = value
    return out


def _qmt_list(value: Any) -> List[Dict[str, Any]]:
    return [_qmt_object_dict(item) for item in (value or [])]


def _symbol_from_qmt(symbol: str) -> Dict[str, Any]:
    code = (symbol or "").strip().upper()
    if not code:
        return {}
    if "." in code:
        raw_code, exchange = code.split(".", 1)
    else:
        raw_code = code
        exchange = "SH" if raw_code.startswith(("6", "5", "9")) else "SZ"
        code = f"{raw_code}.{exchange}"
    return {
        "symbol": code,
        "code": raw_code,
        "exchange": exchange,
        "market": "A",
        "board": _board_from_code(code),
        "status": "active",
    }


def _board_from_code(symbol: str) -> str:
    code = symbol.split(".")[0]
    if symbol.endswith(".BJ"):
        return "bj"
    if code.startswith("688"):
        return "star"
    if code.startswith(("300", "301")):
        return "chinext"
    return "main"


def _instrument_detail_fields(detail: Any) -> Dict[str, Any]:
    if not detail:
        return {}
    name = _pick(detail, ["InstrumentName", "instrument_name", "name", "Name"])
    open_date = _pick(detail, ["OpenDate", "open_date", "上市日期"])
    expire_date = _pick(detail, ["ExpireDate", "expire_date", "delist_date"])
    return {
        "name": name,
        "list_date": _format_qmt_time(open_date)[:10] if open_date else "",
        "delist_date": _format_qmt_time(expire_date)[:10] if expire_date else "",
        "pre_close": _as_float(_pick(detail, ["PreClose", "pre_close"])),
        "limit_up": _as_float(_pick(detail, ["UpStopPrice", "up_stop_price"])),
        "limit_down": _as_float(_pick(detail, ["DownStopPrice", "down_stop_price"])),
        "float_volume": _as_float(_pick(detail, ["FloatVolume", "FloatVolumn", "float_volume"])),
        "total_volume": _as_float(_pick(detail, ["TotalVolume", "TotalVolumn", "total_volume"])),
        "price_tick": _as_float(_pick(detail, ["PriceTick", "price_tick"])),
        "instrument_status": _pick(detail, ["InstrumentStatus", "instrument_status"]),
        "is_trading": _pick(detail, ["IsTrading", "is_trading"]),
        "raw": _json_safe(detail),
    }


def _summarize_value(value: Any, sample_rows: int = 3) -> Dict[str, Any]:
    summary = {"type": type(value).__name__}
    if value is None:
        summary["is_none"] = True
        return summary
    if isinstance(value, dict):
        summary["keys"] = [str(k) for k in list(value.keys())[:20]]
        summary["size"] = len(value)
        return summary
    if isinstance(value, list):
        summary["size"] = len(value)
        summary["sample"] = _json_safe(value[:sample_rows])
        return summary
    if hasattr(value, "shape"):
        try:
            summary["shape"] = list(value.shape)
        except Exception:
            pass
    if hasattr(value, "columns"):
        try:
            summary["columns"] = [str(c) for c in list(value.columns)]
        except Exception:
            pass
    if hasattr(value, "index"):
        try:
            summary["index_name"] = str(getattr(value.index, "name", ""))
        except Exception:
            pass
    if hasattr(value, "head"):
        try:
            summary["sample"] = _json_safe(value.head(sample_rows).reset_index().to_dict("records"))
        except Exception as exc:
            summary["sample_error"] = str(exc)
    else:
        summary["repr"] = str(value)[:500]
    return summary


def _now_in_trading_hours() -> bool:
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    return (9 * 60 + 25 <= minutes <= 11 * 60 + 30) or (13 * 60 <= minutes <= 15 * 60)


@dataclass
class GatewayConfig:
    host: str = "127.0.0.1"
    port: int = 8710
    api_token: str = ""
    mock_mode: bool = False
    dry_run: bool = True
    qmt_user_path: str = ""
    account_id: str = ""
    account_type: str = "STOCK"
    session_id: int = 10001
    strategy_name: str = "vnpy-alpha"
    order_remark: str = "vnpy-alpha"
    enforce_trading_hours: bool = True
    lot_size: int = 100
    max_order_value: float = 20000.0
    max_order_quantity: int = 10000
    allowed_symbols: List[str] = field(default_factory=list)
    blocked_symbols: List[str] = field(default_factory=list)
    min_order_interval_seconds: float = 1.0
    market_timeout_seconds: float = 3.0
    require_api_token: bool = True
    max_batch_orders: int = 20
    allow_raw_trader_calls: bool = False
    allow_raw_market_calls: bool = False
    allowed_raw_trader_methods: List[str] = field(default_factory=list)
    allowed_raw_market_methods: List[str] = field(default_factory=list)

    @classmethod
    def from_file(cls, path: Path) -> "GatewayConfig":
        data = _load_json(path)
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class QmtGatewayError(RuntimeError):
    pass


class QmtMarketData:
    """Read-only market data access via xtquant.xtdata."""

    def __init__(self, config: GatewayConfig):
        self.config = config
        self._xtdata = None

    def _call_with_timeout(self, func, timeout: Optional[float] = None):
        result_queue: "queue.Queue[tuple[bool, Any]]" = queue.Queue(maxsize=1)

        def runner():
            try:
                result_queue.put((True, func()))
            except Exception as exc:
                result_queue.put((False, exc))

        t = threading.Thread(target=runner, daemon=True)
        t.start()
        t.join(timeout if timeout is not None else self.config.market_timeout_seconds)
        if t.is_alive():
            raise QmtGatewayError("QMT market data request timed out")
        ok, result = result_queue.get()
        if ok:
            return result
        raise result

    def _module(self):
        if self.config.mock_mode:
            return None
        if self._xtdata is None:
            try:
                from xtquant import xtdata
            except Exception as exc:  # pragma: no cover - only available on Windows QMT
                raise QmtGatewayError(f"failed to import xtquant.xtdata: {exc}") from exc
            self._xtdata = xtdata
        return self._xtdata

    def quote(self, symbol: str) -> Dict[str, Any]:
        symbol = _normalize_symbol(symbol)
        ticks = self.fulltick([symbol])
        tick = ticks.get(symbol) or {}
        return {
            "symbol": symbol,
            "price": _as_float(_pick(tick, ["lastPrice", "last_price", "price"])),
            "open": _as_float(_pick(tick, ["open", "openPrice"])),
            "high": _as_float(_pick(tick, ["high", "highPrice"])),
            "low": _as_float(_pick(tick, ["low", "lowPrice"])),
            "pre_close": _as_float(_pick(tick, ["lastClose", "preClose", "pre_close"])),
            "volume": _as_int(_pick(tick, ["volume", "vol"])),
            "amount": _as_float(_pick(tick, ["amount", "turnover"])),
            "bid_price": _pick(tick, ["bidPrice", "bid_price"], []),
            "ask_price": _pick(tick, ["askPrice", "ask_price"], []),
            "bid_volume": _pick(tick, ["bidVol", "bidVolume", "bid_volume"], []),
            "ask_volume": _pick(tick, ["askVol", "askVolume", "ask_volume"], []),
            "time": _format_qmt_time(_pick(tick, ["time", "timetag", "datetime"])),
            "raw": _json_safe(tick),
        }

    def subscribe(self, symbols: List[str], period: str = "1m") -> Dict[str, Any]:
        normalized = [_normalize_symbol(s) for s in symbols if (s or "").strip()]
        if not normalized:
            return {"symbols": [], "subscribed": 0}
        if self.config.mock_mode:
            return {"symbols": normalized, "subscribed": len(normalized)}
        xtdata = self._module()
        period = _normalize_period(period)

        def do_subscribe():
            results = {}
            for symbol in normalized:
                try:
                    results[symbol] = xtdata.subscribe_quote(symbol, period=period)
                except TypeError:
                    results[symbol] = xtdata.subscribe_quote(symbol)
            return results

        return {"symbols": normalized, "results": self._call_with_timeout(do_subscribe)}

    def unsubscribe(self, symbols: List[str]) -> Dict[str, Any]:
        normalized = [_normalize_symbol(s) for s in symbols if (s or "").strip()]
        if not normalized:
            return {"symbols": [], "unsubscribed": 0}
        if self.config.mock_mode:
            return {"symbols": normalized, "unsubscribed": len(normalized)}
        xtdata = self._module()
        if not hasattr(xtdata, "unsubscribe_quote"):
            raise QmtGatewayError("xtdata.unsubscribe_quote is not available")

        def do_unsubscribe():
            results = {}
            for symbol in normalized:
                results[symbol] = xtdata.unsubscribe_quote(symbol)
            return results

        return {"symbols": normalized, "results": self._call_with_timeout(do_unsubscribe)}

    def quotes(self, symbols: List[str]) -> Dict[str, Any]:
        normalized = [_normalize_symbol(s) for s in symbols if (s or "").strip()]
        ticks = self.fulltick(normalized)
        return {"quotes": {symbol: self.quote(symbol) if symbol not in ticks else _json_safe(ticks.get(symbol)) for symbol in normalized}}

    def fulltick(self, symbols: List[str]) -> Dict[str, Dict[str, Any]]:
        normalized = [_normalize_symbol(s) for s in symbols if (s or "").strip()]
        if not normalized:
            return {}
        if self.config.mock_mode:
            return {s: {"lastPrice": 10.0, "volume": 0, "time": datetime.now().isoformat()} for s in normalized}
        xtdata = self._module()
        ticks = self._call_with_timeout(lambda: xtdata.get_full_tick(normalized) or {})
        return {k: _json_safe(v) for k, v in ticks.items()}

    def bars(self, symbol: str, period: str = "1m", count: int = 240, adjust: str = "none") -> List[Dict[str, Any]]:
        symbol = _normalize_symbol(symbol)
        period = _normalize_period(period)
        adjust = _normalize_adjust_type(adjust)
        count = max(1, min(int(count or 240), 5000))
        if self.config.mock_mode:
            now = datetime.now()
            return [
                {
                    "time": (now - timedelta(minutes=count - i)).isoformat(),
                    "open": 10.0,
                    "high": 10.1,
                    "low": 9.9,
                    "close": 10.0,
                    "volume": 0,
                    "amount": 0.0,
                }
                for i in range(count)
            ]

        xtdata = self._module()
        self._download_history(xtdata, symbol, period, count)
        data = self._call_with_timeout(
            lambda: xtdata.get_market_data_ex(
                ["time", "open", "high", "low", "close", "volume", "amount"],
                [symbol],
                period=period,
                count=count,
                dividend_type=adjust,
                fill_data=True,
            )
        )
        frame = data.get(symbol) if isinstance(data, dict) else None
        return _bars_from_frame(frame)

    def _download_history(self, xtdata: Any, symbol: str, period: str, count: int) -> None:
        if not hasattr(xtdata, "download_history_data"):
            return
        start_time = _history_start_time(period, count)

        def do_download():
            try:
                return xtdata.download_history_data(symbol, period=period, start_time=start_time, end_time="")
            except TypeError:
                return xtdata.download_history_data(symbol, period, start_time, "")

        self._call_with_timeout(do_download, timeout=max(self.config.market_timeout_seconds, 20))

    def trading_dates(self, market: str = "SH", count: int = 250) -> List[str]:
        count = max(1, min(int(count or 250), 5000))
        if self.config.mock_mode:
            today = datetime.now().date()
            return [str(today - timedelta(days=i)) for i in range(count)][::-1]
        xtdata = self._module()
        if hasattr(xtdata, "get_trading_dates"):
            dates = self._call_with_timeout(lambda: xtdata.get_trading_dates(market, count=count))
        else:
            dates = []
        return [_format_qmt_time(d) for d in (dates or [])]

    def symbols(self, sector: str = "沪深A股", include_detail: bool = True, limit: int = 10000) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit or 10000), 20000))
        if self.config.mock_mode:
            return [
                {"symbol": "600000.SH", "code": "600000", "exchange": "SH", "name": "浦发银行", "market": "A", "board": "main", "status": "active"},
                {"symbol": "000001.SZ", "code": "000001", "exchange": "SZ", "name": "平安银行", "market": "A", "board": "main", "status": "active"},
            ][:limit]
        xtdata = self._module()

        def do_list():
            if hasattr(xtdata, "download_sector_data"):
                try:
                    xtdata.download_sector_data()
                except Exception:
                    LOGGER.exception("download_sector_data failed")
            codes = xtdata.get_stock_list_in_sector(sector) if hasattr(xtdata, "get_stock_list_in_sector") else []
            rows = []
            for code in list(codes or [])[:limit]:
                row = _symbol_from_qmt(code)
                if not row:
                    continue
                if include_detail and hasattr(xtdata, "get_instrument_detail"):
                    try:
                        detail = xtdata.get_instrument_detail(row["symbol"], True)
                    except TypeError:
                        detail = xtdata.get_instrument_detail(row["symbol"])
                    except Exception:
                        detail = {}
                    row.update(_instrument_detail_fields(detail))
                rows.append(row)
            return rows

        return self._call_with_timeout(do_list, timeout=max(self.config.market_timeout_seconds, 30))

    def sectors(self) -> List[str]:
        if self.config.mock_mode:
            return ["沪深A股", "上证A股", "深证A股", "沪深300"]
        xtdata = self._module()
        if not hasattr(xtdata, "get_sector_list"):
            return []
        return self._call_with_timeout(lambda: xtdata.get_sector_list() or [], timeout=max(self.config.market_timeout_seconds, 20))

    def sector_stocks(self, sector: str = "沪深A股", as_of: str = "", include_detail: bool = False, limit: int = 10000) -> Dict[str, Any]:
        if self.config.mock_mode:
            return {"sector": sector, "as_of": as_of, "stocks": self.symbols(sector, include_detail, limit)}
        xtdata = self._module()
        if hasattr(xtdata, "download_sector_data"):
            try:
                self._call_with_timeout(lambda: xtdata.download_sector_data(), timeout=max(self.config.market_timeout_seconds, 30))
            except Exception:
                LOGGER.exception("download_sector_data failed")

        def do_list():
            raw_codes = xtdata.get_stock_list_in_sector(sector, as_of.replace("-", "")) if as_of else xtdata.get_stock_list_in_sector(sector)
            rows = []
            for code in list(raw_codes or [])[: max(1, min(int(limit or 10000), 20000))]:
                row = _symbol_from_qmt(code)
                if include_detail:
                    row.update(self.instrument_detail(row["symbol"], complete=True))
                rows.append(row)
            return rows

        return {"sector": sector, "as_of": as_of, "stocks": self._call_with_timeout(do_list, timeout=max(self.config.market_timeout_seconds, 30))}

    def instrument_detail(self, symbol: str, complete: bool = True) -> Dict[str, Any]:
        symbol = _normalize_symbol(symbol)
        base = _symbol_from_qmt(symbol)
        if self.config.mock_mode:
            return {**base, "name": symbol, "is_trading": True}
        xtdata = self._module()
        if not hasattr(xtdata, "get_instrument_detail"):
            return base

        def do_detail():
            try:
                detail = xtdata.get_instrument_detail(symbol, complete)
            except TypeError:
                detail = xtdata.get_instrument_detail(symbol)
            return {**base, **_instrument_detail_fields(detail or {})}

        return self._call_with_timeout(do_detail, timeout=max(self.config.market_timeout_seconds, 20))

    def index_weight(self, index_code: str) -> Dict[str, Any]:
        index_code = _normalize_symbol(index_code)
        if self.config.mock_mode:
            return {"index_code": index_code, "weights": {}}
        xtdata = self._module()
        if not hasattr(xtdata, "get_index_weight"):
            return {"index_code": index_code, "weights": {}}
        weights = self._call_with_timeout(lambda: xtdata.get_index_weight(index_code) or {}, timeout=max(self.config.market_timeout_seconds, 20))
        return {"index_code": index_code, "weights": _json_safe(weights)}

    def financial_data(self, symbols: List[str], tables: List[str], start_time: str = "", end_time: str = "", report_type: str = "report_time", sample_rows: int = 0) -> Dict[str, Any]:
        normalized = [_normalize_symbol(s) for s in symbols if str(s or "").strip()]
        if not normalized:
            raise ValueError("symbols is required")
        if len(normalized) > 50:
            raise ValueError("financial data supports at most 50 symbols per request")
        if self.config.mock_mode:
            return {"symbols": normalized, "tables": tables, "data": {}}
        xtdata = self._module()
        if not hasattr(xtdata, "get_financial_data"):
            raise QmtGatewayError("xtdata.get_financial_data is not available")
        data = self._call_with_timeout(
            lambda: xtdata.get_financial_data(normalized, tables or [], start_time, end_time, report_type),
            timeout=max(self.config.market_timeout_seconds, 60),
        )
        return {"symbols": normalized, "tables": tables, "start_time": start_time, "end_time": end_time, "report_type": report_type, "data": _records_from_value(data, sample_rows=sample_rows)}

    def download_financial_data(self, symbols: List[str], tables: List[str], start_time: str = "", end_time: str = "") -> Dict[str, Any]:
        normalized = [_normalize_symbol(s) for s in symbols if str(s or "").strip()]
        if not normalized:
            raise ValueError("symbols is required")
        if self.config.mock_mode:
            return {"symbols": normalized, "tables": tables, "downloaded": True}
        xtdata = self._module()
        if not hasattr(xtdata, "download_financial_data"):
            raise QmtGatewayError("xtdata.download_financial_data is not available")
        result = self._call_with_timeout(lambda: xtdata.download_financial_data(normalized, tables or [], start_time, end_time), timeout=max(self.config.market_timeout_seconds, 60))
        return {"symbols": normalized, "tables": tables, "start_time": start_time, "end_time": end_time, "result": _json_safe(result)}

    def download_sector_data(self) -> Dict[str, Any]:
        if self.config.mock_mode:
            return {"downloaded": True}
        xtdata = self._module()
        if not hasattr(xtdata, "download_sector_data"):
            raise QmtGatewayError("xtdata.download_sector_data is not available")
        result = self._call_with_timeout(lambda: xtdata.download_sector_data(), timeout=max(self.config.market_timeout_seconds, 60))
        return {"downloaded": True, "result": _json_safe(result)}

    def debug_xtdata(self) -> Dict[str, Any]:
        if self.config.mock_mode:
            return {"mock_mode": True}
        xtdata = self._module()
        names = [
            "subscribe_quote",
            "unsubscribe_quote",
            "get_full_tick",
            "get_market_data_ex",
            "get_market_data",
            "get_l2_quote",
            "get_l2_order",
            "get_l2_transaction",
            "get_l2_order_queue",
            "download_history_data",
            "download_history_data2",
            "get_trading_dates",
            "download_sector_data",
            "get_stock_list_in_sector",
            "get_instrument_detail",
            "get_index_weight",
            "get_financial_data",
            "download_financial_data",
        ]
        return {
            "module": str(xtdata),
            "functions": {name: hasattr(xtdata, name) for name in names},
        }

    def raw_call(self, method_name: str, args: List[Any], kwargs: Dict[str, Any]) -> Dict[str, Any]:
        if not self.config.allow_raw_market_calls:
            raise QmtGatewayError("raw market calls are disabled")
        allowed = set(self.config.allowed_raw_market_methods)
        if allowed and method_name not in allowed:
            raise QmtGatewayError("raw market method is not allowed")
        if method_name.startswith("_") or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", method_name):
            raise ValueError("invalid method name")
        xtdata = self._module()
        if not hasattr(xtdata, method_name):
            raise QmtGatewayError(f"xtdata method not found: {method_name}")
        result = self._call_with_timeout(lambda: getattr(xtdata, method_name)(*(args or []), **(kwargs or {})), timeout=max(self.config.market_timeout_seconds, 60))
        return {"method": method_name, "result": _records_from_value(result)}

    def debug_bars(self, symbol: str, period: str = "1m", count: int = 5, adjust: str = "none") -> Dict[str, Any]:
        symbol = _normalize_symbol(symbol)
        period = _normalize_period(period)
        adjust = _normalize_adjust_type(adjust)
        count = max(1, min(int(count or 5), 50))
        if self.config.mock_mode:
            return {"mock_mode": True, "adjust": adjust, "bars": self.bars(symbol, period, count, adjust)}

        xtdata = self._module()
        out: Dict[str, Any] = {
            "symbol": symbol,
            "period": period,
            "adjust": adjust,
            "count": count,
            "xtdata": self.debug_xtdata(),
            "download": None,
            "get_market_data_ex_fields": None,
            "get_market_data_ex_all_fields": None,
            "parsed_bars": [],
        }

        try:
            self._download_history(xtdata, symbol, period, count)
            out["download"] = {"ok": True}
        except Exception as exc:
            out["download"] = {"ok": False, "error": str(exc)}

        for label, fields in [
            ("get_market_data_ex_fields", ["time", "open", "high", "low", "close", "volume", "amount"]),
            ("get_market_data_ex_all_fields", []),
        ]:
            try:
                data = self._call_with_timeout(
                    lambda fields=fields: xtdata.get_market_data_ex(
                        fields,
                        [symbol],
                        period=period,
                        count=count,
                        dividend_type=adjust,
                        fill_data=True,
                    )
                )
                frame = data.get(symbol) if isinstance(data, dict) else None
                out[label] = {"ok": True, "data": _summarize_value(data), "frame": _summarize_value(frame)}
                if not out["parsed_bars"]:
                    out["parsed_bars"] = _bars_from_frame(frame)
            except Exception as exc:
                out[label] = {"ok": False, "error": str(exc)}

        return out

    def self_test(self, symbol: str = "600000.SH") -> Dict[str, Any]:
        symbol = _normalize_symbol(symbol)

        def capture(name: str, func):
            try:
                value = func()
                return {"ok": True, "result": value}
            except Exception as exc:
                return {"ok": False, "error": str(exc)}

        quote = capture("quote", lambda: self.quote(symbol))
        subscribe_1m = capture("subscribe_1m", lambda: self.subscribe([symbol], "1m"))
        bars_1m = capture("bars_1m", lambda: self.bars(symbol, "1m", 5, "none"))
        bars_1d = capture("bars_1d", lambda: self.bars(symbol, "1d", 5, "none"))
        bars_1d_front = capture("bars_1d_front", lambda: self.bars(symbol, "1d", 5, "front"))
        dates = capture("trading_dates", lambda: self.trading_dates("SH", 5))
        sectors = capture("sectors", self.sectors)
        symbols = capture("symbols", lambda: self.symbols("沪深A股", include_detail=False, limit=5))
        instrument = capture("instrument_detail", lambda: self.instrument_detail(symbol, complete=True))

        return {
            "symbol": symbol,
            "xtdata": capture("xtdata", self.debug_xtdata),
            "quote": {
                **quote,
                "summary": _summarize_value(quote.get("result")) if quote.get("ok") else None,
            },
            "subscribe_1m": subscribe_1m,
            "bars_1m": {
                **bars_1m,
                "count": len(bars_1m.get("result") or []) if bars_1m.get("ok") else 0,
            },
            "bars_1d": {
                **bars_1d,
                "count": len(bars_1d.get("result") or []) if bars_1d.get("ok") else 0,
            },
            "bars_1d_front": {
                **bars_1d_front,
                "count": len(bars_1d_front.get("result") or []) if bars_1d_front.get("ok") else 0,
            },
            "trading_dates": dates,
            "sectors": {
                **sectors,
                "count": len(sectors.get("result") or []) if sectors.get("ok") else 0,
            },
            "symbols": {
                **symbols,
                "count": len(symbols.get("result") or []) if symbols.get("ok") else 0,
            },
            "instrument_detail": instrument,
        }


class QmtClient:
    def __init__(self, config: GatewayConfig):
        self.config = config
        self._trader = None
        self._account = None
        self._xtconstant = None
        self._last_order_at = 0.0
        self._started_at = datetime.now()
        self._connected_at: Optional[datetime] = None
        self._last_query_at: Optional[datetime] = None
        self._last_order_result_at: Optional[datetime] = None
        self._last_error = ""
        self._event_seq = 0
        self._events: List[Dict[str, Any]] = []
        self._idempotent_orders: Dict[str, Dict[str, Any]] = {}

    @property
    def connected(self) -> bool:
        return self.config.mock_mode or self._trader is not None

    def connect(self) -> None:
        if self.config.mock_mode:
            return
        if self._trader is not None:
            return
        if not self.config.qmt_user_path:
            raise QmtGatewayError("qmt_user_path is required")
        if not self.config.account_id:
            raise QmtGatewayError("account_id is required")

        try:
            from xtquant.xttrader import XtQuantTrader
            from xtquant.xttype import StockAccount
            from xtquant import xtconstant
        except Exception as exc:  # pragma: no cover - only available on Windows QMT
            raise QmtGatewayError(f"failed to import xtquant: {exc}") from exc

        trader = XtQuantTrader(self.config.qmt_user_path, self.config.session_id)
        trader.start()
        result = trader.connect()
        if result not in (0, True, None):
            raise QmtGatewayError(f"QMT connect failed, result={result}")

        account = StockAccount(self.config.account_id, self.config.account_type)
        sub_result = trader.subscribe(account)
        if sub_result not in (0, True, None):
            raise QmtGatewayError(f"QMT account subscribe failed, result={sub_result}")

        self._trader = trader
        self._account = account
        self._xtconstant = xtconstant
        self._connected_at = datetime.now()
        self._record_event("connection", {"connected": True, "account_id": self.config.account_id})

    def health(self) -> Dict[str, Any]:
        return {
            "ok": True,
            "connected": self.connected,
            "mock_mode": self.config.mock_mode,
            "dry_run": self.config.dry_run,
            "account_id": self.config.account_id,
            "time": datetime.now().isoformat(),
            "started_at": self._started_at.isoformat(),
            "connected_at": self._connected_at.isoformat() if self._connected_at else "",
            "last_query_at": self._last_query_at.isoformat() if self._last_query_at else "",
            "last_order_result_at": self._last_order_result_at.isoformat() if self._last_order_result_at else "",
            "last_error": self._last_error,
            "event_seq": self._event_seq,
        }

    def status(self) -> Dict[str, Any]:
        return {
            **self.health(),
            "config": {
                "host": self.config.host,
                "port": self.config.port,
                "account_type": self.config.account_type,
                "session_id": self.config.session_id,
                "strategy_name": self.config.strategy_name,
                "order_remark": self.config.order_remark,
                "enforce_trading_hours": self.config.enforce_trading_hours,
                "lot_size": self.config.lot_size,
                "max_order_value": self.config.max_order_value,
                "max_order_quantity": self.config.max_order_quantity,
                "allowed_symbols_count": len(self.config.allowed_symbols),
                "blocked_symbols_count": len(self.config.blocked_symbols),
                "min_order_interval_seconds": self.config.min_order_interval_seconds,
                "require_api_token": self.config.require_api_token,
                "max_batch_orders": self.config.max_batch_orders,
                "allow_raw_trader_calls": self.config.allow_raw_trader_calls,
                "allow_raw_market_calls": self.config.allow_raw_market_calls,
            },
        }

    def query_account_infos(self) -> List[Dict[str, Any]]:
        if self.config.mock_mode:
            return [{"account_id": self.config.account_id or "MOCK_ACCOUNT", "account_type": self.config.account_type}]
        self.connect()
        self._mark_query()
        return _qmt_list(self._trader.query_account_infos() or [])

    def query_account_status(self) -> List[Dict[str, Any]]:
        if self.config.mock_mode:
            return [{"account_id": self.config.account_id or "MOCK_ACCOUNT", "status": "mock"}]
        self.connect()
        self._mark_query()
        return _qmt_list(self._trader.query_account_status() or [])

    def query_account(self) -> Dict[str, Any]:
        if self.config.mock_mode:
            return {
                "account_id": self.config.account_id or "MOCK_ACCOUNT",
                "cash": 100000.0,
                "available_cash": 100000.0,
                "frozen_cash": 0.0,
                "market_value": 0.0,
                "total_asset": 100000.0,
            }
        self.connect()
        self._mark_query()
        asset = self._trader.query_stock_asset(self._account)
        cash = _as_float(_pick(asset, ["cash", "enable_balance", "available_cash"]))
        frozen_cash = _as_float(_pick(asset, ["frozen_cash", "frozen_balance"]))
        return {
            "account_id": _pick(asset, ["account_id"], self.config.account_id),
            "cash": cash,
            "available_cash": _as_float(_pick(asset, ["available_cash", "enable_balance"], cash)),
            "frozen_cash": frozen_cash,
            "market_value": _as_float(_pick(asset, ["market_value"])),
            "total_asset": _as_float(_pick(asset, ["total_asset", "asset"])),
        }

    def query_positions(self) -> List[Dict[str, Any]]:
        if self.config.mock_mode:
            return []
        self.connect()
        self._mark_query()
        positions = self._trader.query_stock_positions(self._account) or []
        return [self._serialize_position(p) for p in positions]

    def query_position(self, symbol: str) -> Dict[str, Any]:
        symbol = _normalize_symbol(symbol)
        if self.config.mock_mode:
            return {}
        self.connect()
        self._mark_query()
        position = self._trader.query_stock_position(self._account, symbol)
        return self._serialize_position(position) if position else {}

    def query_orders(self, cancelable_only: bool = False) -> List[Dict[str, Any]]:
        if self.config.mock_mode:
            return []
        self.connect()
        self._mark_query()
        orders = self._trader.query_stock_orders(self._account, cancelable_only) or []
        return [self._serialize_order(o) for o in orders]

    def query_order(self, order_id: str) -> Dict[str, Any]:
        if not order_id:
            raise ValueError("order_id is required")
        if self.config.mock_mode:
            return {}
        self.connect()
        self._mark_query()
        order = self._trader.query_stock_order(self._account, _as_int(order_id, order_id))
        return self._serialize_order(order) if order else {}

    def query_trades(self) -> List[Dict[str, Any]]:
        if self.config.mock_mode:
            return []
        self.connect()
        self._mark_query()
        trades = self._trader.query_stock_trades(self._account) or []
        return [
            {
                "id": _pick(t, ["traded_id", "trade_id", "id"]),
                "order_id": _pick(t, ["order_id"]),
                "symbol": _pick(t, ["stock_code", "symbol"]),
                "side": self._side_from_qmt(_pick(t, ["order_type", "entrust_bs"])),
                "quantity": _as_int(_pick(t, ["traded_volume", "volume", "quantity"])),
                "price": _as_float(_pick(t, ["traded_price", "price"])),
                "amount": _as_float(_pick(t, ["traded_amount", "amount"])),
                "time": str(_pick(t, ["traded_time", "trade_time", "time"], "")),
            }
            for t in trades
        ]

    def place_order(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        symbol = _normalize_symbol(payload.get("symbol", ""))
        side = (payload.get("side") or payload.get("action") or "").strip().lower()
        quantity = _as_int(payload.get("quantity"))
        price = _as_float(payload.get("price"))
        client_order_id = str(payload.get("client_order_id") or payload.get("request_id") or "").strip()
        if client_order_id and client_order_id in self._idempotent_orders:
            result = dict(self._idempotent_orders[client_order_id])
            result["idempotent_replay"] = True
            return result

        self._validate_order(symbol, side, quantity, price)
        if self.config.dry_run:
            result = {
                "dry_run": True,
                "order_id": f"DRY_{int(time.time())}_{uuid.uuid4().hex[:8]}",
                "client_order_id": client_order_id,
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "price": price,
            }
            self._last_order_result_at = datetime.now()
            self._remember_order(client_order_id, result)
            self._record_event("order", result)
            return result

        self.connect()
        order_type = self._qmt_order_type(side)
        price_type = self._qmt_price_type(payload.get("price_type", "limit"))
        strategy_name = str(payload.get("strategy_name") or self.config.strategy_name)
        order_remark = str(payload.get("order_remark") or payload.get("remark") or self.config.order_remark)
        order_id = self._trader.order_stock(
            self._account,
            symbol,
            order_type,
            quantity,
            price_type,
            price,
            strategy_name,
            order_remark,
        )
        self._last_order_at = time.time()
        self._last_order_result_at = datetime.now()
        result = {"dry_run": False, "order_id": str(order_id), "client_order_id": client_order_id, "symbol": symbol, "side": side, "quantity": quantity, "price": price}
        self._remember_order(client_order_id, result)
        self._record_event("order", result)
        return result

    def place_orders(self, orders: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not orders:
            raise ValueError("orders is required")
        if len(orders) > self.config.max_batch_orders:
            raise ValueError(f"batch order count exceeds max_batch_orders={self.config.max_batch_orders}")
        results = []
        for index, order in enumerate(orders):
            try:
                results.append({"index": index, "ok": True, "result": self.place_order(order)})
            except Exception as exc:
                self._last_error = str(exc)
                results.append({"index": index, "ok": False, "error": str(exc)})
        return {"count": len(orders), "results": results}

    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        if not order_id:
            raise ValueError("order_id is required")
        if self.config.dry_run or self.config.mock_mode:
            result = {"dry_run": self.config.dry_run, "order_id": order_id, "cancelled": True}
            self._record_event("cancel", result)
            return result
        self.connect()
        result = self._trader.cancel_order_stock(self._account, _as_int(order_id, order_id))
        out = {"order_id": order_id, "result": result}
        self._record_event("cancel", out)
        return out

    def cancel_order_by_sysid(self, market: str, sysid: str) -> Dict[str, Any]:
        if not market or not sysid:
            raise ValueError("market and sysid are required")
        if self.config.dry_run or self.config.mock_mode:
            return {"dry_run": self.config.dry_run, "market": market, "sysid": sysid, "cancelled": True}
        self.connect()
        result = self._trader.cancel_order_stock_sysid(self._account, market, sysid)
        return {"market": market, "sysid": sysid, "result": result}

    def cancel_orders(self, orders: List[Dict[str, Any]]) -> Dict[str, Any]:
        results = []
        for index, order in enumerate(orders or []):
            try:
                if order.get("sysid"):
                    result = self.cancel_order_by_sysid(str(order.get("market") or ""), str(order.get("sysid") or ""))
                else:
                    result = self.cancel_order(str(order.get("order_id") or order.get("id") or ""))
                results.append({"index": index, "ok": True, "result": result})
            except Exception as exc:
                self._last_error = str(exc)
                results.append({"index": index, "ok": False, "error": str(exc)})
        return {"count": len(orders or []), "results": results}

    def cancel_all_orders(self, symbol: str = "", side: str = "") -> Dict[str, Any]:
        orders = self.query_orders(cancelable_only=True)
        symbol_filter = _normalize_symbol(symbol) if symbol else ""
        side_filter = side.lower().strip()
        targets = []
        for order in orders:
            if symbol_filter and str(order.get("symbol") or "").upper() != symbol_filter:
                continue
            if side_filter and str(order.get("side") or "").lower() != side_filter:
                continue
            targets.append(order)
        payload = [{"order_id": order.get("id"), "sysid": order.get("sysid")} for order in targets]
        return {"matched": len(targets), **self.cancel_orders(payload)}

    def query_history(self, kind: str, **kwargs: Any) -> Any:
        if self.config.mock_mode:
            return []
        self.connect()
        mapping = {
            "orders": ["query_stock_orders", "query_orders"],
            "trades": ["query_stock_trades", "query_trades"],
            "assets": ["query_stock_asset"],
            "positions": ["query_stock_positions"],
            "funds": ["query_fund_flow", "query_funds"],
            "deliveries": ["query_deliver_order", "query_deliveries"],
        }
        names = mapping.get(kind)
        if not names:
            raise ValueError("kind must be one of orders, trades, assets, positions, funds, deliveries")
        for name in names:
            if hasattr(self._trader, name):
                method = getattr(self._trader, name)
                try:
                    value = method(self._account, **kwargs)
                except TypeError:
                    value = method(self._account)
                return _records_from_value(value)
        raise QmtGatewayError(f"QMT trader does not support history kind={kind}")

    def query_new_purchase_limit(self) -> Dict[str, Any]:
        if self.config.mock_mode:
            return {}
        self.connect()
        return _json_safe(self._trader.query_new_purchase_limit(self._account) or {})

    def query_ipo_data(self) -> Dict[str, Any]:
        if self.config.mock_mode:
            return {}
        self.connect()
        return _json_safe(self._trader.query_ipo_data() or {})

    def purchase_ipo(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        symbol = _normalize_symbol(payload.get("symbol", ""))
        quantity = _as_int(payload.get("quantity"))
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        if self.config.dry_run or self.config.mock_mode:
            return {"dry_run": self.config.dry_run, "symbol": symbol, "quantity": quantity, "submitted": True}
        self.connect()
        for name in ["purchase_new_stock", "order_stock_async", "order_stock"]:
            if hasattr(self._trader, name):
                method = getattr(self._trader, name)
                try:
                    result = method(self._account, symbol, quantity)
                except TypeError:
                    continue
                return {"symbol": symbol, "quantity": quantity, "result": _json_safe(result)}
        raise QmtGatewayError("QMT trader does not support IPO purchase")

    def query_position_statistics(self) -> List[Dict[str, Any]]:
        if self.config.mock_mode:
            return []
        self.connect()
        return _qmt_list(self._trader.query_position_statistics(self._account) or [])

    def query_credit(self, kind: str) -> Any:
        if self.config.mock_mode:
            return [] if kind != "detail" else {}
        self.connect()
        mapping = {
            "detail": self._trader.query_credit_detail,
            "compacts": self._trader.query_stk_compacts,
            "subjects": self._trader.query_credit_subjects,
            "slo-code": self._trader.query_credit_slo_code,
            "assure": self._trader.query_credit_assure,
        }
        if kind not in mapping:
            raise ValueError("kind must be one of detail, compacts, subjects, slo-code, assure")
        value = mapping[kind](self._account)
        return _qmt_list(value) if isinstance(value, list) else _qmt_object_dict(value)

    def credit_action(self, action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if self.config.dry_run or self.config.mock_mode:
            return {"dry_run": self.config.dry_run, "action": action, "payload": payload, "submitted": True}
        self.connect()
        mapping = {
            "buy": ["credit_buy", "order_credit_stock", "order_stock"],
            "sell": ["credit_sell", "order_credit_stock", "order_stock"],
            "repay-cash": ["repay_credit", "cash_repay"],
            "repay-stock": ["stock_repay", "repay_stock"],
            "direct-repay": ["direct_repay"],
            "collateral-buy": ["collateral_buy", "order_stock"],
            "collateral-sell": ["collateral_sell", "order_stock"],
        }
        names = mapping.get(action)
        if not names:
            raise ValueError("unsupported credit action")
        for name in names:
            if hasattr(self._trader, name):
                method = getattr(self._trader, name)
                try:
                    result = method(self._account, **payload)
                except TypeError:
                    try:
                        result = method(self._account, *payload.get("args", []))
                    except TypeError:
                        continue
                return {"action": action, "result": _json_safe(result)}
        raise QmtGatewayError(f"QMT trader does not support credit action={action}")

    def debug_trader(self) -> Dict[str, Any]:
        if self.config.mock_mode:
            return {"mock_mode": True}
        self.connect()
        names = [
            "query_account_infos",
            "query_account_status",
            "query_stock_asset",
            "query_stock_order",
            "query_stock_orders",
            "query_stock_trades",
            "query_stock_position",
            "query_stock_positions",
            "order_stock",
            "cancel_order_stock",
            "cancel_order_stock_sysid",
            "query_new_purchase_limit",
            "query_ipo_data",
            "query_position_statistics",
            "query_credit_detail",
            "query_stk_compacts",
            "query_credit_subjects",
            "query_credit_slo_code",
            "query_credit_assure",
            "purchase_new_stock",
            "query_fund_flow",
            "query_deliver_order",
            "credit_buy",
            "credit_sell",
            "repay_credit",
            "cash_repay",
            "stock_repay",
            "repay_stock",
            "direct_repay",
            "collateral_buy",
            "collateral_sell",
        ]
        return {"connected": self.connected, "functions": {name: hasattr(self._trader, name) for name in names}}

    def raw_call(self, method_name: str, args: List[Any], kwargs: Dict[str, Any]) -> Dict[str, Any]:
        if not self.config.allow_raw_trader_calls:
            raise QmtGatewayError("raw trader calls are disabled")
        allowed = set(self.config.allowed_raw_trader_methods)
        if allowed and method_name not in allowed:
            raise QmtGatewayError("raw trader method is not allowed")
        if method_name.startswith("_") or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", method_name):
            raise ValueError("invalid method name")
        self.connect()
        if not hasattr(self._trader, method_name):
            raise QmtGatewayError(f"QMT trader method not found: {method_name}")
        method = getattr(self._trader, method_name)
        call_args = [self._account] if kwargs.pop("with_account", True) else []
        call_args.extend(args or [])
        result = method(*call_args, **(kwargs or {}))
        return {"method": method_name, "result": _records_from_value(result)}

    def events(self, after: int = 0, limit: int = 200) -> Dict[str, Any]:
        limit = max(1, min(int(limit or 200), 1000))
        return {
            "last_seq": self._event_seq,
            "events": [event for event in self._events if int(event.get("seq", 0)) > after][:limit],
        }

    def poll_events(self, after: int = 0, limit: int = 200) -> Dict[str, Any]:
        for name, func in [
            ("account", self.query_account),
            ("positions", self.query_positions),
            ("orders", self.query_orders),
            ("trades", self.query_trades),
        ]:
            try:
                self._record_event(f"snapshot.{name}", func())
            except Exception as exc:
                self._last_error = str(exc)
                self._record_event("error", {"source": name, "error": str(exc)})
        return self.events(after=after, limit=limit)

    def _validate_order(self, symbol: str, side: str, quantity: int, price: float) -> None:
        if side not in {"buy", "sell"}:
            raise ValueError("side must be buy or sell")
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        if price <= 0:
            raise ValueError("price must be positive")
        if self.config.enforce_trading_hours and not _now_in_trading_hours():
            raise ValueError("current time is outside A-share trading hours")
        if side == "buy" and self.config.lot_size and quantity % self.config.lot_size != 0:
            raise ValueError(f"buy quantity must be a multiple of {self.config.lot_size}")
        if quantity > self.config.max_order_quantity:
            raise ValueError("quantity exceeds max_order_quantity")
        if quantity * price > self.config.max_order_value:
            raise ValueError("order value exceeds max_order_value")
        allowed = {s.upper() for s in self.config.allowed_symbols}
        blocked = {s.upper() for s in self.config.blocked_symbols}
        if allowed and symbol not in allowed:
            raise ValueError("symbol is not in allowed_symbols")
        if symbol in blocked:
            raise ValueError("symbol is in blocked_symbols")
        elapsed = time.time() - self._last_order_at
        if elapsed < self.config.min_order_interval_seconds:
            raise ValueError("orders are submitted too frequently")

    def _qmt_order_type(self, side: str) -> int:
        if side == "buy":
            return getattr(self._xtconstant, "STOCK_BUY")
        return getattr(self._xtconstant, "STOCK_SELL")

    def _qmt_price_type(self, price_type: str) -> int:
        if isinstance(price_type, int):
            return price_type
        name = str(price_type or "limit").lower()
        if name in {"limit", "fix", "fixed"}:
            return getattr(self._xtconstant, "FIX_PRICE")
        if name in {"market", "latest"}:
            return getattr(self._xtconstant, "LATEST_PRICE")
        mapping = {
            "best5-cancel": ["MARKET_SH_CONVERT_5_CANCEL", "MARKET_SZ_CONVERT_5_CANCEL"],
            "best5-limit": ["MARKET_SH_CONVERT_5_LIMIT", "MARKET_SZ_CONVERT_5_LIMIT"],
            "counterparty-best": ["MARKET_PEER_PRICE_FIRST"],
            "own-best": ["MARKET_MINE_PRICE_FIRST"],
            "instant": ["MARKET_SZ_INSTBUSI_RESTCANCEL"],
            "full-or-cancel": ["MARKET_SZ_FULL_OR_CANCEL"],
        }
        for const_name in mapping.get(name, []):
            if hasattr(self._xtconstant, const_name):
                return getattr(self._xtconstant, const_name)
        raise ValueError("unsupported price_type")

    def _side_from_qmt(self, raw: Any) -> str:
        if self._xtconstant is not None:
            if raw == getattr(self._xtconstant, "STOCK_BUY", object()):
                return "buy"
            if raw == getattr(self._xtconstant, "STOCK_SELL", object()):
                return "sell"
        text = str(raw).lower()
        if "buy" in text or text in {"23", "48"}:
            return "buy"
        if "sell" in text or text in {"24", "49"}:
            return "sell"
        return text

    def _serialize_position(self, position: Any) -> Dict[str, Any]:
        return {
            "symbol": _pick(position, ["stock_code", "symbol"]),
            "quantity": _as_int(_pick(position, ["volume", "quantity"])),
            "available_quantity": _as_int(_pick(position, ["can_use_volume", "available_quantity"])),
            "avg_price": _as_float(_pick(position, ["open_price", "avg_price", "cost_price"])),
            "market_value": _as_float(_pick(position, ["market_value"])),
            "profit": _as_float(_pick(position, ["profit", "position_profit"])),
            "raw": _qmt_object_dict(position),
        }

    def _serialize_order(self, order: Any) -> Dict[str, Any]:
        return {
            "id": str(_pick(order, ["order_id", "entrust_no", "id"], "")),
            "sysid": str(_pick(order, ["order_sysid", "sysid", "entrust_sysid"], "")),
            "symbol": _pick(order, ["stock_code", "symbol"]),
            "side": self._side_from_qmt(_pick(order, ["order_type", "entrust_bs"])),
            "quantity": _as_int(_pick(order, ["order_volume", "volume", "quantity"])),
            "price": _as_float(_pick(order, ["price", "entrust_price"])),
            "traded_quantity": _as_int(_pick(order, ["traded_volume", "business_amount"])),
            "status": str(_pick(order, ["order_status", "status"], "")),
            "time": str(_pick(order, ["order_time", "entrust_time", "time"], "")),
            "message": str(_pick(order, ["status_msg", "error_msg", "message"], "")),
            "raw": _qmt_object_dict(order),
        }

    def _mark_query(self) -> None:
        self._last_query_at = datetime.now()

    def _remember_order(self, client_order_id: str, result: Dict[str, Any]) -> None:
        if not client_order_id:
            return
        self._idempotent_orders[client_order_id] = dict(result)
        if len(self._idempotent_orders) > 5000:
            oldest = next(iter(self._idempotent_orders))
            self._idempotent_orders.pop(oldest, None)

    def _record_event(self, event_type: str, payload: Any) -> None:
        self._event_seq += 1
        self._events.append(
            {
                "seq": self._event_seq,
                "type": event_type,
                "time": datetime.now().isoformat(),
                "payload": _json_safe(payload),
            }
        )
        if len(self._events) > 5000:
            self._events = self._events[-5000:]


def create_app(config: GatewayConfig) -> Flask:
    app = Flask(__name__)
    client = QmtClient(config)
    market = QmtMarketData(config)
    request_log: List[Dict[str, Any]] = []
    request_count = 0

    @app.before_request
    def authenticate():
        nonlocal request_count
        request_count += 1
        request.environ["qmt_gateway_started_at"] = time.time()
        if request.path == "/health":
            return None
        if config.require_api_token and not config.api_token:
            return jsonify({"error": "api_token is required by config.require_api_token"}), 500
        if not config.api_token:
            return None
        auth_header = request.headers.get("Authorization", "")
        bearer = auth_header.removeprefix("Bearer ").strip()
        token = request.headers.get("X-API-Token") or bearer
        if token != config.api_token:
            return jsonify({"error": "unauthorized"}), 401
        return None

    @app.after_request
    def record_request(response):
        started_at = request.environ.get("qmt_gateway_started_at", time.time())
        request_log.append(
            {
                "time": datetime.now().isoformat(),
                "method": request.method,
                "path": request.path,
                "status_code": response.status_code,
                "duration_ms": round((time.time() - started_at) * 1000, 3),
                "remote_addr": request.headers.get("X-Forwarded-For") or request.remote_addr,
            }
        )
        if len(request_log) > 2000:
            del request_log[: len(request_log) - 2000]
        return response

    @app.errorhandler(Exception)
    def handle_error(exc: Exception):
        LOGGER.exception("request failed")
        status = 400 if isinstance(exc, (ValueError, QmtGatewayError)) else 500
        return jsonify({"error": str(exc)}), status

    @app.get("/health")
    def health():
        return jsonify(client.health())

    @app.get("/version")
    def version():
        return jsonify({"name": "vnpy-qmt-gateway", "version": "2026.07.15", "time": datetime.now().isoformat()})

    @app.get("/status")
    def status():
        return jsonify({"gateway": client.status(), "requests": {"count": request_count, "recent": request_log[-20:]}})

    @app.get("/audit/requests")
    def audit_requests():
        limit = max(1, min(_as_int(request.args.get("limit"), 200), 2000))
        return jsonify({"requests": request_log[-limit:], "count": request_count})

    @app.get("/events")
    def events():
        return jsonify(client.events(after=_as_int(request.args.get("after"), 0), limit=_as_int(request.args.get("limit"), 200)))

    @app.post("/events/poll")
    def poll_events():
        data = request.get_json(silent=True) or {}
        after = _as_int(data.get("after", request.args.get("after")), 0)
        limit = _as_int(data.get("limit", request.args.get("limit")), 200)
        return jsonify(client.poll_events(after=after, limit=limit))

    @app.post("/connect")
    def connect():
        client.connect()
        return jsonify(client.health())

    @app.get("/account")
    def account():
        return jsonify({"account": client.query_account()})

    @app.get("/accounts")
    def accounts():
        return jsonify({"accounts": client.query_account_infos()})

    @app.get("/account/status")
    def account_status():
        return jsonify({"status": client.query_account_status()})

    @app.get("/positions")
    def positions():
        return jsonify({"positions": client.query_positions()})

    @app.get("/positions/<symbol>")
    def position(symbol: str):
        return jsonify({"position": client.query_position(symbol)})

    @app.get("/orders")
    def orders():
        cancelable_only = str(request.args.get("cancelable_only", "false")).lower() in {"1", "true", "yes"}
        return jsonify({"orders": client.query_orders(cancelable_only=cancelable_only)})

    @app.get("/orders/<order_id>")
    def order(order_id: str):
        return jsonify({"order": client.query_order(order_id)})

    @app.get("/trades")
    def trades():
        return jsonify({"trades": client.query_trades()})

    @app.post("/orders")
    def place_order():
        return jsonify(client.place_order(request.get_json(silent=True) or {}))

    @app.post("/orders/batch")
    def place_orders():
        data = request.get_json(silent=True) or {}
        orders_payload = data.get("orders") if isinstance(data, dict) else data
        return jsonify(client.place_orders(list(orders_payload or [])))

    @app.post("/orders/<order_id>/cancel")
    def cancel_order(order_id: str):
        return jsonify(client.cancel_order(order_id))

    @app.post("/orders/cancel")
    def cancel_orders():
        data = request.get_json(silent=True) or {}
        return jsonify(client.cancel_orders(list(data.get("orders") or [])))

    @app.post("/orders/cancel-all")
    def cancel_all_orders():
        data = request.get_json(silent=True) or {}
        return jsonify(client.cancel_all_orders(symbol=str(data.get("symbol") or ""), side=str(data.get("side") or "")))

    @app.post("/orders/cancel-by-sysid")
    def cancel_order_by_sysid():
        data = request.get_json(silent=True) or {}
        return jsonify(client.cancel_order_by_sysid(str(data.get("market") or ""), str(data.get("sysid") or "")))

    @app.get("/history/<kind>")
    def history(kind: str):
        kwargs = {k: v for k, v in request.args.items()}
        return jsonify({"kind": kind, "data": client.query_history(kind, **kwargs)})

    @app.get("/ipo/data")
    def ipo_data():
        return jsonify({"ipo": client.query_ipo_data()})

    @app.get("/ipo/purchase-limit")
    def ipo_purchase_limit():
        return jsonify({"limits": client.query_new_purchase_limit()})

    @app.post("/ipo/purchase")
    def ipo_purchase():
        return jsonify(client.purchase_ipo(request.get_json(silent=True) or {}))

    @app.get("/positions/statistics")
    def position_statistics():
        return jsonify({"statistics": client.query_position_statistics()})

    @app.get("/credit/<kind>")
    def credit(kind: str):
        return jsonify({"kind": kind, "data": client.query_credit(kind)})

    @app.post("/credit/actions/<action>")
    def credit_action(action: str):
        return jsonify(client.credit_action(action, request.get_json(silent=True) or {}))

    @app.get("/debug/trader")
    def debug_trader():
        return jsonify(client.debug_trader())

    @app.post("/debug/trader/call/<method_name>")
    def raw_trader_call(method_name: str):
        data = request.get_json(silent=True) or {}
        return jsonify(client.raw_call(method_name, list(data.get("args") or []), dict(data.get("kwargs") or {})))

    @app.get("/market/quote/<symbol>")
    def market_quote(symbol: str):
        return jsonify({"quote": market.quote(symbol)})

    @app.get("/market/quotes")
    def market_quotes_get():
        symbols_arg = request.args.get("symbols", "")
        symbols = [s.strip() for s in symbols_arg.split(",") if s.strip()]
        return jsonify(market.quotes(symbols))

    @app.post("/market/quotes")
    def market_quotes_post():
        data = request.get_json(silent=True) or {}
        symbols = data.get("symbols") or []
        if isinstance(symbols, str):
            symbols = [s.strip() for s in symbols.split(",") if s.strip()]
        return jsonify(market.quotes(symbols))

    @app.get("/market/fulltick")
    def market_fulltick():
        symbols_arg = request.args.get("symbols", "")
        symbols = [s.strip() for s in symbols_arg.split(",") if s.strip()]
        return jsonify({"ticks": market.fulltick(symbols)})

    @app.post("/market/subscribe")
    def market_subscribe():
        data = request.get_json(silent=True) or {}
        symbols = data.get("symbols") or []
        if isinstance(symbols, str):
            symbols = [s.strip() for s in symbols.split(",") if s.strip()]
        period = data.get("period", "1m")
        return jsonify(market.subscribe(symbols, period))

    @app.post("/market/unsubscribe")
    def market_unsubscribe():
        data = request.get_json(silent=True) or {}
        symbols = data.get("symbols") or []
        if isinstance(symbols, str):
            symbols = [s.strip() for s in symbols.split(",") if s.strip()]
        return jsonify(market.unsubscribe(symbols))

    @app.get("/market/bars/<symbol>")
    def market_bars(symbol: str):
        period = request.args.get("period", "1m")
        count = _as_int(request.args.get("count"), 240)
        adjust = request.args.get("adjust", "none")
        return jsonify({
            "symbol": _normalize_symbol(symbol),
            "period": _normalize_period(period),
            "adjust": _normalize_adjust_type(adjust),
            "bars": market.bars(symbol, period, count, adjust),
        })

    @app.get("/market/trading-dates")
    def market_trading_dates():
        market_code = request.args.get("market", "SH")
        count = _as_int(request.args.get("count"), 250)
        return jsonify({"market": market_code, "dates": market.trading_dates(market_code, count)})

    @app.get("/market/symbols")
    def market_symbols():
        sector = request.args.get("sector", "沪深A股")
        limit = _as_int(request.args.get("limit"), 10000)
        include_detail = str(request.args.get("detail", "true")).lower() not in {"0", "false", "no"}
        return jsonify({"source": "qmt", "sector": sector, "symbols": market.symbols(sector=sector, include_detail=include_detail, limit=limit)})

    @app.get("/market/sectors")
    def market_sectors():
        return jsonify({"source": "qmt", "sectors": market.sectors()})

    @app.get("/market/sector-stocks")
    def market_sector_stocks():
        sector = request.args.get("sector", "沪深A股")
        as_of = request.args.get("as_of", "")
        limit = _as_int(request.args.get("limit"), 10000)
        include_detail = str(request.args.get("detail", "false")).lower() not in {"0", "false", "no"}
        return jsonify({"source": "qmt", **market.sector_stocks(sector=sector, as_of=as_of, include_detail=include_detail, limit=limit)})

    @app.get("/market/instruments/<symbol>")
    def market_instrument_detail(symbol: str):
        complete = str(request.args.get("complete", "true")).lower() not in {"0", "false", "no"}
        return jsonify({"source": "qmt", "instrument": market.instrument_detail(symbol, complete=complete)})

    @app.get("/market/index-weight/<index_code>")
    def market_index_weight(index_code: str):
        return jsonify({"source": "qmt", **market.index_weight(index_code)})

    @app.post("/market/financial")
    def market_financial():
        data = request.get_json(silent=True) or {}
        symbols = data.get("symbols") or data.get("symbol") or []
        if isinstance(symbols, str):
            symbols = [s.strip() for s in symbols.split(",") if s.strip()]
        tables = data.get("tables") or data.get("table") or []
        if isinstance(tables, str):
            tables = [s.strip() for s in tables.split(",") if s.strip()]
        return jsonify({
            "source": "qmt",
            **market.financial_data(
                symbols,
                tables,
                start_time=data.get("start_time", ""),
                end_time=data.get("end_time", ""),
                report_type=data.get("report_type", "report_time"),
                sample_rows=_as_int(data.get("sample_rows"), 0),
            ),
        })

    @app.post("/market/download/financial")
    def market_download_financial():
        data = request.get_json(silent=True) or {}
        symbols = data.get("symbols") or data.get("symbol") or []
        if isinstance(symbols, str):
            symbols = [s.strip() for s in symbols.split(",") if s.strip()]
        tables = data.get("tables") or data.get("table") or []
        if isinstance(tables, str):
            tables = [s.strip() for s in tables.split(",") if s.strip()]
        return jsonify({"source": "qmt", **market.download_financial_data(symbols, tables, data.get("start_time", ""), data.get("end_time", ""))})

    @app.post("/market/download/sectors")
    def market_download_sectors():
        return jsonify({"source": "qmt", **market.download_sector_data()})

    @app.get("/market/debug/xtdata")
    def market_debug_xtdata():
        return jsonify(market.debug_xtdata())

    @app.post("/market/debug/call/<method_name>")
    def raw_market_call(method_name: str):
        data = request.get_json(silent=True) or {}
        return jsonify(market.raw_call(method_name, list(data.get("args") or []), dict(data.get("kwargs") or {})))

    @app.get("/market/debug/bars/<symbol>")
    def market_debug_bars(symbol: str):
        period = request.args.get("period", "1m")
        count = _as_int(request.args.get("count"), 5)
        adjust = request.args.get("adjust", "none")
        return jsonify(market.debug_bars(symbol, period, count, adjust))

    @app.get("/market/self-test")
    def market_self_test():
        symbol = request.args.get("symbol", "600000.SH")
        return jsonify(market.self_test(symbol))

    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config_path = Path(os.environ.get("QMT_GATEWAY_CONFIG", "config.json"))
    if not config_path.exists():
        raise SystemExit(f"config file not found: {config_path}")
    config = GatewayConfig.from_file(config_path)
    LOGGER.info("starting QMT gateway on %s:%s, dry_run=%s", config.host, config.port, config.dry_run)
    create_app(config).run(host=config.host, port=config.port, threaded=True)


if __name__ == "__main__":
    main()
