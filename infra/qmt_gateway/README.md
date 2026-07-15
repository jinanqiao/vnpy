# Windows QMT 网关

这是一个运行在 Windows 交易电脑上的轻量 HTTP 网关，用来把国金 QMT / miniQMT 的本地 Python 接口暴露给当前 vn.py 量化项目。

它不是新的交易系统，只是一个桥接进程：

```text
vn.py 本地项目  ->  HTTP  ->  Windows QMT 网关  ->  国金 QMT 客户端
```

## 前置条件

1. Windows 电脑已安装国金 QMT / miniQMT。
2. QMT 客户端已登录实盘账户。
3. Windows 上安装 Python，建议 3.8 到 3.11。
4. QMT 自带或国金提供的 `xtquant` Python 包可被当前 Python 解释器导入。

如果双击 `install_windows.bat` 时看到 `'python' 不是内部或外部命令`，说明 Windows 找不到 Python，不是 32/64 位问题。现在脚本会自动尝试安装 Python 3.11 64 位版本到当前用户目录，并配置 PATH。

你也可以单独双击：

```text
install_python_windows.bat
```

它会调用官方 Python 安装包，安装位置类似：

```text
C:\Users\你的用户名\AppData\Local\Programs\Python\Python311
```

这个安装方式默认不需要管理员权限。

如果自动安装失败，再手动处理：

1. 安装 64 位 Python 3.8 到 3.11，安装第一页勾选 `Add python.exe to PATH`。
2. 安装后重新打开命令行，执行 `python --version` 或 `py -3 --version`。
3. 如果你必须使用 QMT 自带 Python，可以先在 cmd 里指定路径：

```bat
set PYTHON_EXE=C:\你的Python路径\python.exe
install_windows.bat
```

可以先在 Windows PowerShell 里验证：

```powershell
python -c "from xtquant.xttrader import XtQuantTrader; print('xtquant ok')"
```

如果这里报 `No module named xtquant`，需要先把 QMT 的 Python 接口目录加入 `PYTHONPATH`，或使用 QMT 文档推荐的 Python 环境。

## 安装

你可以在 Mac 上开发，然后只把整个 `qmt_gateway` 目录复制到 Windows 交易电脑上运行。Windows 上不需要 Cursor。

在 Mac 上可以先打包：

```bash
cd /Users/xulandong/code8/vnpy
bash scripts/package_qmt_gateway.sh
```

然后用 U 盘、微信文件传输、局域网共享、远程桌面复制、OneDrive 等方式，把 `qmt_gateway.zip` 放到 Windows 上并解压，例如：

```text
C:\vnpy\qmt_gateway
```

进入目录后，双击运行：

```text
install_windows.bat
```

这个脚本会自动检查 Python；如果没有 Python，会先自动安装 Python 3.11 64 位版本。随后它会创建 `.venv`、安装 Flask、numpy、pandas，并在没有 `config.json` 时复制一份配置模板。

如果你想手动执行，也可以在 PowerShell 里运行：

```powershell
cd C:\vnpy\qmt_gateway
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

## 配置

复制配置模板：

```powershell
copy config.example.json config.json
```

修改 `config.json`：

```json
{
  "host": "127.0.0.1",
  "port": 8710,
  "api_token": "change-this-token",
  "dry_run": true,
  "qmt_user_path": "C:/国金证券QMT/bin.x64/userdata_mini",
  "account_id": "你的资金账号"
}
```

关键字段说明：

- `host`：默认 `127.0.0.1` 只允许本机访问。要让 Mac 上的当前 vn.py 项目访问，改成 `0.0.0.0`，并在 Windows 防火墙放行端口。
- `api_token`：接口鉴权 token。远程访问时必须改成强随机字符串。
- `dry_run`：默认 `true`，下单接口只返回模拟委托号，不会真实下单。确认只读接口正常后，再手动改成 `false`。
- `qmt_user_path`：QMT 的 `userdata_mini` 目录。不同安装路径可能不同，请以本机实际路径为准。
- `account_id`：国金资金账号。
- `max_order_value`：单笔最大委托金额，默认 2 万。
- `allowed_symbols`：白名单。为空表示不限制；例如 `["600000.SH", "000001.SZ"]`。
- `blocked_symbols`：黑名单，命中后禁止下单。

## 启动

确保 QMT 客户端已经登录，然后双击：

```text
start_windows.bat
```

这个窗口不要关闭。窗口开着，网关就在线；窗口关掉，网关就停止。

如果你想手动启动，也可以在 PowerShell 里运行：

```powershell
cd C:\vnpy\qmt_gateway
.\.venv\Scripts\activate
python qmt_gateway.py
```

看到类似输出说明 HTTP 服务已启动：

```text
Running on http://127.0.0.1:8710
```

## 接口测试

健康检查不需要 token：

```powershell
curl http://127.0.0.1:8710/health
```

主动连接 QMT：

```powershell
curl -X POST http://127.0.0.1:8710/connect -H "X-API-Token: change-this-token"
```

查询资金：

```powershell
curl http://127.0.0.1:8710/account -H "X-API-Token: change-this-token"
curl http://127.0.0.1:8710/accounts -H "X-API-Token: change-this-token"
curl http://127.0.0.1:8710/account/status -H "X-API-Token: change-this-token"
```

查询持仓：

```powershell
curl http://127.0.0.1:8710/positions -H "X-API-Token: change-this-token"
curl http://127.0.0.1:8710/positions/600000.SH -H "X-API-Token: change-this-token"
curl http://127.0.0.1:8710/positions/statistics -H "X-API-Token: change-this-token"
```

查询委托：

```powershell
curl http://127.0.0.1:8710/orders -H "X-API-Token: change-this-token"
curl "http://127.0.0.1:8710/orders?cancelable_only=true" -H "X-API-Token: change-this-token"
curl http://127.0.0.1:8710/orders/123456 -H "X-API-Token: change-this-token"
```

查询成交：

```powershell
curl http://127.0.0.1:8710/trades -H "X-API-Token: change-this-token"
```

查询实时快照：

```powershell
curl -X POST http://127.0.0.1:8710/market/subscribe `
  -H "Content-Type: application/json" `
  -H "X-API-Token: change-this-token" `
  -d "{\"symbols\":[\"600000.SH\"],\"period\":\"1m\"}"

curl http://127.0.0.1:8710/market/quote/600000.SH -H "X-API-Token: change-this-token"
```

行情接口默认带超时保护。如果返回 `QMT market data request timed out`，说明 `xtdata` 当前没有及时返回，需要检查 QMT 行情是否已登录、是否已订阅、客户端行情页是否正常。

查询批量 tick / 盘口：

```powershell
curl "http://127.0.0.1:8710/market/fulltick?symbols=600000.SH,000001.SZ" -H "X-API-Token: change-this-token"
```

查询分钟 K：

```powershell
curl "http://127.0.0.1:8710/market/bars/600000.SH?period=1m&count=240" -H "X-API-Token: change-this-token"
```

如果 K 线接口返回 `No module named 'numpy'` 或 `No module named 'pandas'`，说明 Windows 网关虚拟环境缺少行情数据依赖。重新运行：

```powershell
cd C:\vnpy\qmt_gateway
.\.venv\Scripts\activate
pip install -r requirements.txt
```

查询日 K：

```powershell
curl "http://127.0.0.1:8710/market/bars/600000.SH?period=1d&count=250" -H "X-API-Token: change-this-token"
```

查询交易日历：

```powershell
curl "http://127.0.0.1:8710/market/trading-dates?market=SH&count=250" -H "X-API-Token: change-this-token"
```

QMT 数据底座接口，供股票池、因子、回测和实盘风控使用：

```powershell
# 板块列表
curl "http://127.0.0.1:8710/market/sectors" -H "X-API-Token: change-this-token"

# 板块成分股，例如沪深 A 股、沪深300、自定义板块等
curl "http://127.0.0.1:8710/market/sector-stocks?sector=沪深A股&limit=20&detail=false" -H "X-API-Token: change-this-token"

# A 股证券主数据。内部使用 get_stock_list_in_sector + get_instrument_detail
curl "http://127.0.0.1:8710/market/symbols?sector=沪深A股&limit=5000&detail=true" -H "X-API-Token: change-this-token"

# 单只合约详情：名称、上市/退市日期、涨跌停、股本、是否交易等
curl "http://127.0.0.1:8710/market/instruments/600000.SH?complete=true" -H "X-API-Token: change-this-token"

# 指数权重，例如沪深300
curl "http://127.0.0.1:8710/market/index-weight/000300.SH" -H "X-API-Token: change-this-token"

# 财务数据。tables 可选：Balance, Income, CashFlow, Capital, HolderNum, Top10Holder, Top10FlowHolder, PershareIndex
curl -X POST "http://127.0.0.1:8710/market/financial" `
  -H "Content-Type: application/json" `
  -H "X-API-Token: change-this-token" `
  -d "{\"symbols\":[\"600000.SH\"],\"tables\":[\"Balance\",\"Income\"],\"start_time\":\"20230101\",\"end_time\":\"20261231\",\"sample_rows\":5}"

# 下载/刷新 QMT 本地板块与财务缓存
curl -X POST "http://127.0.0.1:8710/market/download/sectors" -H "X-API-Token: change-this-token"
curl -X POST "http://127.0.0.1:8710/market/download/financial" `
  -H "Content-Type: application/json" `
  -H "X-API-Token: change-this-token" `
  -d "{\"symbols\":[\"600000.SH\"],\"tables\":[\"Balance\",\"Income\"],\"start_time\":\"20230101\",\"end_time\":\"20261231\"}"
```

行情自检，一次性验证 xtdata、实时快照、订阅、分钟 K、日 K 和交易日历：

```powershell
curl "http://127.0.0.1:8710/market/self-test?symbol=600000.SH" -H "X-API-Token: change-this-token"
```

如果 K 线为空，用诊断接口查看 QMT 原始返回结构：

```powershell
curl "http://127.0.0.1:8710/market/debug/xtdata" -H "X-API-Token: change-this-token"
curl "http://127.0.0.1:8710/market/debug/bars/600000.SH?period=1d&count=5" -H "X-API-Token: change-this-token"
curl "http://127.0.0.1:8710/market/debug/bars/600000.SH?period=1m&count=5" -H "X-API-Token: change-this-token"
```

dry-run 下单测试：

```powershell
curl -X POST http://127.0.0.1:8710/orders `
  -H "Content-Type: application/json" `
  -H "X-API-Token: change-this-token" `
  -d "{\"symbol\":\"600000.SH\",\"side\":\"buy\",\"quantity\":100,\"price\":10.5}"
```

撤单：

```powershell
curl -X POST http://127.0.0.1:8710/orders/123456/cancel -H "X-API-Token: change-this-token"
curl -X POST http://127.0.0.1:8710/orders/cancel-by-sysid `
  -H "Content-Type: application/json" `
  -H "X-API-Token: change-this-token" `
  -d "{\"market\":\"SH\",\"sysid\":\"你的系统委托号\"}"
```

新股申购、信用账户只读信息和交易接口诊断：

```powershell
curl http://127.0.0.1:8710/ipo/data -H "X-API-Token: change-this-token"
curl http://127.0.0.1:8710/ipo/purchase-limit -H "X-API-Token: change-this-token"
curl http://127.0.0.1:8710/credit/detail -H "X-API-Token: change-this-token"
curl http://127.0.0.1:8710/credit/subjects -H "X-API-Token: change-this-token"
curl http://127.0.0.1:8710/credit/slo-code -H "X-API-Token: change-this-token"
curl http://127.0.0.1:8710/credit/assure -H "X-API-Token: change-this-token"
curl http://127.0.0.1:8710/credit/compacts -H "X-API-Token: change-this-token"
curl http://127.0.0.1:8710/debug/trader -H "X-API-Token: change-this-token"
```

网关目前刻意不暴露银证转账、信用预约/展期、外部成交同步、任意文件导出、自定义板块写入等高风险或会修改 QMT 本地状态的接口。后续如确实需要，应单独加权限、审计和二次确认。

## 从 Mac 访问 Windows 网关

1. Windows 配置里把 `host` 改成 `0.0.0.0`。
2. Windows 防火墙放行 TCP `8710`。
3. 获取 Windows 局域网 IP，例如 `192.168.1.20`。
4. 在 Mac 上测试：

```bash
curl http://192.168.1.20:8710/health
curl http://192.168.1.20:8710/account -H "X-API-Token: change-this-token"
```

## 实盘启用顺序

建议严格按这个顺序：

1. `mock_mode=true`，只测试 HTTP 服务。
2. `mock_mode=false`、`dry_run=true`，测试连接 QMT 和查询资金持仓。
3. 仍保持 `dry_run=true`，测试下单接口能通过风控。
4. 交易时间内把 `dry_run=false`，用小金额、白名单股票做 100 股手动委托。
5. 验证撤单、成交、持仓刷新都正常后，再接当前 vn.py 项目。

不要一开始就让策略自动实盘下单。

## 常见问题

### `No module named xtquant`

当前 Python 找不到 QMT 接口。请确认使用的是 QMT 文档指定的 Python 环境，或把 QMT 的 `site-packages` / `xtquant` 所在目录加入 `PYTHONPATH`。

### `qmt_user_path is required`

没有配置 QMT 的 `userdata_mini` 路径。请在 Windows 上找到国金 QMT 安装目录下的 `userdata_mini`。

### 查询接口成功，但下单被拒绝

检查这些配置：

- `dry_run` 是否仍为 `true`。
- 当前是否在 A 股交易时间。
- `quantity` 是否超过 `max_order_quantity`。
- `quantity * price` 是否超过 `max_order_value`。
- 股票是否被 `allowed_symbols` / `blocked_symbols` 限制。

### Mac 无法访问 Windows 网关

检查：

- `host` 是否为 `0.0.0.0`。
- Windows 防火墙是否放行端口。
- 两台机器是否在同一个局域网。
- 是否能从 Mac `ping` 到 Windows IP。
