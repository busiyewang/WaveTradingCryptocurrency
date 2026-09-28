"""方案2：1小时缠论定方向、15分钟中枢边沿定区域、1分钟精确入场；1分钟撮合。规则在首次运行前固定。"""
import argparse
from collections import Counter
import copy
from dataclasses import dataclass, asdict
import datetime
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from .download_okx import BARS, quality, save, iso
    from .backtest import protective_fill, crosscheck_timeframes
    from .chan_features import causal_states
except ImportError:
    from download_okx import BARS, quality, save, iso
    from backtest import protective_fill, crosscheck_timeframes
    from chan_features import causal_states


@dataclass
class Profile:
    name: str = 'm1_chan_a'
    h1_gate: bool = True        # 1小时收盘在中枢外侧同向
    h4_bsp23: bool = False      # 4小时最近二三类买卖点同向
    fee: float = 0.0005
    slippage_ticks: int = 2
    rr_min: float = 1.5         # 净盈亏比门槛
    tol_mult: float = 0.6       # 容差 = tol_mult × 近20根15分钟平均振幅
    min_pull: int = 3           # 回踩至少1分钟根数
    max_pull: int = 120         # 回踩最多等待根数
    min_stop_pct: float = 0.3
    stop_tol_mult: float = 0.25  # 止损=回踩极值外 stop_tol_mult×容差；v2 改为 1.0（以15分钟尺度而非1分钟噪音定失效）
    max_stop_pct: float = 1.5
    risk_pct: float = 0.25
    max_daily: int = 4
    cooldown: int = 30
    day_loss: float = 0.02


def profiles():
    return [Profile('m1_chan_a'),
            Profile('m1_chan_b_h4bsp', h4_bsp23=True),
            Profile('m1_chan_c_nogate', h1_gate=False),
            Profile('m1_chan_a_maker', fee=0.0002),
            # v2：看过v1结果后的一次修正（止损按15分钟容差放宽），单独标注，不再继续调
            Profile('m1_chan_v2', stop_tol_mult=1.0, min_stop_pct=0.5),
            Profile('m1_chan_v2_maker', stop_tol_mult=1.0, min_stop_pct=0.5, fee=0.0002)]


def features(data, cache_dir):
    f = pd.DataFrame(data['1m']).copy()
    f['index'] = np.arange(len(f))
    f['prev_h'] = f.h.shift()
    f['prev_l'] = f.l.shift()
    for bar, prefix in [('15m', 'm15'), ('1H', 'h1'), ('4H', 'h4')]:
        rows = data[bar]
        st = causal_states(rows, bar, cache_dir)
        ht = pd.DataFrame(rows)[['close_time', 'h', 'l']].copy()
        ht['rng'] = (ht.h - ht.l).rolling(20).mean()
        for k in ('zg', 'zd', 'pos', 'bsp23', 'bi_dir'):
            ht[k] = [s.get(k) for s in st]
        ht = ht[['close_time', 'rng', 'zg', 'zd', 'pos', 'bsp23', 'bi_dir']]
        ht.columns = [prefix + '_' + c for c in ht.columns]
        f = pd.merge_asof(f.sort_values('ts'), ht.sort_values(prefix + '_close_time'),
                          left_on='ts', right_on=prefix + '_close_time', direction='backward')
    return f


def gate(r, p):
    if p.h1_gate:
        pos = r['h1_pos']
        d = 1 if pos == 'above' else -1 if pos == 'below' else 0
    else:
        d = 0
        if isinstance(r['m15_pos'], str):
            d = 1 if r['m15_pos'] == 'above' else -1 if r['m15_pos'] == 'below' else 0
    if d and p.h4_bsp23:
        t = r['h4_bsp23']
        if not isinstance(t, str) or t[0] != ('B' if d == 1 else 'S'):
            return 0
    return d


def plan(side, r, pull_extreme, tol, equity, tick, step, min_qty, p):
    zg, zd = r['m15_zg'], r['m15_zd']
    slip = tick * p.slippage_ticks
    entry = r['c'] + side * slip
    edge = zg if side == 1 else zd
    struct_stop = (min(pull_extreme, edge) if side == 1 else max(pull_extreme, edge)) - side * p.stop_tol_mult * tol
    mindist = entry * p.min_stop_pct / 100
    stop = min(struct_stop, entry - mindist) if side == 1 else max(struct_stop, entry + mindist)
    stop = (math.floor(stop / tick) if side == 1 else math.ceil(stop / tick)) * tick
    distance = side * (entry - stop)
    if distance / entry > p.max_stop_pct / 100:
        return None, 'stop_too_wide'
    height = zg - zd
    target = edge + side * height
    target = (math.ceil(target / tick) if side == 1 else math.floor(target / tick)) * tick
    loss = distance + slip + p.fee * (entry + stop - side * slip)
    gain = side * (target - entry) - p.fee * (entry + target)
    if gain <= 0 or gain / loss < p.rr_min:
        return None, 'rr_below_min'
    qty = math.floor(min(equity * p.risk_pct / 100 / loss, equity * 0.99 / entry) / step) * step
    if qty < min_qty:
        return None, 'qty_too_small'
    return dict(side=side, stop=stop, target=target, qty=qty, signal_ts=int(r['close_time']),
                planned_entry=entry, planned_risk=loss * qty, net_rr=round(gain / loss, 2),
                zg=zg, zd=zd, tol=tol), None


def run(data, f, funding, instrument, p, start, end):
    tick = float(instrument['tickSz'])
    ctval = float(instrument['ctVal']) * float(instrument.get('ctMult') or 1)
    step = ctval * float(instrument['lotSz'])
    min_qty = ctval * float(instrument['minSz'])
    rates = {int(r['fundingTime']): float(r.get('realizedRate') or r['fundingRate']) for r in funding}
    cash, pos, pending, close_pending = 1000.0, None, None, None
    last_exit = -10 ** 9
    day, day_equity, day_entries, paused = None, 1000.0, 0, False
    last_equity = 1000.0
    trades, curve, signals = [], [], []
    counts = Counter()
    # 单方向状态：armed（已站上边沿外侧）、pull_start、pull_extreme、当前区域的zg/zd
    st = dict(armed=False, pull_start=None, pull_extreme=None, key=None, side=0)

    def close(price, ts, reason):
        nonlocal cash, pos, last_exit
        exit_fee = pos['qty'] * price * p.fee
        gross = pos['qty'] * pos['side'] * (price - pos['entry'])
        cash += gross - exit_fee
        pos.update(exit=price, exit_ts=int(ts), reason=reason, gross=gross, exit_fee=exit_fee,
                   net=gross - pos['entry_fee'] - exit_fee - pos['funding'])
        trades.append(pos)
        pos = None
        last_exit = i

    selected = f[(f.ts >= start) & (f.close_time <= end)]
    for r in selected.to_dict('records'):
        i = int(r['index'])
        t = int(r['ts'])
        # ---- 开盘：成交挂起的市价单 / 结构退出；资金费；保护单
        if pos is not None and t in rates:
            cost = pos['side'] * pos['qty'] * r['o'] * rates[t]
            cash -= cost
            pos['funding'] += cost
        if pos is not None and close_pending:
            close(r['o'] - pos['side'] * tick * p.slippage_ticks, t, close_pending)
        close_pending = None
        if pending:
            entry = r['o'] + pending['side'] * tick * p.slippage_ticks
            fee = entry * pending['qty'] * p.fee
            if entry * pending['qty'] + fee <= cash:
                pos = dict(pending, entry=entry, entry_ts=t, entry_fee=fee, funding=0.0)
                cash -= fee
            else:
                counts['margin_rejected'] += 1
            pending = None
        if pos is not None:
            fill = protective_fill(pos, r, tick, tick * p.slippage_ticks)
            if fill:
                counts['ambiguous_minutes'] += fill[2]
                close(fill[0], t + 60000, fill[1])
        equity = cash + (pos['qty'] * pos['side'] * (r['c'] - pos['entry']) if pos else 0)
        curve.append(dict(ts=t + 60000, equity=equity))
        current_day = t // 86400000
        if current_day != day:
            day, day_equity, day_entries, paused = current_day, last_equity, 0, False
        paused = paused or (day_equity - equity) / day_equity >= p.day_loss
        last_equity = equity

        # ---- 收盘：信号
        d = gate(r, p)
        zg, zd, tol = r['m15_zg'], r['m15_zd'], (r['m15_rng'] or float('nan')) * p.tol_mult
        valid = d != 0 and isinstance(zg, float) and math.isfinite(zg) and math.isfinite(tol) and zg > zd
        key = (d, zg, zd)
        if not valid or st['key'] != key:
            st.update(armed=False, pull_start=None, pull_extreme=None, key=key if valid else None, side=d)
        allowed = pos is None and i - last_exit >= p.cooldown and not paused and day_entries < p.max_daily and equity > 0
        candidate = None
        if valid and allowed:
            edge = zg if d == 1 else zd
            outside = r['c'] > edge + tol if d == 1 else r['c'] < edge - tol
            touched = r['l'] <= edge + tol if d == 1 else r['h'] >= edge - tol
            broke = r['c'] < edge - 0.25 * tol if d == 1 else r['c'] > edge + 0.25 * tol
            if st['pull_start'] is None:
                if outside:
                    st['armed'] = True
                elif st['armed'] and touched:
                    st['pull_start'] = i
                    st['pull_extreme'] = r['l'] if d == 1 else r['h']
                    counts['pullbacks'] += 1
            else:
                st['pull_extreme'] = min(st['pull_extreme'], r['l']) if d == 1 else max(st['pull_extreme'], r['h'])
                if broke or i - st['pull_start'] > p.max_pull:
                    counts['pull_broken' if broke else 'pull_timeout'] += 1
                    st.update(armed=False, pull_start=None, pull_extreme=None)
                else:
                    mature = i - st['pull_start'] >= p.min_pull
                    if d == 1:
                        confirm = r['c'] > r['o'] and r['c'] > r['prev_h'] and r['c'] > edge and r['c'] <= edge + 1.5 * tol
                    else:
                        confirm = r['c'] < r['o'] and r['c'] < r['prev_l'] and r['c'] < edge and r['c'] >= edge - 1.5 * tol
                    if mature and confirm:
                        candidate = (d, st['pull_extreme'])
                        st.update(armed=False, pull_start=None, pull_extreme=None)
                        counts['confirmations'] += 1
        if candidate:
            pending, why = plan(candidate[0], r, candidate[1], tol, equity, tick, step, min_qty, p)
            signals.append(dict(ts=int(r['close_time']), side=candidate[0], accepted=pending is not None, reject=why,
                                m15_known_at=int(r['m15_close_time']), h1_known_at=int(r['h1_close_time'])))
            if pending:
                day_entries += 1
                counts['submitted'] += 1
            else:
                counts['rejected_' + why] += 1
        if pos:
            s = pos['side']
            flip = p.h1_gate and r['h1_pos'] == ('below' if s == 1 else 'above')
            if paused or flip:
                close_pending = 'daily_risk' if paused else 'h1_flip'
    terminal = copy.deepcopy(pos)
    if pos and len(selected):
        last = selected.iloc[-1]
        close(float(last.c) - pos['side'] * tick * p.slippage_ticks, int(last.close_time), 'end_liquidation')
        curve[-1]['equity'] = cash
    wins = [t['net'] for t in trades if t['net'] > 0]
    losses = [t['net'] for t in trades if t['net'] < 0]
    eq = np.array([1000.] + [c['equity'] for c in curve])
    dd = 1 - eq / np.maximum.accumulate(eq)
    summary = dict(profile=asdict(p), start=iso(start), end=iso(end), trades=len(trades),
                   net_usdt=cash - 1000, return_pct=(cash / 1000 - 1) * 100,
                   win_pct=100 * len(wins) / len(trades) if trades else 0,
                   profit_factor=sum(wins) / abs(sum(losses)) if losses else None,
                   max_1m_close_drawdown_pct=float(dd.max() * 100),
                   average_win=float(np.mean(wins)) if wins else 0, average_loss=float(np.mean(losses)) if losses else 0,
                   fees=sum(t['entry_fee'] + t['exit_fee'] for t in trades), funding_estimate=sum(t['funding'] for t in trades),
                   exit_reasons=dict(Counter(t['reason'] for t in trades)), counters=dict(counts),
                   terminal_position_before_liquidation=terminal, unfilled_final_signal=pending,
                   note='1分钟收盘信号、下一分钟开盘成交、1分钟OHLC撮合；缠论状态按已收盘高周期因果重算；未建盘口/清算模型')
    return dict(summary=summary, trades=trades, equity=curve, signals=signals)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--data', type=Path, default=Path('var/eth_mtf_latest'))
    ap.add_argument('--output', type=Path, default=Path('var/eth_m1_results'))
    a = ap.parse_args()
    manifest = json.loads((a.data / 'manifest.json').read_text())
    if manifest['status'] != 'complete':
        raise ValueError('下载清单未完整，拒绝回测')
    data = {bar: json.loads((a.data / (bar + '.json')).read_text()) for bar in BARS}
    start, end = manifest['start_ms'], manifest['end_ms']
    crosscheck = crosscheck_timeframes(data)
    f = features(data, a.data.parent / 'eth_mtf_chan')
    funding = json.loads((a.data / 'funding.json').read_text())
    instrument = json.loads((a.data / 'instrument.json').read_text())[0]
    a.output.mkdir(parents=True, exist_ok=True)
    holdout = start + ((end - start) * 2 // 3) // 60000 * 60000
    summaries = []
    for p in profiles():
        for label, lo, hi in [('full', start, end), ('development', start, holdout), ('holdout', holdout, end)]:
            result = run(data, f, funding, instrument, p, lo, hi)
            save(a.output / (p.name + '_' + label + '.json'), result)
            s = result['summary']
            summaries.append(dict(split=label, **s))
            print('%-20s %-12s 笔数 %3d 净 %+7.2f 胜率 %5.1f%% PF %s 回撤 %.2f%% %s' % (
                p.name, label, s['trades'], s['net_usdt'], s['win_pct'],
                '%.2f' % s['profit_factor'] if s['profit_factor'] else '—', s['max_1m_close_drawdown_pct'], s['exit_reasons']), flush=True)
    save(a.output / 'summary.json', dict(data_manifest_sha256=hashlib.sha256((a.data / 'manifest.json').read_bytes()).hexdigest(),
                                         source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                                         runs=summaries, crosscheck=crosscheck,
                                         selection='4 profiles fixed before first run; chronological holdout starts flat'))


if __name__ == '__main__':
    main()
