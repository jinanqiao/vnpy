"""004 主线新鲜度规则测试：月龄计数、stale 过滤、回归保护、对照实验脚本。"""

import importlib.util
import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import polars as pl
import pytest

from vnpy.alpha.research.mainline.config import MainlineConfig, load_config_from_json
from vnpy.alpha.research.mainline.industry import SNAPSHOT_COLUMNS, apply_freshness
from vnpy.alpha.research.mainline.pipeline import run_mainline_signals
from mainline_fixtures import (
    make_business_dates,
    make_execution_universe,
    make_industry_map,
    make_stock_bars,
    write_data_lake,
)

REPO_ROOT = Path(__file__).parents[3]


# ---------- 工具：手搓一张最小行业快照 ----------

def make_snapshot(rebalance_date: date, industries: dict[str, bool]) -> pl.DataFrame:
    """{"行业": 是否入选} -> 满足 SNAPSHOT_COLUMNS 结构的快照（其余列填占位值）。"""
    rows = []
    for industry, selected in sorted(industries.items()):
        rows.append(
            {
                "rebalance_date": rebalance_date,
                "industry": industry,
                "mom_60": 0.1, "mom_20": 0.05,
                "rank_60": 1, "rank_20": 1,
                "breadth_60": 0.8, "member_count": 5,
                "selected": selected,
                "industry_streak": None,
                "reject_reason": None if selected else "rank_gate",
            }
        )
    return pl.DataFrame(rows, schema=SNAPSHOT_COLUMNS)


# ---------- T002: 配置校验 ----------

def test_config_default_and_negative() -> None:
    assert MainlineConfig().max_industry_streak == 0
    with pytest.raises(ValueError, match="max_industry_streak"):
        MainlineConfig(max_industry_streak=-1)


def test_config_json_override(tmp_path: Path) -> None:
    path = tmp_path / "override.json"
    path.write_text('{"max_industry_streak": 1}', encoding="utf-8")
    assert load_config_from_json(path).max_industry_streak == 1


# ---------- T003: 月龄计数（不变量 I-14 / I-16） ----------

def test_streak_counts_and_resets() -> None:
    """连任 +1、断档重置为 1、无主线月清空、非主线行业月龄为 null。"""
    config = MainlineConfig()
    day = [date(2025, 1, 31), date(2025, 2, 28), date(2025, 3, 31), date(2025, 4, 30)]

    # 第 1 期：A、B 入选
    snap1, streak, stale = apply_freshness(make_snapshot(day[0], {"A": True, "B": True, "C": False}), {}, config)
    assert streak == {"A": 1, "B": 1}
    assert stale == []
    by_industry = {r["industry"]: r["industry_streak"] for r in snap1.iter_rows(named=True)}
    assert by_industry == {"A": 1, "B": 1, "C": None}

    # 第 2 期：A 连任、B 落选 -> A=2，B 被清出状态
    snap2, streak, _ = apply_freshness(make_snapshot(day[1], {"A": True, "B": False}), streak, config)
    assert streak == {"A": 2}
    assert snap2.filter(pl.col("industry") == "A").get_column("industry_streak")[0] == 2

    # 第 3 期：无主线月 -> 状态清空
    snap3, streak, _ = apply_freshness(make_snapshot(day[2], {"A": False, "B": False}), streak, config)
    assert streak == {}
    assert snap3.get_column("industry_streak").null_count() == snap3.height

    # 第 4 期：A 再入选 -> 重置为 1
    _, streak, _ = apply_freshness(make_snapshot(day[3], {"A": True}), streak, config)
    assert streak == {"A": 1}


def test_streak_invariant_selected_or_stale() -> None:
    """I-14: industry_streak 非空 ⇔ selected 或 reject_reason='stale'。"""
    config = MainlineConfig(max_industry_streak=1)
    snap, streak, _ = apply_freshness(make_snapshot(date(2025, 1, 31), {"A": True, "C": False}), {"A": 1}, config)
    for row in snap.iter_rows(named=True):
        has_streak = row["industry_streak"] is not None
        assert has_streak == (row["selected"] or row["reject_reason"] == "stale")
    assert streak == {"A": 2}


# ---------- T006: 回归保护（max=0） ----------

def test_max_zero_never_filters() -> None:
    """开关关闭：连任多少个月都不过滤，只标注月龄。"""
    config = MainlineConfig()  # max_industry_streak=0
    streak: dict[str, int] = {}
    for month, day in enumerate([date(2025, m, 28) for m in range(1, 6)], start=1):
        snap, streak, stale = apply_freshness(make_snapshot(day, {"A": True}), streak, config)
        assert stale == []
        row = snap.filter(pl.col("industry") == "A").row(0, named=True)
        assert row["selected"] is True
        assert row["industry_streak"] == month
        assert row["reject_reason"] is None


# ---------- T007: stale 过滤 ----------

def test_stale_filter_marks_and_keeps_streak() -> None:
    """max=1 时连任第 2 月被过滤：selected=False、reject='stale'、月龄保留。"""
    config = MainlineConfig(max_industry_streak=1)
    snap, streak, stale = apply_freshness(
        make_snapshot(date(2025, 2, 28), {"A": True, "B": True}), {"A": 1}, config
    )
    assert stale == [("A", 2)]
    row_a = snap.filter(pl.col("industry") == "A").row(0, named=True)
    assert row_a["selected"] is False
    assert row_a["reject_reason"] == "stale"
    assert row_a["industry_streak"] == 2
    row_b = snap.filter(pl.col("industry") == "B").row(0, named=True)
    assert row_b["selected"] is True and row_b["industry_streak"] == 1
    assert streak == {"A": 2, "B": 1}


def test_streak_keeps_counting_while_filtered() -> None:
    """月龄不受过滤影响：连任第 3 月仍是 streak=3，不因前月被过滤而重置。"""
    config = MainlineConfig(max_industry_streak=1)
    streak: dict[str, int] = {}
    seen: list[int] = []
    for day in [date(2025, 1, 31), date(2025, 2, 28), date(2025, 3, 31)]:
        snap, streak, _ = apply_freshness(make_snapshot(day, {"A": True}), streak, config)
        seen.append(snap.filter(pl.col("industry") == "A").get_column("industry_streak")[0])
    assert seen == [1, 2, 3]
    assert streak == {"A": 3}


# ---------- 端到端：合成数据湖 ----------

DATES = make_business_dates(date(2025, 1, 1), 60)

INDUSTRIES = {
    "GICS1STRONG": [("S1.SZ", 0.015), ("S2.SZ", 0.014), ("S3.SZ", 0.013)],
    "GICS1MID": [("M1.SZ", 0.006), ("M2.SZ", 0.005), ("M3.SZ", 0.004)],
    "GICS1WEAK": [("W1.SZ", -0.005), ("W2.SZ", -0.005), ("W3.SZ", -0.005)],
}

SMALL_OVERRIDES = dict(
    rebalance_freq="monthly",  # 本套手算对照按月频构造（60 个交易日 = 2 个完整月）
    industry_mode="select",    # 008 后默认变为 none，新鲜度规则只在行业层模式下有意义
    trend_filters_enabled=True,
    score_weights=(0.40, 0.35, 0.25),
    mom_window=8, confirm_window=4, breadth_window=8,
    min_listed_bars=20, nh_window=20, ma_windows=(3, 5, 8), ma_slope_lag=2,
    vol_short=3, vol_long=6, min_industry_members=2,
    industry_top_n=2, industry_rank_gate=2,
    stocks_per_industry_min=2, stocks_per_industry_max=3,
)


def build_lake(tmp_path: Path) -> MainlineConfig:
    """搭一个最小数据湖：STRONG/MID 每期都会入选，天然形成连任场景。"""
    all_bars = []
    members: dict[str, list[str]] = {}
    for industry, stocks in INDUSTRIES.items():
        members[industry] = [s for s, _ in stocks]
        for vt_symbol, daily_return in stocks:
            all_bars.append(make_stock_bars(vt_symbol, DATES, daily_return))
    bars = pl.concat(all_bars)
    sector_members = make_industry_map(members).rename({"industry": "sector"})
    symbols = [s for stocks in INDUSTRIES.values() for s, _ in stocks]
    universe = make_execution_universe(DATES, symbols)
    write_data_lake(tmp_path, bars, sector_members.select("sector", "vt_symbol"), universe, DATES)
    return replace(
        MainlineConfig(**SMALL_OVERRIDES),
        data_dir=str(tmp_path),
        output_dir=str(tmp_path / "outputs"),
    )


def test_e2e_fresh_only_filters_second_month(tmp_path: Path) -> None:
    """max=1 端到端：第 2 个调仓期主线全部连任 -> 空清单 + stale 记录 + 质量日志。"""
    config = replace(build_lake(tmp_path), max_industry_streak=1)
    result = run_mainline_signals(config)

    industry_signals = result["industry_signals"]
    selection = result["selection"]
    periods = industry_signals.get_column("rebalance_date").unique().sort().to_list()
    assert len(periods) >= 2

    # 第 1 期正常入选，selection 全部月龄 = 1
    assert selection.filter(pl.col("rebalance_date") == periods[0]).height > 0
    assert selection.get_column("industry_streak").max() == 1

    # 第 2 期 STRONG/MID 连任 -> 全部 stale，空清单
    second = industry_signals.filter(pl.col("rebalance_date") == periods[1])
    stale_rows = second.filter(pl.col("reject_reason") == "stale")
    assert stale_rows.get_column("industry").sort().to_list() == ["GICS1MID", "GICS1STRONG"]
    assert stale_rows.get_column("industry_streak").to_list() == [2, 2]
    assert second.filter(pl.col("selected")).height == 0
    assert selection.filter(pl.col("rebalance_date") == periods[1]).height == 0

    # 质量日志有 stale_industry_filtered 事件
    quality = json.loads((result["output_dir"] / "data_quality.json").read_text(encoding="utf-8"))
    stale_events = [e for e in quality if e["type"] == "stale_industry_filtered"]
    assert {e["industry"] for e in stale_events} >= {"GICS1STRONG", "GICS1MID"}

    # report.md 标注被过滤行业
    report = (result["output_dir"] / "report.md").read_text(encoding="utf-8")
    assert "(第2月)" in report


def test_e2e_regression_max_zero(tmp_path: Path) -> None:
    """max=0 端到端回归：无 stale、每期照常入选、月龄逐期递增可审计。"""
    config = build_lake(tmp_path)
    result = run_mainline_signals(config)

    industry_signals = result["industry_signals"]
    assert "stale" not in set(industry_signals.get_column("reject_reason").drop_nulls().to_list())

    periods = industry_signals.get_column("rebalance_date").unique().sort().to_list()
    for index, period in enumerate(periods, start=1):
        picked = industry_signals.filter((pl.col("rebalance_date") == period) & pl.col("selected"))
        assert picked.get_column("industry").sort().to_list() == ["GICS1MID", "GICS1STRONG"]
        assert picked.get_column("industry_streak").to_list() == [index, index]

    # selection 带月龄列且与行业快照一致
    selection = result["selection"]
    assert "industry_streak" in selection.columns
    joined = selection.join(
        industry_signals.select(["rebalance_date", "industry", pl.col("industry_streak").alias("expect")]),
        on=["rebalance_date", "industry"],
    )
    assert (joined.get_column("industry_streak") == joined.get_column("expect")).all()


# ---------- T010: CLI ----------

def load_signals_script():
    spec = importlib.util.spec_from_file_location(
        "run_mainline_signals", REPO_ROOT / "scripts" / "run_mainline_signals.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_max_industry_streak(tmp_path: Path) -> None:
    module = load_signals_script()
    args = module.parse_args(["--max-industry-streak", "1"])
    assert module.build_config(args).max_industry_streak == 1

    # 未传时保持默认 0
    args = module.parse_args([])
    assert module.build_config(args).max_industry_streak == 0

    # 负值在构建配置时报错 -> main 捕获后退出码 1
    args = module.parse_args(["--max-industry-streak", "-2"])
    with pytest.raises(ValueError, match="max_industry_streak"):
        module.build_config(args)


# ---------- T011: 对照实验脚本 ----------

def load_experiments_module():
    spec = importlib.util.spec_from_file_location(
        "run_freshness_experiments", REPO_ROOT / "scripts" / "run_freshness_experiments.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_freshness_experiments_end_to_end(tmp_path: Path, monkeypatch) -> None:
    """一键对照：两组信号 + 两组回测 + comparison.md（指标/差异/样本内声明）。"""
    module = load_experiments_module()
    config = build_lake(tmp_path)

    # 补齐回测需要的文件：后复权行情（与未复权相同）+ 基准指数
    bars = pl.read_parquet(Path(config.data_dir) / "normalized" / "daily_bars_all_a.parquet")
    bars.write_parquet(Path(config.data_dir) / "normalized" / "daily_bars_all_a_adjusted.parquet")
    (Path(config.data_dir) / "benchmark").mkdir()
    pl.DataFrame(
        {"datetime": DATES, "index_symbol": ["000300.SH"] * len(DATES), "close": [3000.0] * len(DATES)}
    ).write_parquet(Path(config.data_dir) / "benchmark" / "index_daily.parquet")

    # 小窗口参数通过 config-json 传给脚本
    overrides = {k: (list(v) if isinstance(v, tuple) else v) for k, v in SMALL_OVERRIDES.items()}
    config_json = tmp_path / "small.json"
    config_json.write_text(json.dumps(overrides), encoding="utf-8")

    signal_out = tmp_path / "sig_out"
    backtest_out = tmp_path / "bt_out"
    monkeypatch.setattr(
        "sys.argv",
        ["prog", "--data-dir", config.data_dir,
         "--signal-output-dir", str(signal_out),
         "--backtest-output-dir", str(backtest_out),
         "--signal-config-json", str(config_json), "--name", "cmp"],
    )
    assert module.main() == 0

    comparison_files = list(backtest_out.glob("*_cmp/comparison.md"))
    assert len(comparison_files) == 1
    content = comparison_files[0].read_text(encoding="utf-8")
    for keyword in ["fresh_baseline", "fresh_only", "差异", "样本内声明", "有信号月数"]:
        assert keyword in content

    # 两组信号目录确实生成，fresh 组 selection 全部月龄 = 1
    fresh_dirs = [d for d in signal_out.iterdir() if d.name.endswith("fresh_only")]
    assert len(fresh_dirs) == 1
    fresh_selection = pl.read_parquet(fresh_dirs[0] / "selection.parquet")
    if fresh_selection.height:
        assert fresh_selection.get_column("industry_streak").max() == 1
