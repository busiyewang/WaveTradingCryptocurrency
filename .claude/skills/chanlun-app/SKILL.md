---
name: chanlun-app
description: 缠论K线分析工具的完整知识库:架构与API、缠论规则量化口径、评分体系、klinecharts 9.8.9 踩坑清单、本机环境陷阱(代理/venv)、调试与验证方法。修改本项目任何代码前先读本文档。
---

# 缠论K线分析工具 · 技能文档

## 1. 项目概览

本地网页版 OKX 永续合约缠论分析工具。Flask 后端(内存缓存,不落盘不下单)+
klinecharts 9.8.9 前端。规则依据同目录《缠论MACD币圈实战手册.html》
(内嵌 JS 工具的算法在 L1973-L2700 附近,背驰评分器 L2070-2185)。

```bash
# 启动(必须用 venv,系统无 python 命令、python3 无依赖)
.venv/bin/python server.py   # → http://127.0.0.1:8420
# 停止/查看
kill $(lsof -ti:8420 -sTCP:LISTEN)
lsof -nP -iTCP:8420 -sTCP:LISTEN
```

**改了 .py 文件必须重启进程**(Flask 非 debug 模式不热载);改 static/ 前端文件
不用重启,浏览器刷新即可。

## 2. 文件职责与 API

| 文件 | 职责 |
|---|---|
| server.py | OKX拉取(candles+history-candles分页到600根,倒序转正序)、缓存dict、路由 |
| chan.py | 包含处理→分型→新笔→中枢→背驰→指标背离(detect_beili)→买卖点+评分+共振票+锁定标志 |
| indicators.py | MACD/KDJ/RSI(国内公式) + analyze_signals(量价/主力/KDJ/RSI背离) |
| decision.py | decide(单级别决策:大级别定方向+本级别定买点) + LEVEL_UP 映射 |
| patterns.py | 收敛三角形(中枢震荡收敛,突破≈三类买卖点) |
| static/app.js | 图表、overlay绘制、面板渲染、增量刷新、仓位计算器 |

API:`GET /api/kline?inst=ETH-USDT-SWAP&bar=4H&force=0|1` → candles + indicators +
chan{fenxing,bi,zhongshu,beichi,beili,bsp,summary} + signals + patterns + decision。
`GET /api/instruments` → 快捷币列表。
bar 大小写敏感:`1m/5m/15m/1H/4H/1D/1W`。OKX 返回倒序、末根 confirm=0。
**所有分析只用 confirm==1 的已收盘K线**;现价/price_pos 例外(用实时价)。

## 3. 缠论规则量化口径(与手册一致,勿随意改)

- 包含处理:向上取高高、向下取低低,方向由前一根未被包含K决定。
- 分型:处理后K线,顶分型=中间K高低点都最高;底反之。k_idx 锚定极值所在原始K。
- **新笔**:端点间(含端点)≥5 根处理后K;端点取分型极值(非收盘);顶底交替,
  同类型取更极端者直接替换(新分型必然更靠后,距离只增不减,无需回退)。
- 中枢:连续3笔重叠,ZG=min(3笔高点),ZD=max(3笔低点),ZG>ZD;
  延伸并入最多 9 段(否则吞掉一切);GG/DD=区间极值。
- **背驰**:比较前后两个同向笔(i-2 与 i)。门槛=后段创新极值;
  有效=同向MACD柱面积比 后/前 <0.7;**面积放大=一票否决(下跌中继,绝不给买点)**。
  趋势/盘整背驰:之前最近两个中枢是否同向依次移动。
  MACD(12,26,9),HIST=2×(DIF−DEA)。
- 买卖点:B1=有效底背驰端点;B2=B1后回调笔不破前低;B3=向上离开中枢的笔后,
  回踩笔终点>ZG。卖点对称。
- **锁定机制**:信号所在笔端点之后已有新确认笔=locked;在最新确认笔端点上=
  未锁定(更极端分型会使端点移动/重绘)。分型确认滞后极值1-3根,彻底锁定约5根+。
- 级别权重 LEVEL_WEIGHT={1m:0.55,5m:0.7,15m:0.8,1H:0.9,4H:1.0,1D:1.0,1W:1.0}
  (15m 手册未给,0.8 为插值)。

## 4. 评分体系(统一口径:**分数越高越可靠**,0-100)

- 背驰评分:面积比分段 ≤0.4→30/≤0.6→26/≤0.7→20/≤0.85→11/<1→4/≥1→0,
  + DIF极值20 + 区间套20(单周期无法验证,恒0,面板注明) + 趋势15 + 结构12
  + 斜率8 + 量能7 + 反向分型8,÷120×100×级别权重。
- 买卖点评分:类型基础分(B1=40+背驰未加权分×0.4;B2=50+回撤浅18/10/4+缩量8
  +背驰联动×0.15;B3=55+回踩浅15/8/3+突破力度15/8/3+缩量8)×级别权重。
  等级:≥75高(绿)/≥60中(蓝)/≥45一般(黄)/<45弱(灰)。
- 决策质量分 q=方向0.4+信号0.4+确认0.2;仓位 ≥0.85→100%/≥0.7→70%/≥0.5→50%
  /≥0.35→30%。硬约束:信号与大级别冲突→禁止;盈亏比<1:2→降级观望并给
  合格入场价 e=(目标+2×止损)/3。

## 5. klinecharts 9.8.9 踩坑清单(重要!)

1. **styles 覆盖不做深合并**:createIndicator 传 `styles.lines` 时每个元素必须是
   完整对象 `{style,'solid',smooth,size,color,dashedValue:[2,2]}`——只传 {color}
   会导致内部读 `dashedValue[0]` 每帧渲染抛 TypeError,**表现为整个图表冻结、
   K线拖不动**。已在 app.js EMA 处踩过并注释。
2. CDN 文件路径是 `dist/umd/klinecharts.min.js`(不是 dist/ 根),用 jsdelivr;
   已本地化到 static/。锁 9.8.x,v10 API 有变动。
3. 自定义 overlay 的 figure 必须 `ignoreEvent:true`,否则挡鼠标事件影响拖图。
4. **刷新数据用增量**:同 inst+bar 用 `chart.updateData(逐根)` 保留用户缩放/拖动
   位置;`applyNewData` 会重置视图,只在切币/切周期时用。拖动中整体重载会造成
   副图断裂的半渲染状态。
5. 内置 RSI 是简单均值,与 OKX(Wilder 平滑)差异大 → 已 registerIndicator
   'RSI_CN';内置 MACD/KDJ 与国内口径一致可直接用。
6. 内置 overlay 'priceLine' 可画水平价格线(需要画入场/止损/止盈线时用它)。
7. text figure 支持 backgroundColor/padding(9.8 已并入,rectText 亦存在)。
8. 调试入口:`window._chart` 已暴露(app.js)。

## 6. 本机环境陷阱

- **本机代理 127.0.0.1:29290 会拦截 localhost**:curl 必须 `--noproxy '*'`;
  Chrome 必须 `--no-proxy-server`;python websocket 需先清 http_proxy 等 env。
  服务端 requests 访问 OKX 则**需要**走这个代理(默认读 env,别清)。
- Python 3.14.4(/opt/homebrew);依赖在 .venv(flask/requests/numpy/pandas/
  websocket-client)。`ls` 看不到 .venv 属正常(隐藏目录)。
- 无 git。用户曾把命令粘贴截断,给命令尽量一行完整可粘贴。

## 7. 调试与验证方法

- 截图验证:`"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
  --headless=new --disable-gpu --no-proxy-server --hide-scrollbars
  --screenshot=out.png --window-size=1680,1080 --virtual-time-budget=15000
  'http://127.0.0.1:8420'`(virtual-time-budget 等异步数据渲染完)。

- **交互问题用 CDP 实测**(scratchpad 有 cdp_test.py 模板):Chrome 加
  `--remote-debugging-port=9333 --remote-allow-origins='*'`,
  Input.dispatchMouseEvent 模拟拖动,前后对比 `_chart.getVisibleRange()`,
  并收集 Runtime.exceptionThrown——渲染循环里的异常会冻结交互,截图看不出来。
- API 验证:`curl -s --noproxy '*' 'http://127.0.0.1:8420/api/kline?inst=ETH-USDT-SWAP&bar=4H' | .venv/bin/python -c ...`
  检查:candles 升序600根、末根 confirm、chan 各字段、locked 标志。
- 算法抽查:构造 K 线(leg() 造波形)断言包含/分型/≥5成笔/ZG=min高点/
  背驰面积比;真实数据抽查笔端点落在极值、中枢 ZG/ZD 等于前3笔计算值。
- 常见"为什么没信号":先算面积比——加速下跌(比值>1)不给买点是**正确行为**;
  其次查信号是否已过期(走出新笔)、是否与大级别冲突被决策层拦截。

## 8. 用户偏好(交易者,重体验)

- 要**结论**不要只展示数据:先给 做多/做空/观望,再给依据和价位。
- 所有评分统一"**越高越好**",避免两套方向。
- 功能一律做成**复选框/开关按钮**直接在主界面操作,不要弹窗多步交互
  (曾做成弹窗被要求改为开关+图上画线)。
- 每类新标注(收敛等)都要有独立开关;EMA 配色对齐 OKX:5白/10红/
  20浅蓝/40橘黄。
- 文档写进 README.md(含 FAQ:python 命令不存在、venv、端口占用等用户踩过的坑)。
- 新概念要解释"怎么看"+落地成图上标注;信号滞后/重绘要明示(锁定机制)。

## 9. 已移除的功能(2026-08 用户要求,勿重新引入)

超短线三级共振模式(scalp/`/api/scalp`/⚡开关)、双顶双底形态(七项检验)、
主力护盘位标注(hupan)三项已整体删除。仓位计算器保留,入口从原超短线横幅
移到普通决策横幅的「仓位计算器」按钮(openCalc 用 state.data.decision 预填)。

## 10. 指标背离信号(2026-08 新增,补充缠论背驰太少的问题)

- chan.detect_beili:比较相邻两个同向确认笔端点,五票制(MACD柱22/DIF16/
  RSI16/KDJ12/缩量12)。常规背离(价新极值而指标拒绝,≥2票,反转)、
  隐藏背离(价未新极值而指标更极端,≥3票,中继须顺大级别)。
  评分=票权重+超卖超买区10+常规12,×LEVEL_WEIGHT,越高越好;与缠论背驰
  同 k_idx 的常规背离不输出(避免与⚡背驰/B1 重复)。类型 DB/DS,锁定机制同背驰。
- find_bsp 每个买卖点带 confirms/confirm_n(信号点±2根:KDJ叉、J钝化、
  RSI超卖超买、MACD柱拐头);decision 确认票 3票制→4票制(第4票=信号点共振≥2)。
- 前端:开关 ckDl「指标背离」,图上青(#1b7c83)买/洋红(#bf3989)卖标签
  offset 34,「隐」前缀=隐藏背离;面板买卖点表与 bsp 合并按 k_idx 排序。
- 手册缠论口径(面积比<0.7 等)未动,背离是并行的补充信号层。

## 11. 多级别联动页(2026-08 新增,/multi)

- multilevel.py:`analyze(big_payload, small_payload, big_bar, small_bar)`。
  关键位 = 大级别 ZG/ZD + 前高/前低笔端点(结构位)+ EMA20/40(辅助,内部自算);
  容差 tol = 0.6×大级别近20根平均振幅;小级别活跃信号(口径同 decision:位于
  最近两个确认笔端点范围内的背驰/背离/买卖点)落在某关键位±tol 内才「验证」,
  否则标杂波。硬过滤与 decision.py 一致:方向冲突禁止、盈亏比<2 观望给
  better_entry。刹车印(持仓检查):①反向常规背离/背驰(隐藏背离不算)
  ②放量滞涨(量≥3×前5均量+小实体/长影)③连续两根收盘破小级别EMA20(只有③
  算确认)→ 情况A/B/C。BAR_RANK/SMALL_DEFAULT 供路由校验与默认搭配。
- server.py:`GET /api/multi?inst=&big=4H&small=15m&force=` → {big,small,multi}
  (big/small 是完整 build_payload 数据,走同一 _cache);small 省略按
  SMALL_DEFAULT;small rank ≥ big rank 返回 400。`GET /multi` → multi.html。
- 前端 static/multi.html + multi.js:双图各带 EMA/VOL/MACD(overlay 注册代码
  与 app.js 同款,levelLine 新增:bounding.width 全宽虚线+右侧标签,须
  ignoreEvent);关键位横线两张图都画,testing 的加粗+「◀测试中」。
  币种经 localStorage('chan_inst') 与主页互通;刷新增量逻辑同 app.js
  (per-chart 记 key/lastList)。级别下拉不合法组合自动纠正。

补充(§6 代理陷阱,2026-08 实测):macOS 上 python urllib 即使清了 env 也会读
**系统级代理设置**(getproxies),访问 127.0.0.1 端口会 502 —— 必须
`build_opener(ProxyHandler({}))`;websocket-client 连 CDP 则要
`env -i PATH.. HOME..` 起进程 + `origin="http://127.0.0.1:9333"`,二者缺一
都是 "Connection to remote host was lost"。

§11 补充(同日优化):①刹车印视角跟随大级别方向(_brakes 收 big_dir,
neutral 才按小级别笔方向)——否则大级别涨、小级别回调时误显示"持空仓检查";
②目标位结构位优先(_pick_target:structure 优先,MA 兜底),防止 EMA 在现价
上方一点点时盈亏比被算虚小;③关键位相距<0.25×tol 自动合并(结构位优先保留,
名称用 ≈ 连接);④_breakout=场景三:小级别放量(≥2×前5均量)K线收盘穿越
关键位 → res["breakout"] 仅作观察信号展示,明示等大级别收盘确认,绝不给入场。
