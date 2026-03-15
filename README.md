# Futu CopyTrade - 富途模拟组合跟单系统

自动跟踪富途牛牛公开模拟组合，通过 moomoo OpenD 在真实账户执行交易。

## ⚠️ 风险提示

**使用本程序前，请务必阅读并同意 moomoo 的 API 使用协议：**
- [moomoo OpenAPI 风险披露协议](https://risk-disclosure.us.moomoo.com/index?agreementNo=USOT0027)

**免责声明：**
- 本程序仅供学习交流，不构成任何投资建议
- 自动交易存在风险，可能导致资金损失
- 使用者需自行承担所有交易风险和后果

## 功能特性

- 🔄 自动监控富途公开模拟组合持仓变化
- 📈 自动跟单买入/卖出
- 🛡️ 买入后自动挂跟踪止损单
- 📧 邮件通知持仓变化和交易状态
- 🌙 夜盘检测变化发邮件，盘前自动执行
- 💼 支持底仓保护，区分跟单持仓和底仓
- ⏰ 盘前/盘中/盘后不同交易策略

## 前置条件

### 1. 安装 moomoo OpenD

OpenD 是 moomoo 提供的本地网关程序，用于连接交易 API。

1. 下载 OpenD：
   - 访问 [moomoo OpenD 下载页面](https://www.moomoo.com/download/openD)
   - 选择对应操作系统版本下载

2. 安装并启动 OpenD：
   - macOS: 解压后运行 `moomoo_OpenD`
   - Windows: 解压后运行 `moomoo_OpenD.exe`

3. 登录 moomoo 账户


4. 签署 API 协议：
   - 登录 moomoo 客户端
   - 进入 设置 → API 设置
   - 签署 [OpenAPI 风险披露协议](https://risk-disclosure.us.moomoo.com/index?agreementNo=USOT0027)

### 2. 安装 Python 依赖

```bash
cd futu_copytrade
pip install -r requirements.txt
```

## 配置说明

## 配置说明

编辑 `config.py` 文件，所有参数说明如下：

### 监控目标

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `PORTFOLIO_ID` | `"183730"` | 要跟踪的富途公开模拟组合 ID |
| `POLL_INTERVAL` | `30` | 轮询间隔（秒），检查组合变化的频率 |

### OpenD 连接

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `OPEND_HOST` | `"127.0.0.1"` | OpenD 网关地址，本地运行保持默认 |
| `OPEND_PORT` | `11111` | OpenD 网关端口，保持默认 |

### 交易账户

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `TRADE_ENV` | `"REAL"` | 交易环境：`SIMULATE`=模拟盘，`REAL`=真实账户 |
| `TRADE_PASSWORD` | `"xxxxxx"` | moomoo 交易解锁密码（6位数字） |
| `TRADE_MARKET` | `"US"` | 交易市场：`US`=美股，`HK`=港股 |

### 跟单策略

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `TOTAL_CAPITAL` | `10000.0` | 跟单资金（美元），用于计算每只股票买多少股 |
| `MIN_TRADE_AMOUNT` | `100.0` | 最小交易金额（美元），低于此金额不跟单 |
| `WEIGHT_CHANGE_THRESHOLD` | `0.02` | 权重变化阈值，低于 2% 视为股价波动忽略 |
| `PREMARKET_SELL_MODE` | `"same_count"` | 盘前卖出模式，见下方详细说明 |

**盘前卖出模式 (`PREMARKET_SELL_MODE`)：**
- `"always"` - 盘前正常跟随卖出信号卖出
- `"never"` - 盘前只买不卖，卖出信号只更新跟单记录
- `"same_count"` - 盘前只有 5只→5只 换仓时不卖，其他情况正常卖出

### 邮件通知

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `EMAIL_ENABLED` | `True` | 是否启用邮件通知 |
| `EMAIL_SMTP_HOST` | `"smtp.gmail.com"` | SMTP 服务器地址 |
| `EMAIL_SMTP_PORT` | `465` | SMTP 端口（SSL） |
| `EMAIL_SENDER` | - | 发件人邮箱 |
| `EMAIL_PASSWORD` | - | 邮箱密码或应用专用密码 |
| `EMAIL_RECEIVER` | - | 收件人邮箱 |

### 限价单追单

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `LIMIT_SLIPPAGE` | `0.001` | 限价单初始滑点比例（0.1%） |
| `REPRICE_SLIPPAGE` | `0.001` | 每次改价增加的滑点比例 |
| `ORDER_CHECK_INTERVAL` | `10` | 检查订单状态的间隔（秒） |
| `MAX_REPRICE_TIMES` | `5` | 最大改价次数，超过则放弃 |

### 止损设置

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `STOP_LOSS_MODE` | `"trailing"` | 止损模式：`none`=不挂止损，`fixed`=固定比例，`trailing`=跟踪止损 |
| `STOP_LOSS_RATIO` | `0.033` | 固定止损比例（仅 fixed 模式），跌 3.3% 触发 |
| `TRAIL_TYPE` | `"ratio"` | 跟踪止损类型：`ratio`=按比例，`amount`=按金额 |
| `TRAIL_VALUE` | `3` | 跟踪止损值：比例模式下 3=3%，金额模式下为美元数 |

### 其他

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `FUTU_COOKIE` | `""` | 富途网页端 Cookie（可选，用于访问需登录的组合） |
| `LOG_FILE` | `"copytrade.log"` | 日志文件名 |

### 获取模拟组合 ID

1. 打开 [富途牛牛网页版](https://www.futunn.com/)
2. 找到要跟踪的公开模拟组合
3. URL 中的数字即为组合 ID，如 `https://www.futunn.com/portfolio/183730` 中的 `183730`

### Gmail 应用专用密码

1. 登录 Google 账户
2. 进入 安全性 → 两步验证 → 应用专用密码
3. 生成一个 16 位密码用于 `EMAIL_PASSWORD`

## 使用方法OpenD 连接
```bash
python test.py
```
成功后应显示有实仓。

```bash
python test_positions.py
```

成功会显示当前账户持仓。

### 启动跟单程序

```bash
# 正常启动
python main.py

# 干跑模式（只检测不下单）
python main.py --dry

# 只检查一次
python main.py --once
```

### 首次启动流程

1. 程序会删除旧的状态文件
2. 获取当前模拟组合持仓
3. 查询你的账户持仓
4. 对于组合中每只股票：
   - 如果你已持有：询问其中多少股是跟单的
   - 如果你未持有：询问是否买入
5. 保存状态后进入轮询监控

## 交易时段说明

| 时段 | 时间 (ET) | 行为 |
|------|-----------|------|
| 夜盘 | 20:00-4:00 | 只发邮件通知，不交易 |
| 盘前 | 4:00-9:30 | 执行买入，卖出根据配置 |
| 盘中 | 9:30-16:00 | 正常买卖（市价单） |
| 盘后 | 16:00-20:00 | 正常买卖（限价单） |

## 文件说明

| 文件 | 说明 |
|------|------|
| `config.py` | 配置文件 |
| `main.py` | 主程序入口 |
| `monitor.py` | 组合监控模块 |
| `trader.py` | 交易执行模块 |
| `stoploss.py` | 止损模块 |
| `notify.py` | 邮件通知模块 |
| `test_positions.py` | 测试脚本 |
| `snapshot.json` | 组合持仓快照（自动生成） |
| `copytrade_positions.json` | 跟单持仓记录（自动生成） |
| `copytrade.log` | 运行日志（自动生成） |

## 常见问题

### Q: 提示 "No one available account"
A: 检查 OpenD 是否已登录，以及是否签署了 API 协议。

### Q: 市价单报错 "非盘中时段不允许市价单"
A: 程序会自动在盘前盘后使用限价单，确保使用最新版本代码。

### Q: 如何保护底仓不被卖出？
A: 首次启动时，对于已持有的股票，输入跟单数量时填 0 或小于实际持仓的数字，差额部分即为底仓。

## License

MIT
