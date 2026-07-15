"""Research package public exports with lazy imports."""

from __future__ import annotations

from importlib import import_module
from typing import Any


_EXPORTS: dict[str, tuple[str, str]] = {
    "DataQualityReport": ("vnpy.alpha.research.data_check", "DataQualityReport"),
    "REQUIRED_PRICE_COLUMNS": ("vnpy.alpha.research.data_check", "REQUIRED_PRICE_COLUMNS"),
    "check_price_data": ("vnpy.alpha.research.data_check", "check_price_data"),
    "validate_price_data": ("vnpy.alpha.research.data_check", "validate_price_data"),
    "ResearchValidationResult": ("vnpy.alpha.research.contracts", "ResearchValidationResult"),
    "validate_research_frame": ("vnpy.alpha.research.contracts", "validate_research_frame"),
    "validate_turtle_price_frame": ("vnpy.alpha.research.contracts", "validate_turtle_price_frame"),
    "validate_turtle_signal_frame": ("vnpy.alpha.research.contracts", "validate_turtle_signal_frame"),
    "RunSummary": ("vnpy.alpha.research.run_summary", "RunSummary"),
    "add_turtle_indicators": ("vnpy.alpha.research.turtle_indicators", "add_turtle_indicators"),
    "calculate_atr": ("vnpy.alpha.research.turtle_indicators", "calculate_atr"),
    "calculate_donchian_channels": ("vnpy.alpha.research.turtle_indicators", "calculate_donchian_channels"),
    "generate_turtle_signals": ("vnpy.alpha.research.turtle_signals", "generate_turtle_signals"),
    "run_long_only_backtest": ("vnpy.alpha.research.turtle_backtest", "run_long_only_backtest"),
    "PerformanceMetrics": ("vnpy.alpha.research.turtle_metrics", "PerformanceMetrics"),
    "calculate_performance_metrics": ("vnpy.alpha.research.turtle_metrics", "calculate_performance_metrics"),
    "save_equity_chart": ("vnpy.alpha.research.turtle_plots", "save_equity_chart"),
    "save_drawdown_chart": ("vnpy.alpha.research.turtle_plots", "save_drawdown_chart"),
    "save_signal_chart": ("vnpy.alpha.research.turtle_plots", "save_signal_chart"),
    "save_turtle_report": ("vnpy.alpha.research.turtle_report", "save_turtle_report"),
    "load_price_data": ("vnpy.alpha.research.turtle_data", "load_price_data"),
    "normalize_price_data": ("vnpy.alpha.research.turtle_data", "normalize_price_data"),
    "summarize_price_data": ("vnpy.alpha.research.turtle_data", "summarize_price_data"),
    "TurtleExperimentConfig": ("vnpy.alpha.research.turtle_experiment", "TurtleExperimentConfig"),
    "TurtlePipelineResult": ("vnpy.alpha.research.turtle_pipeline", "TurtlePipelineResult"),
    "run_turtle_pipeline": ("vnpy.alpha.research.turtle_pipeline", "run_turtle_pipeline"),
    "run_turtle_pipeline_result": ("vnpy.alpha.research.turtle_pipeline", "run_turtle_pipeline_result"),
    "UniverseFilterConfig": ("vnpy.alpha.research.turtle_universe", "UniverseFilterConfig"),
    "apply_basic_universe_filters": ("vnpy.alpha.research.turtle_universe", "apply_basic_universe_filters"),
    "build_sample_universe": ("vnpy.alpha.research.turtle_universe", "build_sample_universe"),
    "QmtGatewayConfig": ("vnpy.alpha.research.qmt_gateway_data", "QmtGatewayConfig"),
    "check_qmt_gateway_health": ("vnpy.alpha.research.qmt_gateway_data", "check_qmt_gateway_health"),
    "download_qmt_daily_bars": ("vnpy.alpha.research.qmt_gateway_data", "download_qmt_daily_bars"),
    "fetch_qmt_daily_bars": ("vnpy.alpha.research.qmt_gateway_data", "fetch_qmt_daily_bars"),
    "fetch_qmt_symbols": ("vnpy.alpha.research.qmt_gateway_data", "fetch_qmt_symbols"),
    "DEFAULT_ALIYUN_PUBLIC_IP": ("vnpy.alpha.research.qmt_gateway_data", "DEFAULT_ALIYUN_PUBLIC_IP"),
    "DEFAULT_ALIYUN_USERNAME": ("vnpy.alpha.research.qmt_gateway_data", "DEFAULT_ALIYUN_USERNAME"),
    "DEFAULT_ALIYUN_PASSWORD": ("vnpy.alpha.research.qmt_gateway_data", "DEFAULT_ALIYUN_PASSWORD"),
    "DEFAULT_QMT_GATEWAY_URL": ("vnpy.alpha.research.qmt_gateway_data", "DEFAULT_QMT_GATEWAY_URL"),
    "DEFAULT_QMT_GATEWAY_TOKEN": ("vnpy.alpha.research.qmt_gateway_data", "DEFAULT_QMT_GATEWAY_TOKEN"),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = _EXPORTS[name]
    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value
