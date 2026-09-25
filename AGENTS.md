# 缠论K线分析工具

本地网页版 OKX 永续合约缠论分析工具(Flask + klinecharts 9.8.9,数据内存缓存)。

**动手改代码前,先用 Skill 工具加载 `chanlun-app` 技能文档**——里面有架构、
缠论规则量化口径、评分体系、klinecharts 踩坑清单、本机环境陷阱和调试方法。

## 最关键的几条(详见技能文档)

- 启动:`cd /Users/wangye/learnCode/WaveTradingCryptocurrency && .venv/bin/python server.py`
  → http://127.0.0.1:8420(本机只有 Python 3.9,勿写 3.10+ 的 `X | None` 注解语法)
  改 .py 必须重启进程(`kill $(lsof -ti:8420 -sTCP:LISTEN)` 后再启);改 static/ 只需刷新浏览器。
- 本机代理 127.0.0.1:29290 拦截 localhost:curl 加 `--noproxy '*'`,
  Chrome 加 `--no-proxy-server`;服务端访问 OKX 则需要走代理(勿清 env)。
- klinecharts 的 styles 覆盖**不做深合并**:线型必须传完整对象(含 dashedValue),
  只传 color 会每帧抛异常导致图表冻结(踩过)。
- 数据刷新走增量 updateData 保留用户视图;applyNewData 只用于切币/切周期。
- 缠论规则口径以《缠论MACD币圈实战手册.html》为准,勿随意改参数
  (新笔≥5根处理后K、面积比<0.7、中枢延伸≤9段、HIST=2×(DIF−DEA))。
- 所有评分统一"越高越好";分析只用已收盘K线;信号有锁定/未锁定机制。
- 用户是交易者:要结论(做多/做空/观望)不要只展示;功能做成主界面开关,不做弹窗。
- 已按用户要求移除:超短线三级共振、双顶双底、主力护盘位——勿重新引入。
