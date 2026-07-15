"""
交易面板股票代码/名称搜索下拉补丁。

数据源：data/raw/qmt/all_a_symbols_*.parquet（QMT 全 A 股票列表快照，
含 code / exchange(SH/SZ/BJ) / name），取文件名最新的一份。

做法与 ui_patches 相同：patch TradingWidget.__init__，在原构造逻辑
跑完后给「代码」「名称」两个输入框挂 QCompleter：
  - 补全条目显示为 "600000 浦发银行 (SSE)"，按包含匹配，
    输入代码或中文名称都能搜到；
  - 选中后自动填代码、切换交易所下拉框，并触发 set_vt_symbol
    完成行情订阅，名称框回填股票名。
"""
from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SYMBOLS_DIR = _REPO_ROOT / "data" / "raw" / "qmt"

# QMT 后缀 → vnpy Exchange.value
_SUFFIX_TO_EXCHANGE = {"SH": "SSE", "SZ": "SZSE", "BJ": "BSE"}


def _load_stock_list() -> list[tuple[str, str, str]]:
    """返回 [(code, exchange_value, name), ...]；无数据时返回空列表。"""
    files = sorted(_SYMBOLS_DIR.glob("all_a_symbols_*.parquet"))
    if not files:
        return []

    import polars as pl

    df = (
        pl.read_parquet(files[-1])
        .select(["code", "exchange", "name"])
        .unique(subset=["code", "exchange"])
        .sort("code")
    )

    stocks: list[tuple[str, str, str]] = []
    for code, suffix, name in df.iter_rows():
        exchange_value = _SUFFIX_TO_EXCHANGE.get(suffix)
        if exchange_value and code and name:
            stocks.append((code, exchange_value, str(name).strip()))
    return stocks


def _patch_set_vt_symbol(widget_module) -> None:
    """修正 set_vt_symbol：交易所下拉显示 "SSE (上交所)" 时，
    拼 vt_symbol 前先剥掉括号说明，避免生成 "600000.SSE (上交所)" 这类脏值。
    """
    from vnpy.trader.constant import Exchange
    from vnpy.trader.object import ContractData, SubscribeRequest
    from vnpy.trader.utility import get_digits

    def set_vt_symbol(self) -> None:
        symbol: str = str(self.symbol_line.text())
        if not symbol:
            return

        exchange_value: str = str(self.exchange_combo.currentText()).split(" ")[0]
        vt_symbol: str = f"{symbol}.{exchange_value}"

        if vt_symbol == self.vt_symbol:
            return
        self.vt_symbol = vt_symbol

        contract: ContractData | None = self.main_engine.get_contract(vt_symbol)
        if not contract:
            self.name_line.setText("")
            gateway_name: str = self.gateway_combo.currentText()
        else:
            self.name_line.setText(contract.name)
            gateway_name = contract.gateway_name

            ix: int = self.gateway_combo.findText(gateway_name)
            self.gateway_combo.setCurrentIndex(ix)

            self.price_digits = get_digits(contract.pricetick)

        self.clear_label_text()
        self.volume_line.setText("")
        self.price_line.setText("")

        req: SubscribeRequest = SubscribeRequest(
            symbol=symbol, exchange=Exchange(exchange_value)
        )
        self.main_engine.subscribe(req, gateway_name)

    widget_module.TradingWidget.set_vt_symbol = set_vt_symbol


def patch_trading_widget_stock_completer() -> None:
    """给 TradingWidget 的代码/名称输入框加搜索下拉。"""
    stocks = _load_stock_list()
    if not stocks:
        print("未找到本地股票列表数据，跳过代码搜索下拉补丁")
        return

    from vnpy.trader.ui import QtCore, QtGui, QtWidgets
    from vnpy.trader.ui import widget as widget_module

    _patch_set_vt_symbol(widget_module)

    original_init = widget_module.TradingWidget.__init__

    def new_init(self, main_engine, event_engine):
        original_init(self, main_engine, event_engine)

        # 共用一个数据模型：显示 "代码 名称 (交易所)"
        model = QtGui.QStandardItemModel()
        for code, exchange_value, name in stocks:
            item = QtGui.QStandardItem(f"{code} {name} ({exchange_value})")
            item.setData((code, exchange_value, name))
            model.appendRow(item)

        def apply_selection(index: QtCore.QModelIndex) -> None:
            data = model.itemFromIndex(index).data()
            if not data:
                return
            code, exchange_value, name = data

            # 交易所下拉显示文本可能是 "SSE (上交所)"，按前缀匹配切换
            combo = self.exchange_combo
            for i in range(combo.count()):
                if combo.itemText(i).split(" ")[0] == exchange_value:
                    combo.setCurrentIndex(i)
                    break

            def fill() -> None:
                self.symbol_line.setText(code)
                self.set_vt_symbol()
                # 无合约信息时 set_vt_symbol 会清空名称，这里用本地名称回填
                if not self.name_line.text():
                    self.name_line.setText(name)

            # completer 会在 activated 之后把完整补全文本写回输入框，
            # 用 singleShot 排在其后覆盖为纯代码
            QtCore.QTimer.singleShot(0, fill)

        def make_completer() -> QtWidgets.QCompleter:
            completer = QtWidgets.QCompleter(model)
            completer.setFilterMode(QtCore.Qt.MatchFlag.MatchContains)
            completer.setCompletionMode(
                QtWidgets.QCompleter.CompletionMode.PopupCompletion
            )
            completer.setCaseSensitivity(QtCore.Qt.CaseSensitivity.CaseInsensitive)
            completer.setMaxVisibleItems(12)
            completer.activated[QtCore.QModelIndex].connect(apply_selection)
            return completer

        self.symbol_line.setCompleter(make_completer())
        self.symbol_line.setPlaceholderText("输入代码或名称搜索")

        # 名称框原本只读，放开后也支持按名称搜索
        self.name_line.setReadOnly(False)
        self.name_line.setCompleter(make_completer())
        self.name_line.setPlaceholderText("输入名称搜索")

    widget_module.TradingWidget.__init__ = new_init
