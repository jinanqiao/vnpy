from typing import Any

from vnpy.alpha.research.qmt_gateway_data import QmtGatewayConfig


def test_qmt_gateway_config_uses_hardcoded_defaults() -> None:
    config = QmtGatewayConfig()

    assert config.base_url == "http://47.93.170.141:8710"
    assert config.token == "my-qmt-token-123456"


def test_qmt_gateway_config_from_env_falls_back_to_hardcoded_defaults(monkeypatch: Any) -> None:
    monkeypatch.delenv("QMT_GATEWAY_URL", raising=False)
    monkeypatch.delenv("QMT_GATEWAY_TOKEN", raising=False)

    config = QmtGatewayConfig.from_env()

    assert config.base_url == "http://47.93.170.141:8710"
    assert config.token == "my-qmt-token-123456"
