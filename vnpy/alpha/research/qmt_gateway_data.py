from dataclasses import dataclass
from pathlib import Path
from typing import Any
import os

import polars as pl
import requests


DEFAULT_ALIYUN_PUBLIC_IP: str = "8.141.119.179"
DEFAULT_ALIYUN_USERNAME: str = "Administrator"
DEFAULT_ALIYUN_PASSWORD: str = "xulandong123A?"
DEFAULT_QMT_GATEWAY_PORT: int = 8710
DEFAULT_QMT_GATEWAY_URL: str = f"http://{DEFAULT_ALIYUN_PUBLIC_IP}:{DEFAULT_QMT_GATEWAY_PORT}"
DEFAULT_QMT_GATEWAY_TOKEN: str = "my-qmt-token-123456"


@dataclass(frozen=True)
class QmtGatewayConfig:
    """Connection settings for the Windows-side QMT Gateway."""

    base_url: str = DEFAULT_QMT_GATEWAY_URL
    token: str = DEFAULT_QMT_GATEWAY_TOKEN
    timeout: float = 30.0

    @classmethod
    def from_env(cls) -> "QmtGatewayConfig":
        base_url = os.environ.get("QMT_GATEWAY_URL", DEFAULT_QMT_GATEWAY_URL).rstrip("/")
        token = os.environ.get("QMT_GATEWAY_TOKEN", DEFAULT_QMT_GATEWAY_TOKEN)
        return cls(base_url=base_url, token=token)


def check_qmt_gateway_health(config: QmtGatewayConfig) -> dict[str, Any]:
    """Check gateway health. This endpoint does not require a token."""
    return _request(config, "GET", "/health", auth=False)


def fetch_qmt_symbols(
    config: QmtGatewayConfig,
    sector: str = "沪深A股",
    limit: int = 5000,
    detail: bool = True,
) -> list[dict[str, Any]]:
    """Fetch stock universe from QMT Gateway."""
    response = _request(
        config,
        "GET",
        "/market/symbols",
        params={"sector": sector, "limit": int(limit), "detail": "true" if detail else "false"},
    )
    return list(response.get("symbols") or [])


def fetch_qmt_daily_bars(
    config: QmtGatewayConfig,
    symbol: str,
    count: int = 5000,
    adjust: str = "none",
) -> pl.DataFrame:
    """Fetch one symbol's daily bars and normalize them to the turtle data contract."""
    response = _request(
        config,
        "GET",
        f"/market/bars/{symbol}",
        params={"period": "1d", "count": int(count), "adjust": adjust},
    )
    bars = response.get("bars") or []
    if not bars:
        return _empty_bar_frame()

    return (
        pl.DataFrame(bars)
        .with_columns(
            pl.lit(symbol).alias("vt_symbol"),
            pl.col("time").str.to_datetime(strict=False).alias("datetime"),
            pl.col("amount").cast(pl.Float64, strict=False).alias("turnover"),
        )
        .select(["datetime", "vt_symbol", "open", "high", "low", "close", "volume", "turnover"])
        .with_columns(
            pl.col("open").cast(pl.Float64, strict=False),
            pl.col("high").cast(pl.Float64, strict=False),
            pl.col("low").cast(pl.Float64, strict=False),
            pl.col("close").cast(pl.Float64, strict=False),
            pl.col("volume").cast(pl.Float64, strict=False),
        )
        .sort(["vt_symbol", "datetime"])
    )


def download_qmt_daily_bars(
    config: QmtGatewayConfig,
    symbols: list[str],
    output_path: str | Path,
    count: int = 5000,
    adjust: str = "none",
) -> Path:
    """Download several symbols' daily bars and save one parquet file."""
    frames = [fetch_qmt_daily_bars(config, symbol, count=count, adjust=adjust) for symbol in symbols]
    frames = [frame for frame in frames if not frame.is_empty()]
    if not frames:
        raise ValueError("No bars returned from QMT Gateway.")

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.concat(frames, how="vertical").write_parquet(path)
    return path


def _request(config: QmtGatewayConfig, method: str, path: str, auth: bool = True, **kwargs: Any) -> dict[str, Any]:
    headers = kwargs.pop("headers", {})
    if auth and config.token:
        headers["X-API-Token"] = config.token

    response = requests.request(
        method,
        f"{config.base_url.rstrip('/')}{path}",
        headers=headers,
        timeout=config.timeout,
        **kwargs,
    )
    try:
        data = response.json()
    except ValueError:
        data = {"error": response.text}

    if not response.ok:
        raise RuntimeError(data.get("error") or f"QMT Gateway HTTP {response.status_code}")
    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError(str(data["error"]))
    return data if isinstance(data, dict) else {"data": data}


def _empty_bar_frame() -> pl.DataFrame:
    return pl.DataFrame(
        schema={
            "datetime": pl.Datetime,
            "vt_symbol": pl.String,
            "open": pl.Float64,
            "high": pl.Float64,
            "low": pl.Float64,
            "close": pl.Float64,
            "volume": pl.Float64,
            "turnover": pl.Float64,
        }
    )
