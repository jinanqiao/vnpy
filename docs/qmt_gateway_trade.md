# QMT Gateway 实盘/模拟盘接入说明

## 当前结论

本项目已经补齐一套 Windows 侧 QMT Gateway，目录在 `infra/qmt_gateway`。它运行在阿里云 Windows 机器上，负责连接本地 QMT/miniQMT；当前 Mac 项目通过 HTTP 调用它。

网关支持两类能力：

- 行情与数据：健康检查、股票池、K 线、交易日历、板块、合约详情、指数权重、财务数据。
- 交易与账户：连接 QMT、账户资金、账户状态、持仓、委托、成交、下单、撤单、IPO、两融查询。
- 实盘运维：状态接口、请求审计、事件轮询、批量下单、批量撤单、幂等下单、历史/对账查询占位、raw-call 白名单扩展。

这套网关可以支撑模拟盘演练。默认配置 `dry_run=true`，下单接口只返回模拟委托号，不会真实下单。实盘前必须在 Windows 网关上确认只读接口、资金账户、持仓、撤单链路都正常，再手动把 `dry_run` 改成 `false`。

## Windows 部署

在 Mac 当前项目根目录打包：

```bash
bash scripts/package_qmt_gateway.sh
```

生成的 zip 在 `outputs/qmt_gateway/` 下，例如：

```text
outputs/qmt_gateway/qmt_gateway_20260714_1800.zip
```

把 zip 复制到阿里云 Windows 机器，解压到类似：

```text
C:\vnpy\qmt_gateway
```

进入目录后双击：

```text
install_windows.bat
```

安装完成后，复制配置：

```powershell
copy config.example.json config.json
```

重点修改：

```json
{
  "host": "0.0.0.0",
  "port": 8710,
  "api_token": "换成强随机token",
  "mock_mode": false,
  "dry_run": true,
  "qmt_user_path": "C:/国金证券QMT/bin.x64/userdata_mini",
  "account_id": "你的资金账号",
  "max_order_value": 20000,
  "allowed_symbols": [],
  "blocked_symbols": [],
  "require_api_token": true,
  "max_batch_orders": 20,
  "allow_raw_trader_calls": false,
  "allow_raw_market_calls": false,
  "allowed_raw_trader_methods": [],
  "allowed_raw_market_methods": []
}
```

启动前要先打开并登录 QMT/miniQMT 客户端，然后双击：

```text
start_windows.bat
```

## 连通性检查

健康检查不需要 token：

```bash
curl http://阿里云公网IP:8710/health
```

连接 QMT：

```bash
curl -X POST http://阿里云公网IP:8710/connect -H "X-API-Token: 你的token"
```

账户/持仓/委托：

```bash
curl http://阿里云公网IP:8710/account -H "X-API-Token: 你的token"
curl http://阿里云公网IP:8710/positions -H "X-API-Token: 你的token"
curl http://阿里云公网IP:8710/orders -H "X-API-Token: 你的token"
curl http://阿里云公网IP:8710/trades -H "X-API-Token: 你的token"
```

网关运行状态和审计：

```bash
curl http://阿里云公网IP:8710/version -H "X-API-Token: 你的token"
curl http://阿里云公网IP:8710/status -H "X-API-Token: 你的token"
curl "http://阿里云公网IP:8710/audit/requests?limit=100" -H "X-API-Token: 你的token"
```

dry-run 下单测试：

```bash
curl -X POST http://阿里云公网IP:8710/orders \
  -H "Content-Type: application/json" \
  -H "X-API-Token: 你的token" \
  -d '{"client_order_id":"signal-20260715-000001","symbol":"600000.SH","side":"buy","quantity":100,"price":10.5,"price_type":"limit"}'
```

批量下单和撤单：

```bash
curl -X POST http://阿里云公网IP:8710/orders/batch \
  -H "Content-Type: application/json" \
  -H "X-API-Token: 你的token" \
  -d '{"orders":[{"client_order_id":"batch-1","symbol":"600000.SH","side":"buy","quantity":100,"price":10.5}]}'

curl -X POST http://阿里云公网IP:8710/orders/cancel-all \
  -H "Content-Type: application/json" \
  -H "X-API-Token: 你的token" \
  -d '{"symbol":"600000.SH"}'
```

事件轮询，适合模拟盘/实盘主循环定时拉取状态变化：

```bash
curl -X POST http://阿里云公网IP:8710/events/poll \
  -H "Content-Type: application/json" \
  -H "X-API-Token: 你的token" \
  -d '{"after":0,"limit":200}'
```

批量行情和订阅：

```bash
curl -X POST http://阿里云公网IP:8710/market/quotes \
  -H "Content-Type: application/json" \
  -H "X-API-Token: 你的token" \
  -d '{"symbols":["600000.SH","000001.SZ"]}'

curl -X POST http://阿里云公网IP:8710/market/subscribe \
  -H "Content-Type: application/json" \
  -H "X-API-Token: 你的token" \
  -d '{"symbols":["600000.SH"],"period":"1m"}'

curl -X POST http://阿里云公网IP:8710/market/unsubscribe \
  -H "Content-Type: application/json" \
  -H "X-API-Token: 你的token" \
  -d '{"symbols":["600000.SH"]}'
```

## 当前项目侧调用

Python 客户端在 `vnpy/alpha/research/qmt_gateway_trade.py`：

```python
from vnpy.alpha.research.qmt_gateway_data import QmtGatewayConfig
from vnpy.alpha.research.qmt_gateway_trade import QmtOrderRequest, QmtTradeGatewayClient

config = QmtGatewayConfig.from_env()
client = QmtTradeGatewayClient(config)

print(client.health())
print(client.state())

result = client.place_order(
    {
        "client_order_id": "signal-20260715-000001",
        "symbol": "600000.SH",
        "side": "buy",
        "quantity": 100,
        "price": 10.5,
    }
)
print(result)
```

环境变量：

```bash
export QMT_GATEWAY_URL="http://阿里云公网IP:8710"
export QMT_GATEWAY_TOKEN="你的token"
```

## 风控建议

实盘前保持 `dry_run=true` 至少跑完整个交易日，检查信号、目标仓位、委托数量、价格、撤单逻辑和盘后对账。确认无误后，再把 Windows 侧 `config.json` 改成 `dry_run=false`。

建议先用这些约束保护账户：

- `max_order_value`：限制单笔最大下单金额。
- `max_order_quantity`：限制单笔最大股数。
- `allowed_symbols`：第一阶段只允许少量白名单股票。
- `blocked_symbols`：永久禁止不想交易的标的。
- `enforce_trading_hours`：禁止非 A 股交易时段下单。

## 后续免重新部署的扩展口

如果某个 QMT 版本有特殊方法，而当前网关还没有专门封装，可以通过 raw-call 白名单临时调用。这个能力默认关闭，实盘建议只对白名单方法开放。

Windows 侧 `config.json` 示例：

```json
{
  "allow_raw_trader_calls": true,
  "allowed_raw_trader_methods": ["query_stock_orders", "query_stock_trades"],
  "allow_raw_market_calls": true,
  "allowed_raw_market_methods": ["get_full_tick", "get_market_data_ex"]
}
```

调用示例：

```bash
curl -X POST http://阿里云公网IP:8710/debug/trader/call/query_stock_orders \
  -H "Content-Type: application/json" \
  -H "X-API-Token: 你的token" \
  -d '{"args":[],"kwargs":{"with_account":true}}'

curl -X POST http://阿里云公网IP:8710/market/debug/call/get_full_tick \
  -H "Content-Type: application/json" \
  -H "X-API-Token: 你的token" \
  -d '{"args":[["600000.SH"]],"kwargs":{}}'
```

这类接口只适合作为“避免重新部署的兜底口”，长期稳定使用的能力后续仍应沉淀成明确接口。

## 和增量数据同步的关系

QMT Gateway 负责“实时连接”和“最新数据拉取”，不是长期数据库。长期数据仍然落在本地数据湖和 TimescaleDB：

- 日线/股票池/交易日历继续用 `scripts/refresh_live_data_foundation.py --run-downloads` 增量刷新。
- 数据版本、质量报告同步到 TimescaleDB 用 `scripts/sync_metadata_to_timescale.py`。
- 实盘产生的信号、委托、成交、持仓快照后续写入 TimescaleDB 的 `live.*` 表。

也就是说，QMT Gateway 是交易通道，PostgreSQL/TimescaleDB 是实盘运行账本，Parquet/DuckDB 是研究和批量查询底座。
