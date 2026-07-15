"""风控层测试：规则单测 + 三条规则端到端 + 回归保护 + 四组对照实验。

对照 specs/003-mainline-risk-control/ 的 spec（FR-001~010）与
contracts/artifacts-schema.md 的不变式 9~13。

合成市场约定（build_risk_lake）：
    交易日   2025-01-01 起 85 个工作日，覆盖 1~4 月；
             真实月末: dates[22]=1/31, dates[42]=2/28, dates[63]=3/31。
    基准     前 36 天恒 3000 → 每天跌 100 到 1500 → 每天涨 100 回升。
             用 5 日均线判定: 2 月末执行日弱市、1 月末与 3 月末执行日强市。
    个股     FLAT.SZ 恒定 10 元（隔离验证风控逻辑，自身不贡献盈亏）。
"""

import importlib.util
import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import polars as pl
import pytest

from vnpy.alpha.research.mainline_backtest.config import BacktestConfig
from vnpy.alpha.research.mainline_backtest.pipeline import run_mainline_backtest
from vnpy.alpha.research.mainline_backtest.risk import (
    build_benchmark_ma,
    find_no_signal_month_ends,
    is_weak_market,
    scan_stop_loss,
)
from backtest_fixtures import (
    make_benchmark,
    make_business_dates,
    make_dual_bars,
    make_execution_universe,
    make_flat_dual_bars,
    make_selection,
    write_backtest_lake,
)

REPO_ROOT = Path(__file__).resolve().parents[3]

RISK_REASONS = {"timing_exit", "no_signal_exit", "stop_loss"}


def risk_benchmark_closes(count: int) -> list[float]:
    """三段式基准: 高位横盘 → 单边下跌（跌破均线）→ 单边回升（收复均线）。"""
    closes: list[float] = []
    for index in range(count):
        if index <= 35:
            closes.append(3000.0)
        elif index <= 50:
            closes.append(3000.0 - 100.0 * (index - 35))
        else:
            closes.append(1500.0 + 100.0 * (index - 50))
    return closes


def build_risk_lake(
    tmp_path: Path,
    selection_rows: list[tuple[date, str, str]],
    extra_bars: list[tuple[pl.DataFrame, pl.DataFrame]] | None = None,
    extra_symbols: list[str] | None = None,
    **config_overrides,
) -> tuple[BacktestConfig, list[date]]:
    """写一个 85 个交易日、基准三段式走势的合成数据湖。"""
    dates = make_business_dates(date(2025, 1, 1), 85)
    flat_adj, flat_raw = make_flat_dual_bars("FLAT.SZ", dates, 10.0)

    adjusted_frames = [flat_adj]
    unadjusted_frames = [flat_raw]
    for adj, raw in extra_bars or []:
        adjusted_frames.append(adj)
        unadjusted_frames.append(raw)

    symbols = ["FLAT.SZ"] + (extra_symbols or [])
    selection = make_selection(selection_rows)
    universe = make_execution_universe(dates, symbols)
    benchmark = make_benchmark(dates, risk_benchmark_closes(85))

    data_dir = tmp_path / "data"
    selection_path = write_backtest_lake(
        data_dir, pl.concat(adjusted_frames), pl.concat(unadjusted_frames),
        selection, universe, benchmark, dates,
    )
    config = BacktestConfig(
        selection_path=str(selection_path),
        data_dir=str(data_dir),
        output_dir=str(tmp_path / "outputs"),
        name="risk_e2e",
        **config_overrides,
    )
    return config, dates


def read_quality_logs(output_dir: Path) -> list[dict]:
    """读回 data_quality.json 便于断言风控事件。"""
    return json.loads((output_dir / "data_quality.json").read_text(encoding="utf-8"))


# ---------- T005: 规则函数单测 ----------

def test_build_benchmark_ma_hand_check() -> None:
    """滚动均线手算对照：窗口 3，前两行 null，第三行起是滑动平均。"""
    dates = make_business_dates(date(2025, 1, 1), 5)
    table = build_benchmark_ma(
        pl.DataFrame({"datetime": dates, "benchmark_close": [10.0, 10.0, 10.0, 4.0, 4.0]}),
        window=3,
    )
    ma = table.get_column("ma").to_list()
    assert ma[0] is None and ma[1] is None
    assert ma[2] == 10.0
    assert ma[3] == pytest.approx((10 + 10 + 4) / 3)
    assert ma[4] == pytest.approx((10 + 4 + 4) / 3)


def test_is_weak_market_uses_only_prior_day() -> None:
    """择时判定只看执行日之前的数据：当天暴跌不影响当天执行日的判定。"""
    dates = make_business_dates(date(2025, 1, 1), 5)
    table = build_benchmark_ma(
        pl.DataFrame({"datetime": dates, "benchmark_close": [10.0, 10.0, 10.0, 4.0, 4.0]}),
        window=3,
    )
    # 执行日 dates[3]（当天收盘 4，暴跌）：前一日 dates[2] 收盘 10 == 均线 10 → 不弱市
    verdict = table.pipe(is_weak_market, dates[3])
    assert not verdict["weak"] and not verdict["window_short"]
    # 执行日 dates[4]：前一日 dates[3] 收盘 4 < 均线 (10+10+4)/3=8 → 弱市
    verdict = is_weak_market(table, dates[4])
    assert verdict["weak"]
    assert verdict["benchmark_close"] == 4.0
    assert verdict["benchmark_ma"] == pytest.approx(8.0)


def test_is_weak_market_window_short_passes() -> None:
    """均线窗口不足（早期历史）→ window_short=True 且恒判"通过"。"""
    dates = make_business_dates(date(2025, 1, 1), 5)
    table = build_benchmark_ma(
        pl.DataFrame({"datetime": dates, "benchmark_close": [1.0, 1.0, 1.0, 1.0, 1.0]}),
        window=3,
    )
    verdict = is_weak_market(table, dates[1])   # 前面只有 1 天历史
    assert verdict["window_short"] and not verdict["weak"]


def test_find_no_signal_month_ends() -> None:
    """无信号月末：有信号的月剔除、日历末尾不完整月剔除、首信号之前剔除。"""
    dates = make_business_dates(date(2025, 1, 1), 63)   # 1~3 月，3 月只到 28 日
    jan_end = max(d for d in dates if d.month == 1)     # 2025-01-31
    feb_end = max(d for d in dates if d.month == 2)     # 2025-02-28

    only_jan = make_selection([(jan_end, "FLAT.SZ", "GICS1A")])
    assert find_no_signal_month_ends(only_jan, dates, dates[-1]) == [feb_end]

    both = make_selection([(jan_end, "A.SZ", "G"), (feb_end, "A.SZ", "G")])
    assert find_no_signal_month_ends(both, dates, dates[-1]) == []

    only_feb = make_selection([(feb_end, "A.SZ", "G")])  # 1 月在首信号之前，不算无信号月
    assert find_no_signal_month_ends(only_feb, dates, dates[-1]) == []


def test_scan_stop_loss_strictly_greater_than_threshold() -> None:
    """止损阈值是严格大于：恰好 -15% 不触发，再跌一分钱触发。"""
    config = BacktestConfig(stop_loss_rate=0.15, slippage_rate=0.001)
    entry_adj = 10.0 * (1 + config.slippage_rate)       # 买入执行价（后复权口径）
    position = {
        "vt_symbol": "A.SZ", "buy_open_adj": 10.0,
        "last_close_adj": entry_adj * 0.85,             # 回撤恰好 15%
    }
    assert scan_stop_loss({"A.SZ": position}, {}, config) == []

    position["last_close_adj"] = entry_adj * 0.85 - 0.01
    hits = scan_stop_loss({"A.SZ": position}, {}, config)
    assert len(hits) == 1 and hits[0]["vt_symbol"] == "A.SZ"
    assert hits[0]["drawdown"] < -0.15

    # 已挂在补卖队列里的持仓不重复触发
    assert scan_stop_loss({"A.SZ": position}, {"A.SZ": (date(2025, 1, 2), "stop_loss")}, config) == []


def test_config_validation_rejects_bad_risk_params() -> None:
    """非法风控参数在构建配置时直接报错。"""
    with pytest.raises(ValueError, match="stop_loss_rate"):
        BacktestConfig(stop_loss_rate=0.0)
    with pytest.raises(ValueError, match="timing_ma_window"):
        BacktestConfig(timing_ma_window=1)


# ---------- T006/T009: US1 大盘择时 ----------

def timing_selection(dates: list[date]) -> list[tuple[date, str, str]]:
    """三期信号：1 月末（强市）、2 月末（弱市）、3 月末（回升后强市）。"""
    return [
        (dates[22], "FLAT.SZ", "GICS1A"),
        (dates[42], "FLAT.SZ", "GICS1A"),
        (dates[63], "FLAT.SZ", "GICS1A"),
    ]


def test_timing_exit_clears_and_recovers(tmp_path) -> None:
    """弱市调仓日：清仓（reason=timing_exit）+ 不买入不 abandoned；回升后恢复建仓。"""
    config, dates = build_risk_lake(
        tmp_path, timing_selection(dates=make_business_dates(date(2025, 1, 1), 85)),
        timing_enabled=True, timing_ma_window=5,
    )
    result = run_mainline_backtest(config)
    trades = result["trades"]

    # 1 月信号（强市）正常买入
    jan_buys = trades.filter(
        (pl.col("signal_date") == dates[22]) & (pl.col("side") == "buy") & (pl.col("status") == "filled")
    )
    assert jan_buys.height == 1

    # 2 月信号（弱市）：卖出标注 timing_exit，且完全没有买入记录（连 abandoned 都没有）
    feb_sells = trades.filter((pl.col("signal_date") == dates[22]) & (pl.col("side") == "sell"))
    assert feb_sells.height == 1
    assert feb_sells.get_column("reason").to_list() == ["timing_exit"]
    assert feb_sells.get_column("executed_date").to_list() == [dates[43]]
    assert trades.filter((pl.col("signal_date") == dates[42]) & (pl.col("side") == "buy")).height == 0

    # 空仓期净值平直（全现金，没有任何持仓损益）
    nav = result["nav"]
    empty_navs = nav.filter(
        (pl.col("datetime") >= dates[43]) & (pl.col("datetime") < dates[64])
    ).get_column("nav").to_list()
    assert len(set(empty_navs)) == 1
    empty_positions = result["positions"].filter(
        (pl.col("datetime") >= dates[43]) & (pl.col("datetime") < dates[64])
    )
    assert empty_positions.height == 0

    # 3 月信号（基准收复均线）恢复正常建仓
    mar_buys = trades.filter(
        (pl.col("signal_date") == dates[63]) & (pl.col("side") == "buy") & (pl.col("status") == "filled")
    )
    assert mar_buys.height == 1

    # 质量日志有 timing_skip 事件
    logs = read_quality_logs(result["output_dir"])
    skips = [log for log in logs if log["type"] == "timing_skip"]
    assert len(skips) == 1 and skips[0]["signal_date"] == str(dates[42])


def test_timing_window_short_treated_as_pass(tmp_path) -> None:
    """基准历史不足均线窗口 → 视为通过（照常买入）并记 timing_window_short。"""
    dates = make_business_dates(date(2025, 1, 1), 85)
    config, _ = build_risk_lake(
        tmp_path, [(dates[22], "FLAT.SZ", "GICS1A")],
        timing_enabled=True, timing_ma_window=60,   # 1 月末执行日只有 23 天历史
    )
    result = run_mainline_backtest(config)
    buys = result["trades"].filter((pl.col("side") == "buy") & (pl.col("status") == "filled"))
    assert buys.height == 1
    logs = read_quality_logs(result["output_dir"])
    assert any(log["type"] == "timing_window_short" for log in logs)


def test_switches_off_regression(tmp_path) -> None:
    """回归保护（不变式 10）：默认配置下无任何风控痕迹，且逐字节可复现。"""
    dates = make_business_dates(date(2025, 1, 1), 85)
    config, _ = build_risk_lake(tmp_path, timing_selection(dates))
    run_one = run_mainline_backtest(config)
    run_two = run_mainline_backtest(config)

    reasons = set(run_one["trades"].get_column("reason").drop_nulls().to_list())
    assert reasons & RISK_REASONS == set()
    logs = read_quality_logs(run_one["output_dir"])
    assert all(
        log["type"] not in {"timing_skip", "timing_window_short", "no_signal_exit", "stop_loss_trigger"}
        for log in logs
    )
    for filename in ["nav.parquet", "positions.parquet", "trades.parquet"]:
        assert (run_one["output_dir"] / filename).read_bytes() == (run_two["output_dir"] / filename).read_bytes()

    # 默认配置持仓一路拿到最后（没有无信号清仓）
    last_day_positions = run_one["positions"].filter(pl.col("datetime") == dates[-1])
    assert last_day_positions.height == 1


# ---------- 007: 逐日择时清仓 ----------

def test_timing_daily_requires_timing() -> None:
    """逐日择时必须建立在择时开关之上，单独打开直接报错。"""
    with pytest.raises(ValueError, match="timing_daily_enabled"):
        BacktestConfig(timing_daily_enabled=True)


def test_timing_daily_exits_mid_period(tmp_path) -> None:
    """逐日择时：基准月中跌破均线 → 次一交易日清仓，不等月末调仓日。"""
    dates = make_business_dates(date(2025, 1, 1), 85)
    config, _ = build_risk_lake(
        tmp_path, timing_selection(dates),
        timing_enabled=True, timing_ma_window=5, timing_daily_enabled=True,
    )
    result = run_mainline_backtest(config)
    trades = result["trades"]

    # 基准从 dates[36] 开始下跌，dates[36] 收盘首次跌破 MA5 → dates[37] 开盘清仓。
    # 对照组（只在调仓日检查）要等到 dates[43] 才离场。
    exits = trades.filter((pl.col("side") == "sell") & (pl.col("reason") == "timing_exit"))
    assert exits.height == 1
    row = exits.row(0, named=True)
    assert row["executed_date"] == dates[37]
    assert row["signal_date"] == dates[22]           # 卖的是 1 月信号建的仓

    # 清仓日起到 3 月重新建仓前一路空仓
    empty_positions = result["positions"].filter(
        (pl.col("datetime") >= dates[37]) & (pl.col("datetime") < dates[64])
    )
    assert empty_positions.height == 0

    # 2 月末调仓日仍是弱市：只记 timing_skip，不产生第二笔 timing_exit
    logs = read_quality_logs(result["output_dir"])
    daily_exits = [log for log in logs if log["type"] == "timing_daily_exit"]
    assert len(daily_exits) == 1 and daily_exits[0]["datetime"] == str(dates[37])
    assert any(log["type"] == "timing_skip" for log in logs)

    # 3 月末基准收复均线 → 恢复建仓
    mar_buys = trades.filter(
        (pl.col("signal_date") == dates[63]) & (pl.col("side") == "buy") & (pl.col("status") == "filled")
    )
    assert mar_buys.height == 1


def test_timing_reentry_requires_timing_daily() -> None:
    """逐日再入场必须建立在逐日择时之上，单独打开直接报错。"""
    with pytest.raises(ValueError, match="timing_reentry_enabled"):
        BacktestConfig(timing_enabled=True, timing_reentry_enabled=True)


def test_timing_reentry_buys_back_mid_period(tmp_path) -> None:
    """逐日再入场：择时空仓后基准收复均线 → 次一交易日买回当期清单，不等月末。

    手算对照（基准 MA5）：dates[52] 收盘 1700 > MA5 1620，是回升段首个强市日
    → dates[53] 再入场，买回 2 月期（dates[42]）的清单。
    """
    dates = make_business_dates(date(2025, 1, 1), 85)
    config, _ = build_risk_lake(
        tmp_path, timing_selection(dates),
        timing_enabled=True, timing_ma_window=5,
        timing_daily_enabled=True, timing_reentry_enabled=True,
    )
    result = run_mainline_backtest(config)
    trades = result["trades"]
    logs = read_quality_logs(result["output_dir"])

    reentries = [log for log in logs if log["type"] == "timing_reentry"]
    assert len(reentries) == 1
    assert reentries[0]["datetime"] == str(dates[53])
    assert reentries[0]["signal_date"] == str(dates[42])   # 买回的是 2 月期清单

    reentry_buys = trades.filter(
        (pl.col("signal_date") == dates[42]) & (pl.col("side") == "buy") & (pl.col("status") == "filled")
    )
    assert reentry_buys.height == 1
    assert reentry_buys.row(0, named=True)["executed_date"] == dates[53]

    # 再入场当日起持仓非空，直到 3 月调仓换仓
    held = result["positions"].filter(
        (pl.col("datetime") >= dates[53]) & (pl.col("datetime") < dates[64])
    )
    held_days = held.get_column("datetime").n_unique()
    assert held_days == 64 - 53

    # 对照断言：清仓 dates[37] ~ 再入场前一日 dates[52] 空仓
    empty = result["positions"].filter(
        (pl.col("datetime") >= dates[37]) & (pl.col("datetime") < dates[53])
    )
    assert empty.height == 0


def test_timing_reentry_off_matches_previous_behavior(tmp_path) -> None:
    """回归保护：timing_reentry 关闭时与逐日择时版逐字节一致。"""
    dates = make_business_dates(date(2025, 1, 1), 85)
    config_a, _ = build_risk_lake(
        tmp_path / "a", timing_selection(dates),
        timing_enabled=True, timing_ma_window=5, timing_daily_enabled=True,
    )
    config_b, _ = build_risk_lake(
        tmp_path / "b", timing_selection(dates),
        timing_enabled=True, timing_ma_window=5, timing_daily_enabled=True,
        timing_reentry_enabled=False,
    )
    run_a = run_mainline_backtest(config_a)
    run_b = run_mainline_backtest(config_b)
    for filename in ["nav.parquet", "positions.parquet", "trades.parquet"]:
        assert (run_a["output_dir"] / filename).read_bytes() == (run_b["output_dir"] / filename).read_bytes()


def test_timing_daily_off_matches_previous_behavior(tmp_path) -> None:
    """回归保护：timing_daily 关闭时与原择时版逐字节一致。"""
    dates = make_business_dates(date(2025, 1, 1), 85)
    config_a, _ = build_risk_lake(
        tmp_path / "a", timing_selection(dates), timing_enabled=True, timing_ma_window=5,
    )
    config_b, _ = build_risk_lake(
        tmp_path / "b", timing_selection(dates),
        timing_enabled=True, timing_ma_window=5, timing_daily_enabled=False,
    )
    run_a = run_mainline_backtest(config_a)
    run_b = run_mainline_backtest(config_b)
    for filename in ["nav.parquet", "positions.parquet", "trades.parquet"]:
        assert (run_a["output_dir"] / filename).read_bytes() == (run_b["output_dir"] / filename).read_bytes()


# ---------- T010/T012: US2 无信号月清仓 ----------

def test_no_signal_exit_clears_next_trading_day(tmp_path) -> None:
    """2 月无信号 → 2 月末次一交易日清仓（reason=no_signal_exit），之后空仓。"""
    dates = make_business_dates(date(2025, 1, 1), 85)
    config, _ = build_risk_lake(
        tmp_path, [(dates[22], "FLAT.SZ", "GICS1A")],   # 只有 1 月有信号
        no_signal_exit_enabled=True,
    )
    result = run_mainline_backtest(config)
    trades = result["trades"]

    exits = trades.filter(pl.col("reason") == "no_signal_exit")
    assert exits.height == 1
    assert exits.get_column("executed_date").to_list() == [dates[43]]   # 2/28 次一交易日
    assert exits.get_column("status").to_list() == ["filled"]

    # 清仓后一路空仓（2 月、3 月都没有信号，但只清一次）
    after = result["positions"].filter(pl.col("datetime") >= dates[43])
    assert after.height == 0

    logs = read_quality_logs(result["output_dir"])
    exit_logs = [log for log in logs if log["type"] == "no_signal_exit"]
    assert len(exit_logs) == 1 and exit_logs[0]["month_end"] == str(dates[42])


def test_no_signal_exit_off_keeps_positions(tmp_path) -> None:
    """开关关闭（002 现状）：无信号月继续持有旧仓。"""
    dates = make_business_dates(date(2025, 1, 1), 85)
    config, _ = build_risk_lake(tmp_path, [(dates[22], "FLAT.SZ", "GICS1A")])
    result = run_mainline_backtest(config)
    assert result["trades"].filter(pl.col("reason") == "no_signal_exit").height == 0
    assert result["positions"].filter(pl.col("datetime") == dates[-1]).height == 1


# ---------- T013/T015: US3 个股止损 ----------

def make_drop_stock(
    dates: list[date], drop_index: int, suspended_indexes: list[int] | None = None
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """恒定 10 元、在 drop_index 当天收盘跳水到 8 元的股票（回撤约 20%）。"""
    closes = [10.0 if index < drop_index else 8.0 for index in range(len(dates))]
    keep = [index for index in range(len(dates)) if index not in (suspended_indexes or [])]
    kept_dates = [dates[index] for index in keep]
    kept_closes = [closes[index] for index in keep]
    return make_dual_bars("DROP.SZ", kept_dates, kept_closes)


def test_stop_loss_triggers_and_rebuys(tmp_path) -> None:
    """收盘跌破 15% → 次日开盘卖出（reason=stop_loss）；下期再入选照常买回。"""
    dates = make_business_dates(date(2025, 1, 1), 85)
    config, _ = build_risk_lake(
        tmp_path,
        [(dates[22], "DROP.SZ", "GICS1A"), (dates[42], "DROP.SZ", "GICS1A")],
        extra_bars=[make_drop_stock(dates, drop_index=31)],
        extra_symbols=["DROP.SZ"],
        stop_loss_enabled=True, stop_loss_rate=0.15,
    )
    result = run_mainline_backtest(config)
    trades = result["trades"]

    stop_sells = trades.filter(pl.col("reason") == "stop_loss")
    assert stop_sells.height == 1
    row = stop_sells.row(0, named=True)
    assert row["status"] == "filled"
    assert row["executed_date"] == dates[32]        # 触发日 dates[31] 的次一交易日
    assert row["planned_date"] == dates[32]

    # 触发事件可追溯：同股票、触发日早于执行日、回撤幅度确实超阈值
    logs = read_quality_logs(result["output_dir"])
    triggers = [log for log in logs if log["type"] == "stop_loss_trigger"]
    assert len(triggers) == 1
    assert triggers[0]["vt_symbol"] == "DROP.SZ"
    assert triggers[0]["datetime"] == str(dates[31])
    assert triggers[0]["drawdown"] < -0.15

    # 止损后不拉黑：2 月信号再入选，照常买回
    rebuys = trades.filter(
        (pl.col("signal_date") == dates[42]) & (pl.col("side") == "buy") & (pl.col("status") == "filled")
    )
    assert rebuys.height == 1


def test_stop_loss_deferred_when_suspended(tmp_path) -> None:
    """止损执行日停牌 → 顺延到下一个可交易日，status=deferred、reason 不变。"""
    dates = make_business_dates(date(2025, 1, 1), 85)
    config, _ = build_risk_lake(
        tmp_path,
        [(dates[22], "DROP.SZ", "GICS1A")],
        extra_bars=[make_drop_stock(dates, drop_index=31, suspended_indexes=[32])],
        extra_symbols=["DROP.SZ"],
        stop_loss_enabled=True, stop_loss_rate=0.15,
    )
    trades = run_mainline_backtest(config)["trades"]
    stop_sells = trades.filter(pl.col("reason") == "stop_loss")
    assert stop_sells.height == 1
    row = stop_sells.row(0, named=True)
    assert row["status"] == "deferred"
    assert row["executed_date"] == dates[33]
    assert row["defer_days"] >= 1


def test_stop_loss_collision_with_rebalance_no_duplicate(tmp_path) -> None:
    """止损执行日恰逢调仓执行日：该股只卖一次（止损单先行），无重复卖出。"""
    dates = make_business_dates(date(2025, 1, 1), 85)
    config, _ = build_risk_lake(
        tmp_path,
        [(dates[22], "DROP.SZ", "GICS1A"), (dates[42], "FLAT.SZ", "GICS1A")],
        extra_bars=[make_drop_stock(dates, drop_index=42)],   # 触发日 = 2 月末信号日
        extra_symbols=["DROP.SZ"],
        stop_loss_enabled=True, stop_loss_rate=0.15,
    )
    trades = run_mainline_backtest(config)["trades"]
    drop_sells = trades.filter((pl.col("vt_symbol") == "DROP.SZ") & (pl.col("side") == "sell"))
    assert drop_sells.height == 1                    # 只卖一次
    row = drop_sells.row(0, named=True)
    assert row["executed_date"] == dates[43]         # 调仓执行日当天
    assert row["reason"] == "stop_loss"


# ---------- T018: US4 四组对照实验 ----------

def load_experiments_module():
    """把 scripts/run_risk_experiments.py 当模块加载（脚本不在包里）。"""
    spec = importlib.util.spec_from_file_location(
        "run_risk_experiments", REPO_ROOT / "scripts" / "run_risk_experiments.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_risk_experiments_four_groups(tmp_path, monkeypatch) -> None:
    """一键四组：comparison.md 生成、每组开关快照正确、baseline 无风控痕迹。"""
    module = load_experiments_module()
    dates = make_business_dates(date(2025, 1, 1), 85)
    config, _ = build_risk_lake(tmp_path, timing_selection(dates))

    monkeypatch.setattr(
        "sys.argv",
        ["prog", "--selection", config.selection_path, "--data-dir", config.data_dir,
         "--output-dir", config.output_dir, "--name", "cmp"],
    )
    assert module.main() == 0

    output_root = Path(config.output_dir)
    comparison_files = list(output_root.glob("*_cmp/comparison.md"))
    assert len(comparison_files) == 1
    content = comparison_files[0].read_text(encoding="utf-8")
    for group in ["baseline", "timing", "timing_ns", "all_on"]:
        assert group in content
    assert "边际贡献" in content

    # 每组 config 快照的开关状态与组名一致
    expected = {
        "risk_baseline": (False, False, False),
        "risk_timing": (True, False, False),
        "risk_timing_ns": (True, True, False),
        "risk_all_on": (True, True, True),
    }
    for run_dir in output_root.iterdir():
        name = "_".join(run_dir.name.split("_")[2:])
        if name not in expected:
            continue
        snapshot = json.loads((run_dir / "config.json").read_text(encoding="utf-8"))["config"]
        timing, no_signal, stop_loss = expected[name]
        assert snapshot["timing_enabled"] is timing
        assert snapshot["no_signal_exit_enabled"] is no_signal
        assert snapshot["stop_loss_enabled"] is stop_loss

    # baseline 组无风控痕迹（不变式 10 端到端版）
    baseline_dir = next(d for d in output_root.iterdir() if d.name.endswith("risk_baseline"))
    baseline_trades = pl.read_parquet(baseline_dir / "trades.parquet")
    reasons = set(baseline_trades.get_column("reason").drop_nulls().to_list())
    assert reasons & RISK_REASONS == set()
