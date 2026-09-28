"""方案3：日线/周线缠论定方向、4小时中枢定网格区间、挂单网格交易；1分钟撮合。规则在首次运行前固定。"""
import argparse
from collections import Counter
import copy
from dataclasses import dataclass, asdict
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from .download_okx import BARS, save, iso
    from .backtest import crosscheck_timeframes
    from .chan_features import causal_states
except ImportError:
    from download_okx import BARS, save, iso
    from backtest import crosscheck_timeframes
    from chan_features import causal_states


@dataclass
class Profile:
    name: str = 'grid_a'
    weekly_gate: bool = True     # 周线中枢反向位置阻止
    daily_gate: bool = True      # 日线中枢位置定方向，最近买卖点不得反向
    step_pct: float = 0.6        # 网格最小步长 %（按中枢等分，实际步长≥此值）
    min_intervals: int = 4       # 中枢至少能放多少格
    cap: float = 1.0             # 网格总名义/权益上限
    maker: float = 0.0002
    taker: float = 0.0005
    slippage_ticks: int = 2
    day_loss: float = 0.03       # 日初权益回撤超过则暂停新挂单
    reopen: bool = False         # v2：中枢破坏后，4小时收盘回到区间内则允许重开（看过v1结果后的唯一修正）


def profiles():
    return [Profile('grid_a'),
            Profile('grid_b_noweek', weekly_gate=False),
            Profile('grid_c_step1', step_pct=1.0),
            Profile('grid_d_taker', maker=0.0005),
            Profile('grid_v2_reopen', reopen=True),
            Profile('grid_v2_reopen_step1', reopen=True, step_pct=1.0)]


def features(data, cache_dir):
    f = pd.DataFrame(data['1m']).copy()
    f['index'] = np.arange(len(f))
    for bar, prefix in [('4H', 'h4'), ('1D', 'd1'), ('1W', 'w1')]:
        rows = data[bar]
        st = causal_states(rows, bar, cache_dir)
        ht = pd.DataFrame(rows)[['close_time', 'c']].copy()
        for k in ('zg', 'zd', 'pos', 'bsp'):
            ht[k] = [s.get(k) for s in st]
        ht.columns = [prefix + '_' + c for c in ht.columns]
        f = pd.merge_asof(f.sort_values('ts'), ht.sort_values(prefix + '_close_time'),
                          left_on='ts', right_on=prefix + '_close_time', direction='backward')
    return f


def gate(r, p):
    d = 0
    if p.daily_gate:
        d = 1 if r['d1_pos'] == 'above' else -1 if r['d1_pos'] == 'below' else 0
        b = r['d1_bsp']
        if d and isinstance(b, str) and b[0] != ('B' if d == 1 else 'S'):
            d = 0
    else:
        d = 1 if r['h4_pos'] == 'above' else -1 if r['h4_pos'] == 'below' else 0
    if d and p.weekly_gate:
        if (d == 1 and r['w1_pos'] == 'below') or (d == -1 and r['w1_pos'] == 'above'):
            d = 0
    return d


def run(data, f, funding, instrument, p, start, end):
    tick = float(instrument['tickSz'])
    ctval = float(instrument['ctVal']) * float(instrument.get('ctMult') or 1)
    step = ctval * float(instrument['lotSz'])
    min_qty = ctval * float(instrument['minSz'])
    slip = tick * p.slippage_ticks
    rates = {int(r['fundingTime']): float(r.get('realizedRate') or r['fundingRate']) for r in funding}
    cash = 1000.0
    lots = []            # 持仓格：side, entry, qty, line, target, key, zd, zg, entry_ts, fees, funding
    orders = []          # 下一分钟生效的开仓挂单：side, price, line, qty, key, zd, zg
    market_close = None  # (reason, key or None)
    blocked = set()      # 已止损的中枢，不再重开
    trades, curve = [], []
    counts = Counter()
    day, day_equity, paused, last_equity = None, 1000.0, False, 1000.0
    prev_h4_close_time = None
    max_lots, max_expo = 0, 0.0

    def close_lot(lot, price, ts, reason, fee_rate):
        nonlocal cash
        fee = lot['qty'] * price * fee_rate
        gross = lot['side'] * lot['qty'] * (price - lot['entry'])
        cash += gross - fee
        lot.update(exit=price, exit_ts=int(ts), reason=reason, gross=gross, exit_fee=fee,
                   net=gross - lot['entry_fee'] - fee - lot['funding'])
        trades.append(lot)

    selected = f[(f.ts >= start) & (f.close_time <= end)]
    for r in selected.to_dict('records'):
        t = int(r['ts'])
        o, h, l, c = r['o'], r['h'], r['l'], r['c']
        # ---- 资金费
        if t in rates:
            for lot in lots:
                cost = lot['side'] * lot['qty'] * o * rates[t]
                cash -= cost
                lot['funding'] += cost
        # ---- 开盘：市价清仓
        if market_close and lots:
            reason, key = market_close
            keep = []
            for lot in lots:
                if key is None or lot['key'] == key:
                    close_lot(lot, o - lot['side'] * slip, t, reason, p.taker)
                else:
                    keep.append(lot)
            lots = keep
        market_close = None
        # ---- 平仓挂单成交（本分钟开始前已有的格）
        keep = []
        for lot in lots:
            T = lot['target']
            filled = None
            if lot['side'] == 1:
                if o >= T:
                    filled = o
                elif h >= T + tick:
                    filled = T
            else:
                if o <= T:
                    filled = o
                elif l <= T - tick:
                    filled = T
            if filled is not None:
                close_lot(lot, filled, t + 60000, 'grid_take', p.maker)
                counts['grid_take'] += 1
            else:
                keep.append(lot)
        lots = keep
        # ---- 开仓挂单成交
        for od in orders:
            P = od['price']
            filled = None
            if od['side'] == 1:
                if o <= P:
                    filled = o
                elif l <= P - tick:
                    filled = P
            else:
                if o >= P:
                    filled = o
                elif h >= P + tick:
                    filled = P
            if filled is not None:
                fee = od['qty'] * filled * p.maker
                cash -= fee
                lots.append(dict(side=od['side'], entry=filled, qty=od['qty'], line=od['line'], target=od['target'],
                                 key=od['key'], zd=od['zd'], zg=od['zg'], entry_ts=t, entry_fee=fee, funding=0.0))
                counts['grid_fill'] += 1
        orders = []
        unreal = sum(lot['side'] * lot['qty'] * (c - lot['entry']) for lot in lots)
        equity = cash + unreal
        curve.append(dict(ts=t + 60000, equity=equity))
        expo = sum(lot['qty'] * c for lot in lots)
        max_lots = max(max_lots, len(lots)); max_expo = max(max_expo, expo / equity if equity > 0 else 0)
        current_day = t // 86400000
        if current_day != day:
            day, day_equity, paused = current_day, last_equity, False
        paused = paused or (day_equity - equity) / day_equity >= p.day_loss
        last_equity = equity

        # ---- 收盘：4小时新收盘 → 中枢破坏检查
        if r['h4_close_time'] != prev_h4_close_time:
            prev_h4_close_time = r['h4_close_time']
            hc = r['h4_c']
            if p.reopen:
                for key in list(blocked):
                    if key[2] <= hc <= key[1]:
                        blocked.discard(key)
                        counts['reopened'] += 1
            for key in {lot['key'] for lot in lots}:
                sample = next(lot for lot in lots if lot['key'] == key)
                broken = hc < sample['zd'] if sample['side'] == 1 else hc > sample['zg']
                if broken:
                    market_close = ('center_break', key)
                    blocked.add(key)
                    counts['center_break'] += 1
        d = gate(r, p)
        for lot in lots:
            if d == -lot['side'] and not market_close:
                market_close = ('gate_flip', None)
                counts['gate_flip'] += 1
                break
        zg, zd = r['h4_zg'], r['h4_zd']
        valid = d != 0 and isinstance(zg, float) and math.isfinite(zg) and zg > zd and zd <= c <= zg and equity > 0
        if valid and not paused and not market_close:
            key = (d, zg, zd)
            if key in blocked:
                counts['blocked_center'] += 1
            else:
                mid = (zg + zd) / 2
                n = int((zg - zd) / (p.step_pct / 100 * mid))
                if n < p.min_intervals:
                    counts['center_too_small'] += 1
                else:
                    width = (zg - zd) / n
                    lines = [zd + k * width for k in range(n + 1)]
                    occupied = {lot['line'] for lot in lots if lot['key'] == key}
                    per_lot_notional = p.cap * equity / n
                    for k in range(n + 1):
                        P = lines[k]
                        if d == 1 and k <= n - 1 and P < c and k not in occupied:
                            qty = math.floor(per_lot_notional / P / step) * step
                            if qty >= min_qty:
                                orders.append(dict(side=1, price=P, line=k, target=lines[k + 1], qty=qty, key=key, zd=zd, zg=zg))
                        if d == -1 and k >= 1 and P > c and k not in occupied:
                            qty = math.floor(per_lot_notional / P / step) * step
                            if qty >= min_qty:
                                orders.append(dict(side=-1, price=P, line=k, target=lines[k - 1], qty=qty, key=key, zd=zd, zg=zg))
                    if orders:
                        counts['bars_with_orders'] += 1
    terminal = copy.deepcopy(lots)
    if lots and len(selected):
        last = selected.iloc[-1]
        for lot in lots:
            close_lot(lot, float(last.c) - lot['side'] * slip, int(last.close_time), 'end_liquidation', p.taker)
        lots = []
        curve[-1]['equity'] = cash
    wins = [x['net'] for x in trades if x['net'] > 0]
    losses = [x['net'] for x in trades if x['net'] < 0]
    eq = np.array([1000.] + [x['equity'] for x in curve])
    dd = 1 - eq / np.maximum.accumulate(eq)
    summary = dict(profile=asdict(p), start=iso(start), end=iso(end), lots_closed=len(trades),
                   net_usdt=cash - 1000, return_pct=(cash / 1000 - 1) * 100,
                   win_pct=100 * len(wins) / len(trades) if trades else 0,
                   profit_factor=sum(wins) / abs(sum(losses)) if losses else None,
                   max_1m_close_drawdown_pct=float(dd.max() * 100),
                   fees=sum(x['entry_fee'] + x['exit_fee'] for x in trades), funding_estimate=sum(x['funding'] for x in trades),
                   exit_reasons=dict(Counter(x['reason'] for x in trades)), counters=dict(counts),
                   max_concurrent_lots=max_lots, max_notional_over_equity=round(max_expo, 3),
                   terminal_lots_before_liquidation=terminal,
                   note='挂单按1分钟穿透1tick成交，开盘更优价按开盘价；市价清仓按吃单费+滑点；未建盘口排队/清算模型')
    return dict(summary=summary, trades=trades, equity=curve)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--data', type=Path, default=Path('var/eth_mtf_latest'))
    ap.add_argument('--output', type=Path, default=Path('var/eth_grid_results'))
    a = ap.parse_args()
    manifest = json.loads((a.data / 'manifest.json').read_text())
    if manifest['status'] != 'complete':
        raise ValueError('下载清单未完整，拒绝回测')
    data = {bar: json.loads((a.data / (bar + '.json')).read_text()) for bar in list(BARS) + ['1D', '1W']}
    start, end = manifest['start_ms'], manifest['end_ms']
    crosscheck = crosscheck_timeframes({b: data[b] for b in BARS})
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
            print('%-16s %-12s 格数 %3d 净 %+7.2f 胜率 %5.1f%% PF %s 回撤 %.2f%% 最多持仓格 %d 最大名义/权益 %.2f %s' % (
                p.name, label, s['lots_closed'], s['net_usdt'], s['win_pct'],
                '%.2f' % s['profit_factor'] if s['profit_factor'] else '—', s['max_1m_close_drawdown_pct'],
                s['max_concurrent_lots'], s['max_notional_over_equity'], s['exit_reasons']), flush=True)
    save(a.output / 'summary.json', dict(data_manifest_sha256=hashlib.sha256((a.data / 'manifest.json').read_bytes()).hexdigest(),
                                         source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                                         runs=summaries, crosscheck=crosscheck,
                                         selection='4 profiles fixed before first run; chronological holdout starts flat'))


if __name__ == '__main__':
    main()
