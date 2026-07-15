"""滚动样本外回测（T4）：把生产参数固定，用滚动窗口跑分段回测汇总。

思路：不重新寻优（不做训练），只用当前生产参数 + selection 分段跑，
每个"OOS 窗口"内的年化/回撤/Sharpe 就是"如果这段是样本外，实盘会怎样"的近似。
真正的样本外验证需要"前窗训练+后窗验证"，但当前生产参数是简单的固定权重，
不涉及拟合的参数搜索，因此"分段回测"就是对样本外表现的合理近似。

同时支持 A/B 对比：传两个 selection.parquet（如"生产反向权重" vs "T3 发现的翻正权重"），
在每个 OOS 窗口下并排比较，看方向翻转信号在最近 OOS 上是否已经该切换权重。

用法:
    python3 scripts/run_rolling_oos_backtest.py \
        --selection outputs/mainline/*_illiq0/selection.parquet \
        --window 12 --step 6 --name t4_oos_reverse

    # A/B 对比
    python3 scripts/run_rolling_oos_backtest.py \
        --selection outputs/mainline/*_illiq0/selection.parquet \
        --selection-b outputs/mainline/*_positive_weights/selection.parquet \
        --window 12 --step 6 --name t4_ab_direction

产物：outputs/mainline_backtest/rolling_oos_<name>/summary.md + 每个窗口独立子目录。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import polars as pl

BACKTEST_ARGS_DEFAULT = [
    "--timing", "--timing-daily", "--timing-reentry",
    "--no-signal-exit", "--stop-loss", "--delta-rebalance",
]


def load_period_ends(selection: Path) -> list:
    return (
        pl.read_parquet(selection, columns=["rebalance_date"])
        .get_column("rebalance_date").unique().sort().to_list()
    )


def build_windows(period_ends: list, window: int, step: int) -> list[tuple[int, int]]:
    """返回 [(start_idx, end_idx), ...]（含头含尾），滚动步长 step。"""
    out: list[tuple[int, int]] = []
    i = 0
    while i + window <= len(period_ends):
        out.append((i, i + window - 1))
        i += step
    # 最后一个窗口如果尾部还有余量但差 < step，追加一个"贴末端"的窗口
    if out and out[-1][1] != len(period_ends) - 1:
        out.append((len(period_ends) - window, len(period_ends) - 1))
    return out


def run_one_backtest(
    selection: Path,
    start: str,
    end: str,
    output_dir: Path,
    name: str,
    python: str,
    extra_args: list[str] | None = None,
) -> Path:
    cmd = [
        python, "scripts/run_mainline_backtest.py",
        "--selection", str(selection),
        "--start", start, "--end", end,
        "--output-dir", str(output_dir), "--name", name,
        *BACKTEST_ARGS_DEFAULT,
        *(extra_args or []),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"backtest 失败: {result.stderr[-500:]}")
    # 找到刚生成的目录（时间戳前缀 + name 后缀）
    dirs = sorted(output_dir.glob(f"*_{name}"), key=lambda p: p.stat().st_mtime)
    if not dirs:
        raise RuntimeError(f"未找到 backtest 输出目录: {name}")
    return dirs[-1]


def read_metrics(bt_dir: Path) -> dict:
    return json.loads((bt_dir / "metrics.json").read_text())


def summarize(metrics: dict) -> dict:
    return {
        "annual": metrics["annual_return"],
        "mdd": metrics["max_drawdown"],
        "sharpe": metrics["sharpe"],
        "gross_annual": metrics["gross"]["annual_return"],
        "turnover": metrics["annual_turnover"],
    }


def format_row(label: str, m: dict) -> str:
    return (
        f"| {label} | {m['annual']*100:+.2f}% | {m['mdd']*100:.2f}% | "
        f"{m['sharpe']:.3f} | {m['gross_annual']*100:+.2f}% | {m['turnover']:.2f}x |"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", required=True, help="A 组 selection.parquet")
    parser.add_argument("--selection-b", default="", help="（可选）B 组 selection，做 A/B 对比")
    parser.add_argument("--window", type=int, default=12, help="每个 OOS 窗口的期数（月频=月数）")
    parser.add_argument("--step", type=int, default=6, help="滚动步长")
    parser.add_argument("--name", default="rolling_oos", help="产物根目录名")
    parser.add_argument("--output-dir", default="outputs/mainline_backtest")
    parser.add_argument(
        "--python", default="/Library/Frameworks/Python.framework/Versions/3.13/bin/python3",
        help="调用的 python 解释器",
    )
    parser.add_argument(
        "--extra-args-a", default="",
        help="A 组回测追加参数，用空格分隔（如 '--portfolio-vol-target 0.10'）",
    )
    parser.add_argument(
        "--extra-args-b", default="",
        help="B 组回测追加参数，用空格分隔",
    )
    args = parser.parse_args()
    extra_a = args.extra_args_a.split() if args.extra_args_a else []
    extra_b = args.extra_args_b.split() if args.extra_args_b else []

    sel_a = Path(args.selection)
    if not sel_a.exists():
        print(f"[FATAL] selection 不存在: {sel_a}", file=sys.stderr)
        return 2
    period_ends = load_period_ends(sel_a)
    windows = build_windows(period_ends, args.window, args.step)
    print(f"[INFO] A 组 selection: {sel_a}")
    print(f"[INFO] 共 {len(period_ends)} 期，窗口={args.window}，步长={args.step} → {len(windows)} 个 OOS 窗口")

    sel_b = Path(args.selection_b) if args.selection_b else None
    if sel_b:
        pe_b = load_period_ends(sel_b)
        if pe_b != period_ends:
            print(f"[WARN] A/B 组期数或调仓日不完全一致，B 组期数={len(pe_b)}")
        print(f"[INFO] B 组 selection: {sel_b}")

    root = Path(args.output_dir) / f"rolling_oos_{args.name}"
    root.mkdir(parents=True, exist_ok=True)

    rows_a: list[str] = []
    rows_b: list[str] = []
    summary_a: list[dict] = []
    summary_b: list[dict] = []

    start_ts = time.time()
    for i, (lo, hi) in enumerate(windows, 1):
        start_str = period_ends[lo].isoformat()
        end_str = period_ends[hi].isoformat()
        print(f"[RUN] 窗口 {i}/{len(windows)}: {start_str} ~ {end_str}")

        bt_a = run_one_backtest(sel_a, start_str, end_str, root, f"a_win{i:02d}", args.python, extra_a)
        ma = summarize(read_metrics(bt_a))
        summary_a.append({"window": i, "start": start_str, "end": end_str, **ma})
        rows_a.append(format_row(f"{start_str} ~ {end_str}", ma))

        if sel_b:
            bt_b = run_one_backtest(sel_b, start_str, end_str, root, f"b_win{i:02d}", args.python, extra_b)
            mb = summarize(read_metrics(bt_b))
            summary_b.append({"window": i, "start": start_str, "end": end_str, **mb})
            rows_b.append(format_row(f"{start_str} ~ {end_str}", mb))

    lines = [
        f"# 滚动样本外回测报告：{args.name}",
        "",
        f"- 生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- A 组 selection: {sel_a}",
    ]
    if sel_b:
        lines.append(f"- B 组 selection: {sel_b}")
    lines.append(f"- 窗口={args.window} 期，步长={args.step} 期，共 {len(windows)} 个 OOS 窗口")
    lines.append(f"- 回测参数: {' '.join(BACKTEST_ARGS_DEFAULT)}")
    lines.append("")

    header = "| OOS 窗口 | 年化 | 回撤 | Sharpe | Gross年化 | 换手 |"
    sep = "|---|---|---|---|---|---|"

    lines += ["## A 组结果", "", header, sep, *rows_a, ""]
    if sel_b:
        lines += ["## B 组结果", "", header, sep, *rows_b, ""]
        # A/B 差额
        lines += ["## A/B 差额（B - A）", "", "| OOS 窗口 | Δ年化 | Δ回撤 | ΔSharpe |", "|---|---|---|---|"]
        for a, b in zip(summary_a, summary_b):
            lines.append(
                f"| {a['start']} ~ {a['end']} | "
                f"{(b['annual']-a['annual'])*100:+.2f}pp | "
                f"{(b['mdd']-a['mdd'])*100:+.2f}pp | "
                f"{b['sharpe']-a['sharpe']:+.3f} |"
            )
        lines.append("")

    # 全窗口聚合
    def agg(rows: list[dict], label: str) -> str:
        n = len(rows)
        avg_ar = sum(r["annual"] for r in rows) / n
        avg_mdd = sum(r["mdd"] for r in rows) / n
        avg_sharpe = sum(r["sharpe"] for r in rows) / n
        return f"- {label}: 年化均值 {avg_ar*100:+.2f}%，回撤均值 {avg_mdd*100:.2f}%，Sharpe 均值 {avg_sharpe:.3f}（{n} 窗口）"

    lines += ["## 聚合统计", ""]
    lines.append(agg(summary_a, "A 组"))
    if sel_b:
        lines.append(agg(summary_b, "B 组"))
    lines.append("")

    summary_path = root / "summary.md"
    summary_path.write_text("\n".join(lines), encoding="utf-8")

    (root / "summary.json").write_text(json.dumps({
        "a": summary_a,
        "b": summary_b if sel_b else None,
        "windows": [{"i": i + 1, "start": p[0].isoformat(), "end": p[-1].isoformat()}
                    for i, p in enumerate([[period_ends[lo], period_ends[hi]] for lo, hi in windows])],
    }, indent=2, default=str))

    print(f"\n[OK] 汇总已写入 {summary_path} (耗时 {(time.time()-start_ts)/60:.1f} 分)")
    print("\n".join(lines[-20:]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
