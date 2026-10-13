"""AI 复核:每小时把 1H/4H/1D/1W 快照发给 DeepSeek,确认是否右侧入场信号。

- 定时:后台单线程,每个整点后 RUN_DELAY 秒(等 OKX 把上一根 1H 标为已收盘)
  对关注列表里的每个币种跑一次;关注列表与历史结果落在 var/ai_review/(已 gitignore)。
- 快照:各级别最近 N 根**已收盘**K线(OHLCV + MACD/KDJ/RSI)+ 缠论结构摘要
  (笔/中枢/背驰/背离/买卖点,含锁定状态)+ 本工具自己的决策,另附未收盘K线与现价。
- 模型:DeepSeek OpenAI 兼容接口,JSON 输出;key 读 env `DEEPSEEK_API_KEY` 或根目录 .env。
  模型名可用 env/.env `DEEPSEEK_MODEL` 覆盖(默认 deepseek-chat)。
- AI 结论只作复核参考,不改动 decision.py 的决策,不下单。

命令行查看快照(不调用模型):.venv/bin/python ai_review.py ETH-USDT-SWAP --dry
命令行立即复核一次:      .venv/bin/python ai_review.py ETH-USDT-SWAP
"""

import json
import os
import sys
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

import requests

import decision

ROOT = os.path.dirname(os.path.abspath(__file__))
STORE_DIR = os.path.join(ROOT, "var", "ai_review")
WATCH_FILE = os.path.join(STORE_DIR, "watch.json")

API_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-chat"
RUN_DELAY = 90          # 整点后等待秒数,确保上一根 1H 已 confirm
HISTORY_KEEP = 48       # 内存/接口返回的历史条数(约两天)

LEVELS = ["1H", "4H", "1D", "1W"]
# 每个级别发送的已收盘K线根数(控制 token:合计约 250 行)
BARS_PER_LEVEL = {"1H": 72, "4H": 60, "1D": 60, "1W": 52}
TZ = timezone(timedelta(hours=8))

SYSTEM_PROMPT = """你是严格的缠论右侧交易复核员,服务对象是做 OKX 永续合约的交易者。
你会收到同一币种 1H/4H/1D/1W 四个级别的已收盘K线(含 MACD(10,20,5) HIST=2×(DIF−DEA)、KDJ、RSI)、
本地程序算好的缠论结构(笔/中枢/背驰/指标背离/买卖点,locked=true 表示已锁定不会重绘)、
以及本地程序的单级别决策。你的任务:判断"此刻是否存在已经确认的右侧入场信号"。

右侧入场的判定口径:
1. 只认已确认的结构:一买(趋势背驰)之后的二买/三买(卖点对称);或离开中枢后回踩/回抽不回到中枢区间(三类点)。
   一类点本身属于左侧,除非次级别已出现二类点确认,否则不算右侧。
2. 信号必须基于已收盘K线;未锁定(locked=false)的信号只能算"待确认"。
3. 大级别定方向、小级别找入场:入场方向不得与 4H/1D 的主方向冲突;1W 用于判断大背景。
4. 现价已越过信号点结构止损位则信号作废;盈亏比(到第一目标)< 1:2 时不应追单,给出更好的入场价。
5. 盘整背驰、动能衰减、隐藏背离只能作辅助,不能单独构成入场。
6. 不确定就观望,宁缺毋滥。不要编造数据里没有的价位。

只输出一个 JSON 对象,字段:
{
  "verdict": "做多" | "做空" | "观望",
  "right_side": "已确认" | "待确认" | "无",
  "confidence": 0-100 的整数(越高越可靠),
  "signal_level": "触发信号的级别,如 1H",
  "signal": "信号简述,如 1H 二买(4H 三买背景)",
  "entry": 数值或 null,
  "stop": 数值或 null,
  "targets": [数值, ...],
  "rr": 到第一目标的盈亏比数值或 null,
  "better_entry": 盈亏比不足时的合格入场价或 null,
  "agree_with_local": true/false(是否认同本地程序决策),
  "levels": {"1W": "一句话", "1D": "一句话", "4H": "一句话", "1H": "一句话"},
  "reasons": ["依据1", "依据2", ...],
  "risks": ["风险1", ...],
  "invalidation": "什么情况下结论作废",
  "watch_next": "下一小时重点观察什么"
}
全部用中文。"""


# ---------------- 配置 ----------------
def _env(name: str, default: str = "") -> str:
    v = os.environ.get(name, "").strip()
    if v:
        return v
    path = os.path.join(ROOT, ".env")
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith(name + "="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return default


def configured() -> bool:
    return bool(_env("DEEPSEEK_API_KEY"))


# ---------------- 快照 ----------------
def _t(ts_ms: Optional[int]) -> Optional[str]:
    if not ts_ms:
        return None
    return datetime.fromtimestamp(ts_ms / 1000, TZ).strftime("%Y-%m-%d %H:%M")


def _r(v, nd=6):
    if v is None:
        return None
    return float(f"{v:.{nd}g}")


def _bars(payload, n):
    """最近 n 根已收盘K线的紧凑表(列见 columns)。"""
    cs = payload["candles"]
    ind = payload["indicators"]
    idx = [i for i, c in enumerate(cs) if c["confirm"] == 1][-n:]
    m, k, r = ind["macd"], ind["kdj"], ind["rsi"]
    rows = []
    for i in idx:
        c = cs[i]
        rows.append([_t(c["ts"]), _r(c["o"]), _r(c["h"]), _r(c["l"]), _r(c["c"]), _r(c["vol"], 4),
                     _r(m["dif"][i], 4), _r(m["dea"][i], 4), _r(m["hist"][i], 4),
                     _r(k["k"][i], 3), _r(k["d"][i], 3), _r(k["j"][i], 3), _r(r["rsi12"][i], 3)])
    return rows


def _bi(b):
    return {"dir": b["dir"], "from": [_t(b.get("start_ts")), _r(b["start_price"])],
            "to": [_t(b.get("end_ts")), _r(b["end_price"])],
            "state": b.get("state"), "locked": b.get("locked")}


def _zs(z):
    return {"zg": _r(z["zg"]), "zd": _r(z["zd"]), "gg": _r(z.get("gg")), "dd": _r(z.get("dd")),
            "from": _t(z.get("start_ts")), "to": _t(z.get("end_ts")),
            "bi_count": z.get("bi_count"), "extending": z.get("extending"), "locked": z.get("locked")}


def _pick(d, keys):
    return {k: d.get(k) for k in keys if d.get(k) is not None}


def _level_snapshot(payload, dec, bar):
    ch = payload["chan"]
    s = ch.get("summary") or {}
    cs = payload["candles"]
    live = cs[-1]
    out = {
        "columns": ["time", "open", "high", "low", "close", "vol",
                    "dif", "dea", "hist", "k", "d", "j", "rsi12"],
        "bars": _bars(payload, BARS_PER_LEVEL[bar]),
        "forming_bar": {"time": _t(live["ts"]), "o": live["o"], "h": live["h"], "l": live["l"],
                        "c": live["c"], "closed": live["confirm"] == 1},
        "price_pos_vs_last_center": s.get("price_pos"),
        "bi_last8": [_bi(b) for b in ch["bi"][-8:] if b.get("state") != "blocked"],
        "centers_last3": [_zs(z) for z in (ch.get("zhongshu_display") or [])[-3:]],
        "beichi_last3": [{"kind": b.get("kind"), "dir": b.get("dir"), "struct_ok": b.get("struct_ok"), "time": _t(b.get("ts")),
                          "price": _r(b.get("price")), "area_ratio": b.get("area_ratio"),
                          "score": b.get("score"), "locked": b.get("locked")}
                         for b in ch["beichi"][-3:]],
        "beili_last4": [{"type": b.get("type"), "subtype": b.get("subtype"), "time": _t(b.get("ts")),
                         "price": _r(b.get("price")), "votes": b.get("votes"), "score": b.get("score"),
                         "locked": b.get("locked")} for b in ch["beili"][-4:]],
        "bsp_last4": [{"type": p["type"], "time": _t(p.get("ts")), "price": _r(p["price"]),
                       "score": p.get("score"), "grade": p.get("grade"), "note": p.get("note"),
                       "confirms": p.get("confirms"), "locked": p.get("locked")}
                      for p in ch["bsp"][-4:]],
        "indicator_state": payload.get("signals") or {},
        "local_decision": _pick(dec, ["action", "dir_level", "dir_desc", "q", "position", "entry",
                                      "stop", "targets", "rr", "better_entry", "reason", "warnings"]),
    }
    return out


def build_snapshot(inst: str, get_data: Callable, force: bool = True):
    """get_data(inst, bar, force) → build_payload 结果(server._get_data)。"""
    datas = {}
    for bar in LEVELS:
        datas[bar] = get_data(inst, bar, force)
    levels = {}
    for bar in LEVELS:
        hi = decision.LEVEL_UP.get(bar, bar)
        dec = decision.decide(datas[bar], datas[hi], bar, hi)
        levels[bar] = _level_snapshot(datas[bar], dec, bar)
    live = datas["1H"]["candles"][-1]["c"]
    return {"inst": inst, "generated_at": _t(int(time.time() * 1000)) + " (UTC+8)",
            "last_price": live, "levels": levels}


# ---------------- 调用模型 ----------------
def call_deepseek(snapshot: dict):
    key = _env("DEEPSEEK_API_KEY")
    if not key:
        raise RuntimeError("未配置 DEEPSEEK_API_KEY(写入项目根目录 .env)")
    model = _env("DEEPSEEK_MODEL", DEFAULT_MODEL)
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "快照数据(JSON):\n" +
             json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))},
        ],
        "temperature": 0.2,
    }
    if "reasoner" not in model:  # reasoner 不支持 JSON 模式,靠提示词约束
        body["response_format"] = {"type": "json_object"}
    resp = requests.post(API_URL, json=body, timeout=180,
                         headers={"Authorization": f"Bearer {key}"})
    if resp.status_code != 200:
        raise RuntimeError(f"DeepSeek HTTP {resp.status_code}: {resp.text[:300]}")
    j = resp.json()
    text = j["choices"][0]["message"]["content"] or ""
    s, e = text.find("{"), text.rfind("}")
    try:
        result = json.loads(text[s:e + 1])
    except ValueError:
        raise RuntimeError(f"模型输出不是 JSON: {text[:300]}")
    return result, model, j.get("usage") or {}


# ---------------- 定时与存储 ----------------
class Reviewer:
    def __init__(self):
        self.get_data: Optional[Callable] = None
        self.lock = threading.Lock()
        self.watch = set()
        self.history = {}   # inst → deque[record]
        self.running = set()
        self.next_run = None
        self._started = False
        self._load()

    def _load(self):
        try:
            with open(WATCH_FILE, encoding="utf-8") as f:
                self.watch = set(json.load(f))
        except (OSError, ValueError):
            self.watch = set()

    def _save_watch(self):
        os.makedirs(STORE_DIR, exist_ok=True)
        with open(WATCH_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(self.watch), f)

    def _hist(self, inst):
        if inst not in self.history:
            dq = deque(maxlen=HISTORY_KEEP)
            try:
                with open(os.path.join(STORE_DIR, f"{inst}.jsonl"), encoding="utf-8") as f:
                    for line in f:
                        try:
                            dq.append(json.loads(line))
                        except ValueError:
                            pass
            except OSError:
                pass
            self.history[inst] = dq
        return self.history[inst]

    def set_watch(self, inst, on):
        with self.lock:
            (self.watch.add if on else self.watch.discard)(inst)
            self._save_watch()

    def status(self, inst):
        with self.lock:
            hist = list(self._hist(inst))
        return {"code": 0, "inst": inst, "configured": configured(),
                "watching": inst in self.watch, "running": inst in self.running,
                "next_run": _t(int(self.next_run * 1000)) if self.next_run else None,
                "latest": hist[-1] if hist else None,
                "history": [{"at": h.get("at"), "price": h.get("price"),
                             "verdict": (h.get("result") or {}).get("verdict"),
                             "right_side": (h.get("result") or {}).get("right_side"),
                             "confidence": (h.get("result") or {}).get("confidence"),
                             "error": h.get("error")} for h in reversed(hist)]}

    def run_once(self, inst):
        with self.lock:
            if inst in self.running:
                return
            self.running.add(inst)
        rec = {"at": _t(int(time.time() * 1000)), "inst": inst}
        try:
            snap = build_snapshot(inst, self.get_data, force=True)
            rec["price"] = snap["last_price"]
            rec["local"] = {b: snap["levels"][b]["local_decision"].get("action") for b in LEVELS}
            result, model, usage = call_deepseek(snap)
            rec.update(result=result, model=model, tokens=usage.get("total_tokens"))
        except Exception as e:  # 定时任务不能因单次失败中断
            rec["error"] = str(e)[:500]
        finally:
            with self.lock:
                self.running.discard(inst)
                self._hist(inst).append(rec)
                os.makedirs(STORE_DIR, exist_ok=True)
                with open(os.path.join(STORE_DIR, f"{inst}.jsonl"), "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    def run_async(self, inst):
        threading.Thread(target=self.run_once, args=(inst,), daemon=True).start()

    def _loop(self):
        while True:
            now = time.time()
            self.next_run = (now // 3600 + 1) * 3600 + RUN_DELAY
            time.sleep(max(1.0, self.next_run - now))
            with self.lock:
                insts = sorted(self.watch)
            for inst in insts:  # 串行,避免同时打满 OKX 限速
                self.run_once(inst)

    def start(self, get_data: Callable):
        self.get_data = get_data
        if not self._started:
            self._started = True
            threading.Thread(target=self._loop, daemon=True, name="ai_review").start()


reviewer = Reviewer()


if __name__ == "__main__":
    import server  # 命令行用法:复用服务端的拉取逻辑
    inst = (sys.argv[1] if len(sys.argv) > 1 else "ETH-USDT-SWAP").upper()
    snap = build_snapshot(inst, server._get_data, force=True)
    if "--dry" in sys.argv:
        print(json.dumps(snap, ensure_ascii=False, indent=1))
        print(f"\n快照约 {len(json.dumps(snap, ensure_ascii=False, separators=(',', ':')))} 字符",
              file=sys.stderr)
    else:
        result, model, usage = call_deepseek(snap)
        print(json.dumps({"model": model, "usage": usage, "result": result},
                         ensure_ascii=False, indent=1))
