import sys
from pathlib import Path

from vnpy.event import EventEngine

from vnpy.trader.engine import MainEngine
from vnpy.trader.ui import MainWindow, create_qapp
from vnpy.trader.utility import load_json

# vnpy_ctp 在 macOS arm64 上无法编译安装，缺失时跳过该接口
try:
    from vnpy_ctp import CtpGateway
except ImportError:
    CtpGateway = None

# 远程 QMT 网关（HTTP 桥模式），实现位于 wd-vnpy 项目
_WD_VNPY_ROOT = Path.home() / "code7" / "wd-vnpy"
if _WD_VNPY_ROOT.exists() and str(_WD_VNPY_ROOT) not in sys.path:
    sys.path.insert(0, str(_WD_VNPY_ROOT))
try:
    from core.http_gateway import HttpGateway
except ImportError as e:
    print(f"未能加载 HTTP_QMT 网关: {e}")
    HttpGateway = None

# 交易所下拉框中英文并列显示（如 "SSE (上交所)"），不影响底层 vt_symbol 解析
try:
    from core.ui_patches import patch_exchange_combo_chinese
    patch_exchange_combo_chinese()
except ImportError as e:
    print(f"未能加载交易所中文显示补丁: {e}")

# 交易面板代码/名称输入框支持本地股票列表搜索下拉
from stock_selector import patch_trading_widget_stock_completer
patch_trading_widget_stock_completer()
# from vnpy_ctptest import CtptestGateway
# from vnpy_mini import MiniGateway
# from vnpy_femas import FemasGateway
# from vnpy_sopt import SoptGateway
# from vnpy_esunny import EsunnyGateway
# from vnpy_xtp import XtpGateway
# from vnpy_tora import ToraStockGateway, ToraOptionGateway
# from vnpy_ib import IbGateway
# from vnpy_tap import TapGateway
# from vnpy_da import DaGateway
# from vnpy_rohon import RohonGateway
# from vnpy_tts import TtsGateway

# from vnpy_paperaccount import PaperAccountApp
from vnpy_ctastrategy import CtaStrategyApp
from vnpy_ctabacktester import CtaBacktesterApp
# from vnpy_spreadtrading import SpreadTradingApp
# from vnpy_algotrading import AlgoTradingApp
# from vnpy_optionmaster import OptionMasterApp
# from vnpy_portfoliostrategy import PortfolioStrategyApp
# from vnpy_scripttrader import ScriptTraderApp
# from vnpy_chartwizard import ChartWizardApp
# from vnpy_rpcservice import RpcServiceApp
# from vnpy_excelrtd import ExcelRtdApp
from vnpy_datamanager import DataManagerApp
# from vnpy_datarecorder import DataRecorderApp
# from vnpy_riskmanager import RiskManagerApp
# from vnpy_webtrader import WebTraderApp
# from vnpy_portfoliomanager import PortfolioManagerApp


def main():
    """"""
    qapp = create_qapp()

    event_engine = EventEngine()

    main_engine = MainEngine(event_engine)

    if CtpGateway is not None:
        main_engine.add_gateway(CtpGateway)
    if HttpGateway is not None:
        main_engine.add_gateway(HttpGateway)
    # main_engine.add_gateway(CtptestGateway)
    # main_engine.add_gateway(MiniGateway)
    # main_engine.add_gateway(FemasGateway)
    # main_engine.add_gateway(SoptGateway)
    # main_engine.add_gateway(UftGateway)
    # main_engine.add_gateway(EsunnyGateway)
    # main_engine.add_gateway(XtpGateway)
    # main_engine.add_gateway(ToraStockGateway)
    # main_engine.add_gateway(ToraOptionGateway)
    # main_engine.add_gateway(IbGateway)
    # main_engine.add_gateway(TapGateway)
    # main_engine.add_gateway(DaGateway)
    # main_engine.add_gateway(RohonGateway)
    # main_engine.add_gateway(TtsGateway)

    # main_engine.add_app(PaperAccountApp)
    main_engine.add_app(CtaStrategyApp)
    main_engine.add_app(CtaBacktesterApp)
    # main_engine.add_app(SpreadTradingApp)
    # main_engine.add_app(AlgoTradingApp)
    # main_engine.add_app(OptionMasterApp)
    # main_engine.add_app(PortfolioStrategyApp)
    # main_engine.add_app(ScriptTraderApp)
    # main_engine.add_app(ChartWizardApp)
    # main_engine.add_app(RpcServiceApp)
    # main_engine.add_app(ExcelRtdApp)
    main_engine.add_app(DataManagerApp)
    # main_engine.add_app(DataRecorderApp)
    # main_engine.add_app(RiskManagerApp)
    # main_engine.add_app(WebTraderApp)
    # main_engine.add_app(PortfolioManagerApp)

    main_window = MainWindow(main_engine, event_engine)
    main_window.showMaximized()

    # 启动后自动连接远程 QMT 桥（配置保存在 ~/.vntrader/connect_http_qmt.json）
    if HttpGateway is not None:
        setting: dict = load_json("connect_http_qmt.json")
        if setting:
            main_engine.connect(setting, "HTTP_QMT")

    qapp.exec()


if __name__ == "__main__":
    main()
