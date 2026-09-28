# ETH 多周期数据与策略研究

本次交付的是可复现研究脚本。**真实数据检验未通过：新版全样本及时间留出仍亏损。**
没有把任何版本接到账户，也没有提交交易或创建警报。

## 文件与用途

- `download_okx.py`：下载 OKX `ETH-USDT-SWAP` 的 4H、1H、15m、5m、1m 已收盘 K 线、历史资金费、近期公开逐笔、盘口、持仓量和行情快照。无需 API Key。
- `backtest.py`：原策略 v2 的独立 Python 镜像、仅添加 H1 过滤的对照、新版 v3，以及成本翻倍的敏感性检验。不是 TradingView 官方模拟引擎。
- `原策略v2基线.pine`：用户粘贴的原始源码留档。
- `../../tradingview/ETH四小时一小时过滤五分钟回踩策略.pine`：新的 Pine v6 研究版，中文文件名、显示名称、参数和提示。
- `test_research.py`：合成边界测试。它们证明特定逻辑满足测试条件，不证明盈利。
- `测试报告.md`：本次真实行情的数据范围、所有配置结果及限制。

## 一行运行

下面命令均在项目根目录执行：

```bash
cd '/Users/wangye/YeWang/加密货币/缠论'
```

重现本次固定截止时间的数据：

```bash
.venv/bin/python research/eth_mtf/download_okx.py --days 60 --end 2026-09-28T17:00:00Z --proxy http://127.0.0.1:29290
```

下载至最新已收盘分钟，另存新目录，避免覆盖本次验证数据：

```bash
.venv/bin/python research/eth_mtf/download_okx.py --days 60 --output var/eth_mtf_latest --proxy http://127.0.0.1:29290
```

运行回测及测试：

```bash
.venv/bin/python research/eth_mtf/backtest.py
.venv/bin/python -m unittest research.eth_mtf.test_research -v
```

回测新数据：

```bash
.venv/bin/python research/eth_mtf/backtest.py --data var/eth_mtf_latest --output var/eth_mtf_latest_results
```

代理地址是本机现有设置；其他机器请换成自己的代理或省略。客户端也读取系统/环境代理，不会修改系统配置。每20页保存一次检查点；同一路径、同一时间范围可续传。存在缺口会在清单标记并拒绝完整回测，不填造K线。

## TradingView 使用

打开 **OKX:ETHUSDT.P 的标准5分钟图**，将 `ETH四小时一小时过滤五分钟回踩策略.pine` 全部粘贴进 Pine 编辑器，保存并添加到图表。

- 4小时决定大方向，1小时已收盘数据过滤，5分钟完成推动→回踩→确认。
- 新版要求4小时EMA20/EMA60间距至少0.3倍4小时ATR；每日最多6次开仓、平仓冷却6根。
- 新版使用已收盘1小时慢线失效退出，替代原先连续两根5分钟破慢线退出；固定初始止损和目标不放宽。
- 初始资金1000 USDT、单笔计划风险0.25%、名义仓位最多权益1倍；均为研究假设，未优化。
- 单边手续费0.05%、滑点2 ticks、限价穿透1 tick；默认关闭平台的 Bar Magnifier (高精度), 不依赖Premium/Ultimate。具备相应权限时可在属性里手动开启; 放大镜细周期和覆盖范围由 TradingView 决定, 不保证恰好1分钟。不同撮合设置的结果不能直接混比。
- 当前查询的OKX规格是每张0.1 ETH、最小/步长0.01张，即0.001 ETH。Python以ETH核算。Pine数量还要结合 `syminfo.pointvalue` 核对，不能把OKX张数直接粘贴为图表数量。
- 如修改费用，需同步调整策略属性与“成本估算”参数。时间限制默认关闭；开启后只限制新入场，截止后仍管理原有持仓。
- Python回测从数据开头计算5分钟指标并预热180根；高周期补充更早数据。TradingView加载历史起点不同会改变EMA初始化及交易序列。
- 已进行源码审阅，但**尚未在 TradingView 官方环境编译运行**；本地没有可调用的 Pine 编译器。Python通过不代表Pine编译通过。

Pine不能直接读取这份本地OKX文件，也不包含公开逐笔或资金费扣款。它使用TradingView自己的OKX图表数据。Python中的1分钟数据用于撮合，不是把5分钟规则直接缩短成1分钟高频策略。

## 数据口径

所有时间为UTC毫秒。K线字段 `ts` 是开盘时间，`close_time` 是收盘边界，`available_at` 暂假设为收盘边界，未模拟网络延迟；只保留 `confirm=1`。交易所返回的 `vol` 为张数，`base_vol` 为ETH，`quote_vol` 为USDT。实时快照的抓取时间晚于历史回测截止时间，**不输入历史信号或筛选**。

`recent_trades.json` 仅为当时最近成交快照，本次500条，不是60天全量逐笔；`side` 为主动成交方向，`sz` 为张数。没有读取私人账户成交。合约规格保存的是当前快照，不是完整历史规格变更表。

`var/eth_mtf/manifest.json` 保存请求范围、缺失检查、数据哈希；`var/eth_mtf_results/summary.json` 保存配置、全部试验和源文件哈希。每次试验文件包含 `trades`、逐分钟 `equity` 和收盘 `signals`，信号记录高周期可知时间。

## 撮合与验证边界

按5分钟收盘生成信号，最早在次根开盘执行；高周期必须在当前5分钟开盘前已收盘。持仓用真实1分钟OHLC撮合：跳空止损按更差开盘扣滑点，同一分钟双触发保守止损优先；如果分钟开盘已经越过目标，可确定开盘目标先发生。限价仅触碰不算成交，要穿透1 tick；未建排队或成交量参与率。

市价计划基于信号价，下一根可能跳价，0.25%是计划风险而非保证上限。为保留原策略语义，未在看到下一根开盘后重新挑选入场。实际名义金额加费用超出现金则拒绝。保护单与市价退出在同一开盘的优先级可能与TradingView不同；止损/止盈的 `exit_ts` 表示其所在分钟结束时刻，不声称获知秒级成交时刻。

资金费按下载的实际结算事件计入，当时存量仓位先结算、再处理该分钟新开仓；结算价格以1分钟成交开盘价估计，缺少历史结算标记价。未模拟维持保证金、清算、盘口深度、延迟或交易所拒单。因此这些是含费用的研究估算，不能称为真实永续可执行收益。

若期末仍持仓，显式记录原持仓，并以最终收盘扣退出费用和滑点作**研究性期末清算**，不是可提前知道收盘价的真实交易。本次全样本没有期末持仓。权益回撤按1分钟收盘估值，未捕捉分钟内最坏浮亏。

四组配置在第一次回测前固定，没有网格寻优。前40天为开发观察，后20天为一次性时间留出；留出开始重置为1000 USDT并从空仓起步，指标仍使用此前历史。完整区间和分段结果因此不必简单相加。现在留出结果已经看过，今后基于它修改参数须使用新留出期验证。原策略镜像也存在时间区间、历史初始化、费用及撮合引擎差异，不能直接等同截图中65笔的结果。

研究规则：D01/D02/D03（收盘、口径、资金费）；B01/B02/B03/B04（因果成交、路径歧义、时间留出、前缀不变）。它是EMA回踩研究模型，不冒称完整缠论二买或课程已验证策略。

参考：[OKX公开接口](https://app.okx.com/docs-v5/en/)、[TradingView高周期读取](https://www.tradingview.com/pine-script-docs/concepts/other-timeframes-and-data/)、[TradingView策略模拟](https://www.tradingview.com/pine-script-docs/concepts/strategies/)。


## 运行时错误排查

2026-09-28 增加分类诊断：分别显示图表周期、图表类型、两组均线参数、止损距离、回踩根数、时间范围和品种错误的当前值。未移除原检查，也未改变交易逻辑。

点击图表策略名称旁的红色感叹号，读取**完整错误文字与行号**。仅显示“运行时错误”不足以确认原因。

- 提示“周期不匹配”：本Pine须运行在标准5分钟图，不能加载到1分钟、1小时或4小时图后期待它沿用5分钟模型。
- 提示“图表类型不匹配”：切换标准蜡烛图，不能用合成价格回测同一策略。
- 参数或时间冲突：按错误中显示的数值修正相应输入；默认均线20/60、止损0.25%至1.5%、回踩2至12根，时间限制默认关闭。
- 品种不匹配：选择 `OKX:ETHUSDT.P`，不要把 `ETHUSD`、其他币或指数当作同一合约。
- 如果报错不是上述中文提示（例如资源、历史数据、订单数量或平台限制），保留完整原文再定位，不把猜测当成已修复。

本次仍未取得TradingView官方编译/运行结果。诊断提示增强不能证明用户遇到的错误已经消失。


### 截图中的编辑器警告修正

用户截图显示的是黄色警告和Unicode易混淆字符提示, 没有展示运行时异常正文。已在同一Pine文件完成:

1. 将 `ta.change(utcDay)` 提到全局变量 `newUtcDay`, 保证每根K线调用, 再参与日界判断, 避免Pine v6的 `or` 短路跳过调用。
2. 面板条件改为 `barstate.islastconfirmedhistory or (barstate.isrealtime and barstate.isconfirmed)`, 历史加载结束即显示最后确认状态, 实时收盘刷新。保留 `calc_on_every_tick=false`, 不改变策略收盘决策方式。
3. 中文提示保留, 易混淆的全角逗号/冒号等标点改为半角。

这次只调整日界计算调用位置、面板刷新和提示文字; Python历史交易规则未修改, 不重跑参数筛选。已做本地源码检查, TradingView官方编译/运行仍需在编辑器验证。

依据: [Pine v6短路求值](https://www.tradingview.com/pine-script-docs/migration-guides/to-pine-version-6/#lazy-evaluation-of-conditions), [收盘策略与面板](https://www.tradingview.com/pine-script-docs/visuals/tables/).


### v3.1诊断: 空白报告与高精度设置

用户截图中策略名称旁有红色感叹号, 表示计算遇到运行时错误。它与上一次编辑器黄色警告不同; 未读到弹出错误原文前, 不认定是某个入场条件导致整月无交易。

本地已有v3固定规则账本在2026-09-01至09-28截止共有39笔入场。它来自独立Python撮合, 不保证TradingView同样39笔, 但说明不能直接将空白报告解释为整月没信号。

截图高精度已启用并提示Premium; 因此本版将 `use_bar_magnifier` 默认设为 `false`, 让普通回测不依赖会员功能。这是兼容性调整, 尚未确认就是用户红色报错的根因。入场过滤参数及风险预算未放宽; 撮合方式改变仍可能改变最终成交和回测收益。

将最新全文替换到Pine编辑器, 保存并移除旧策略后重新添加, 确认标题含 `v3.1诊断`。已有图表设置可能覆盖源码默认值, 请同时把策略报告顶部“高精度”改为“默认精度”, 或在属性中关闭Bar Magnifier。不需要为排查此问题购买升级。

若成功运行, 右上面板新增4小时/5分钟和1小时预热进度, 方向过滤、时间区间及模拟权益。状态栏会区分预热不足、方向不一致、均线间距不足、区间外和正常等待回踩。如果仍有红色感叹号, 点击它获取完整错误及行号后继续定位。

依据: [官方无订单排查](https://www.tradingview.com/support/solutions/43000478450-i-ve-successfully-added-a-strategy-to-my-chart-but-it-doesn-t-generate-orders/), [高精度权限说明](https://www.tradingview.com/pine-script-docs/language/declaration-statements/#use_bar_magnifier)。


## 缠论结构过滤（2026-09-28 追加）

- `chan_features.py`：用项目 `chan.analyze` 按因果方式给每根已收盘 4 小时/1 小时 K 线计算笔、中枢、背驰、买卖点状态（窗口 600 根），结果缓存在 `var/eth_mtf_chan`。
- `backtest.py --chan`：在 v3 规则上叠加缠论开关（`Profile` 的 `chan_*` 字段），输出 `var/eth_mtf_chan_results`；不加 `--chan` 时行为与原来完全一致。
- 结论见《测试报告》"缠论结构过滤试验"：只有"4小时最近买卖点方向须同向"全样本转正（+19.7 USDT/37 笔），留出仅 3 笔，未验证盈利。
- 对应 Pine：`../../tradingview/ETH四小时缠论买卖点过滤五分钟回踩策略.pine`（v4），在 v3.1 上增加 4 小时缠论引擎（移植自"缠论严格过滤交易策略"）与"4小时缠论买卖点过滤"选项。

## 方案2 / 方案3 追加（2026-09-28）

- `download_okx_htf.py --proxy ...`：补下 OKX 日线/周线（UTC 对齐），写入 `var/eth_mtf_latest/1D.json、1W.json` 并更新清单。
- `backtest_1m.py`：1 小时方向 + 15 分钟中枢边沿 + 1 分钟入场。**未通过**，全部配置亏损。
- `backtest_grid.py`：日线/周线缠论定方向 + 4 小时中枢挂单网格，输出 `var/eth_grid_results`。60 天为正但只有一个中枢样本，详见《测试报告》。

- BTC 对照：`download_okx.py --inst BTC-USDT-SWAP --output var/btc_mtf_latest` + `download_okx_htf.py --output var/btc_mtf_latest`，再 `backtest_grid.py --data var/btc_mtf_latest --output var/btc_grid_results`。
