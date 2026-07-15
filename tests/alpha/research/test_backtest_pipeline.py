"""流水线测试：数据加载校验、下载脚本离线逻辑、端到端不变式、参数对照。

端到端部分对照 contracts/artifacts-schema.md 的八条不变式逐条断言。
"""

import importlib.util
import json
from datetime import date
from pathlib import Path

import polars as pl
import pytest

from vnpy.alpha.research.mainline_backtest.config import BacktestConfig, load_config_from_json
from vnpy.alpha.research.mainline_backtest.data_loader import (
    load_bars,
    load_selection,
    next_trading_day,
    verify_adjusted_bars,
)
from vnpy.alpha.research.mainline_backtest.pipeline import run_mainline_backtest
from backtest_fixtures import (
    make_business_dates,
    make_benchmark,
    make_dual_bars,
    make_execution_universe,
    make_flat_dual_bars,
    make_selection,
    write_backtest_lake,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


def load_download_module():
    """把 scripts/download_adjusted_bars.py 当模块加载（脚本不在包里）。"""
    spec = importlib.util.spec_from_file_location(
        "download_adjusted_bars", REPO_ROOT / "scripts" / "download_adjusted_bars.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------- T006: 数据加载校验 ----------

def test_selection_missing_column_raises(tmp_path) -> None:
    """selection 缺少契约列（如 industry_rank）直接报错。"""
    bad = pl.DataFrame({"rebalance_date": [date(2025, 1, 31)], "vt_symbol": ["A.SZ"]})
    path = tmp_path / "selection.parquet"
    bad.write_parquet(path)

    with pytest.raises(ValueError, match="缺少必需列"):
        load_selection(BacktestConfig(selection_path=str(path)))


def test_missing_adjusted_bars_hints_download(tmp_path) -> None:
    """后复权文件缺失时，报错信息提示先运行下载脚本。"""
    with pytest.raises(FileNotFoundError, match="download_adjusted_bars"):
        load_bars(BacktestConfig(data_dir=str(tmp_path)), adjusted=True)


def test_next_trading_day_skips_to_next_month() -> None:
    """月末信号 → 次月首个交易日执行（跨周末）。"""
    trading_days = [date(2025, 1, 30), date(2025, 1, 31), date(2025, 2, 3)]
    assert next_trading_day(trading_days, date(2025, 1, 31)) == date(2025, 2, 3)
    assert next_trading_day(trading_days, date(2025, 2, 3)) is None  # 日历走到头


def test_verify_adjusted_bars_pass_and_fail_paths() -> None:
    """三项一致性校验：好数据通过；坏成交额 / 覆盖率不足分别不通过。"""
    dates = make_business_dates(date(2025, 1, 1), 10)
    adj, raw = make_dual_bars(
        "A.SZ", dates, [10.0] * 10, [1.0] * 5 + [2.0] * 5  # 第 6 天除权
    )
    good = verify_adjusted_bars(adj, raw)
    assert good["passed"] and good["coverage"] == 1.0 and good["ratio_ok"]

    bad_turnover = verify_adjusted_bars(adj.with_columns(pl.col("turnover") * 2), raw)
    assert not bad_turnover["passed"] and not bad_turnover["turnover_ok"]

    low_coverage = verify_adjusted_bars(adj.head(5), raw)  # 只覆盖一半
    assert not low_coverage["passed"] and not low_coverage["coverage_ok"]


# ---------- T008: 下载脚本离线测试 ----------

def test_download_merge_shards_and_resume(tmp_path, monkeypatch) -> None:
    """分片合并按 (股票, 日期) 排序；resume 模式跳过已存在的分片。"""
    module = load_download_module()
    dates = make_business_dates(date(2025, 1, 1), 3)
    shard_dir = tmp_path / "shards"
    shard_dir.mkdir()

    adj_b, _ = make_dual_bars("B.SZ", dates, [20.0, 21.0, 22.0])
    adj_a, _ = make_dual_bars("A.SZ", dates, [10.0, 11.0, 12.0])
    adj_b.write_parquet(shard_dir / "shard_0000.parquet")
    adj_a.write_parquet(shard_dir / "shard_0001.parquet")

    merged, dropped = module.merge_shards(shard_dir)
    assert merged.height == 6 and dropped == []
    assert merged["vt_symbol"].to_list()[:3] == ["A.SZ"] * 3  # 排序后 A 在前

    # 全 0 价格的股票（复权因子缺失）应被整只剔除并记入失败清单
    bad_adj, _ = make_dual_bars("ZERO.SZ", dates, [0.0, 0.0, 0.0])
    bad_adj.write_parquet(shard_dir / "shard_0002.parquet")
    merged_two, dropped_two = module.merge_shards(shard_dir)
    assert merged_two.height == 6
    assert dropped_two == ["ZERO.SZ: invalid_zero_price"]

    # resume: 两个分片都已存在 → fetch 一次都不该被调用
    def fetch_must_not_be_called(*args, **kwargs):
        raise AssertionError("resume 模式不应重新下载已完成的批次")

    monkeypatch.setattr(module, "fetch_one_with_retry", fetch_must_not_be_called)
    failed = module.download_batches(
        config=None, symbols=["A.SZ", "B.SZ"], shard_dir=shard_dir,
        batch_size=1, count=100, resume=True,
    )
    assert failed == []


def test_download_verification_failure_blocks_final_write(tmp_path, monkeypatch) -> None:
    """交叉校验不通过 → 退出码 3，最终 parquet 不落盘。"""
    module = load_download_module()
    dates = make_business_dates(date(2025, 1, 1), 5)
    adj, raw = make_dual_bars("A.SZ", dates, [10.0] * 5)

    symbols_path = tmp_path / "symbols.parquet"
    pl.DataFrame({"vt_symbol": ["A.SZ"]}).write_parquet(symbols_path)
    unadjusted_path = tmp_path / "unadjusted.parquet"
    raw.write_parquet(unadjusted_path)
    output_path = tmp_path / "adjusted_final.parquet"

    monkeypatch.setattr(module, "check_qmt_gateway_health", lambda config: {"ok": True})
    # 网关返回的"后复权"数据成交额被翻倍 → 与未复权数据对不上，校验必须失败
    monkeypatch.setattr(
        module, "fetch_one_with_retry",
        lambda config, symbol, count: adj.with_columns(pl.col("turnover") * 2),
    )
    monkeypatch.setattr(module.QmtGatewayConfig, "from_env", classmethod(
        lambda cls: cls(base_url="http://fake:1", token="", timeout=1.0)
    ))
    monkeypatch.chdir(tmp_path)  # 失败清单/校验报告写进临时目录
    monkeypatch.setattr(
        "sys.argv",
        ["prog", "--output-path", str(output_path), "--shard-dir", str(tmp_path / "shards"),
         "--symbols-path", str(symbols_path), "--unadjusted-path", str(unadjusted_path)],
    )

    assert module.main() == 3
    assert not output_path.exists()


# ---------- T017/T022: 端到端 ----------

def build_synthetic_lake(tmp_path: Path) -> tuple[BacktestConfig, list[date]]:
    """两个调仓期的合成市场（走势可手算）:

    交易日: 2025-01-01 起 40 个工作日。
    信号日: dates[9]（第一期）、dates[24]（第二期）→ 执行日 dates[10]、dates[25]。
    个股:
        FLAT.SZ  恒定 10 元（两期都入选）
        UP.SZ    每天涨 1%（第一期入选）
        SUSP.SZ  第一期入选后，从 dates[25] 起停牌 2 天（第二期卖出被顺延）
        GONE.SZ  第二期入选，但执行日 dates[25] 停牌（买入放弃）
    """
    dates = make_business_dates(date(2025, 1, 1), 40)

    flat_adj, flat_raw = make_flat_dual_bars("FLAT.SZ", dates, 10.0)
    up_closes = [10.0 * 1.01**i for i in range(40)]
    up_adj, up_raw = make_dual_bars("UP.SZ", dates, up_closes)

    susp_dates = dates[:25] + dates[27:]                     # dates[25]、[26] 停牌
    susp_adj, susp_raw = make_dual_bars("SUSP.SZ", susp_dates, [20.0] * len(susp_dates))

    gone_dates = dates[:25] + dates[26:]                     # 仅执行日 dates[25] 停牌
    gone_adj, gone_raw = make_dual_bars("GONE.SZ", gone_dates, [30.0] * len(gone_dates))

    adjusted = pl.concat([flat_adj, up_adj, susp_adj, gone_adj])
    unadjusted = pl.concat([flat_raw, up_raw, susp_raw, gone_raw])

    selection = make_selection(
        [
            (dates[9], "FLAT.SZ", "GICS1A"), (dates[9], "UP.SZ", "GICS1A"),
            (dates[9], "SUSP.SZ", "GICS1B"),
            (dates[24], "FLAT.SZ", "GICS1A"), (dates[24], "GONE.SZ", "GICS1B"),
        ]
    )
    symbols = ["FLAT.SZ", "UP.SZ", "SUSP.SZ", "GONE.SZ"]
    universe = make_execution_universe(dates, symbols)
    benchmark = make_benchmark(dates, [3000.0 * 1.002**i for i in range(40)])

    data_dir = tmp_path / "data"
    selection_path = write_backtest_lake(
        data_dir, adjusted, unadjusted, selection, universe, benchmark, dates
    )
    config = BacktestConfig(
        selection_path=str(selection_path),
        data_dir=str(data_dir),
        output_dir=str(tmp_path / "outputs"),
        name="e2e",
    )
    return config, dates


def test_end_to_end_invariants(tmp_path) -> None:
    """八条不变式逐条断言（artifacts-schema.md）。"""
    config, dates = build_synthetic_lake(tmp_path)
    result = run_mainline_backtest(config)
    nav = result["nav"]
    trades = result["trades"]
    positions = result["positions"]

    # 产物六件套齐备
    for filename in ["config.json", "nav.parquet", "positions.parquet",
                     "trades.parquet", "metrics.json", "report.md"]:
        assert (result["output_dir"] / filename).exists(), filename

    # 1. 记账恒等: nav × capital = cash + position_value
    identity = nav.with_columns(
        ((pl.col("nav") * config.initial_capital - pl.col("cash") - pl.col("position_value")).abs())
        .alias("gap")
    )
    assert identity.get_column("gap").max() < 1e-6 * config.initial_capital

    # 2. 状态枚举封闭；abandoned 全 null；deferred defer_days ≥ 1
    assert set(trades["status"].unique().to_list()) <= {"filled", "deferred", "abandoned", "forced"}
    abandoned = trades.filter(pl.col("status") == "abandoned")
    assert abandoned.height >= 1                            # GONE.SZ 执行日停牌
    for column in ["exec_price_raw", "shares", "gross_amount", "commission"]:
        assert abandoned.get_column(column).null_count() == abandoned.height
    deferred = trades.filter(pl.col("status") == "deferred")
    assert deferred.height >= 1                             # SUSP.SZ 卖出被顺延
    assert deferred.get_column("defer_days").min() >= 1

    # 3. 无未来信息
    assert trades.filter(pl.col("planned_date") <= pl.col("signal_date")).height == 0
    executed = trades.filter(pl.col("executed_date").is_not_null())
    assert executed.filter(pl.col("executed_date") < pl.col("planned_date")).height == 0

    # 4. 等权偏差可归因: 第一期 3 只，目标 = 期初权益/3，偏差 ≤ 1 手股价 + 成本
    first_buys = trades.filter(
        (pl.col("signal_date") == dates[9]) & (pl.col("side") == "buy") & (pl.col("status") == "filled")
    )
    target = config.initial_capital / 3
    for row in first_buys.iter_rows(named=True):
        invested = row["gross_amount"] + row["commission"]
        lot_price = row["exec_price_raw"] * config.lot_size
        assert abs(invested - target) <= lot_price + row["commission"] + 1e-6

    # 5. 净值连续: 无 null、恒为正
    assert nav.get_column("nav").null_count() == 0
    assert nav.get_column("nav").min() > 0

    # 6. 影子净值 nav_gross ≥ nav
    assert nav.filter(pl.col("nav_gross") < pl.col("nav") - 1e-12).height == 0

    # 8. 固定排序
    assert trades.sort(["signal_date", "vt_symbol", "side"]).equals(trades)
    assert nav.sort("datetime").equals(nav)
    assert positions.sort(["datetime", "vt_symbol"]).equals(positions)

    # 附加：UP.SZ 在涨、基准也在涨 → 组合净值应高于 1（方向合理性）
    assert nav.get_column("nav")[-1] > 1.0


def test_reproducibility_byte_identical(tmp_path) -> None:
    """不变式 7: 同输入重跑，三个 parquet 逐字节一致。"""
    config, _ = build_synthetic_lake(tmp_path)
    run_one = run_mainline_backtest(config)
    run_two = run_mainline_backtest(config)

    for filename in ["nav.parquet", "positions.parquet", "trades.parquet"]:
        bytes_one = (run_one["output_dir"] / filename).read_bytes()
        bytes_two = (run_two["output_dir"] / filename).read_bytes()
        assert bytes_one == bytes_two, f"{filename} 两次运行不一致"


def test_zero_cost_and_config_overrides(tmp_path) -> None:
    """T022: 零成本时 nav == nav_gross；成本越高年化越低；未知配置字段报错。"""
    config, _ = build_synthetic_lake(tmp_path)

    from vnpy.alpha.research.mainline_backtest.config import zero_cost
    result_free = run_mainline_backtest(zero_cost(config))
    gap = result_free["nav"].filter(
        (pl.col("nav_gross") - pl.col("nav")).abs() > 1e-12
    )
    assert gap.height == 0                                   # 零成本: 两条净值重合

    result_default = run_mainline_backtest(config)
    from dataclasses import replace
    result_pricey = run_mainline_backtest(replace(config, slippage_rate=0.005))
    annual_free = result_free["metrics"]["annual_return"]
    annual_default = result_default["metrics"]["annual_return"]
    annual_pricey = result_pricey["metrics"]["annual_return"]
    assert annual_free >= annual_default >= annual_pricey    # 成本方向性

    # config-json 覆盖生效 + 未知字段报错
    override_path = tmp_path / "override.json"
    override_path.write_text(json.dumps({"slippage_rate": 0.003}), encoding="utf-8")
    loaded = load_config_from_json(override_path, base=config)
    assert loaded.slippage_rate == 0.003

    bad_path = tmp_path / "bad.json"
    bad_path.write_text(json.dumps({"slipage_rate": 0.003}), encoding="utf-8")  # 拼错
    with pytest.raises(ValueError, match="不认识的字段"):
        load_config_from_json(bad_path, base=config)
