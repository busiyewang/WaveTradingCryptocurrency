"""5分钟收盘决策、下一根开盘成交、1分钟撮合。独立研究镜像，非TradingView引擎。"""
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
    from .download_okx import BARS, quality, save, iso
except ImportError:
    from download_okx import BARS, quality, save, iso


@dataclass
class Profile:
    name: str = 'v3_fixed'
    h1_filter: bool = True
    h1_exit: bool = True
    h4_separation: float = 0.3
    max_daily: int = 6
    cooldown: int = 6
    risk_pct: float = 0.25
    fee: float = 0.0005
    slippage_ticks: int = 2
    rr: float = 1.5
    # 缠论结构开关（默认关闭，原有配置行为不变）
    chan_h1_pos: bool = False   # 1小时收盘须在最近已知中枢外侧且与方向一致（三买/三卖区域）
    chan_h4_bi: bool = False    # 4小时最近完成笔方向须与交易方向一致
    chan_m5_fx: bool = False    # 5分钟回踩极值须为已知的包含处理后分型
    chan_h1_exit: bool = False  # 1小时收盘回到中枢内或另一侧则退出（替代1小时慢线失效）
    chan_h4_bsp: str = ''       # 'all'：4小时最近已知买卖点方向须一致；'23'：只看二三类点；'23a'：活跃范围内二三类点（Pine引擎口径）
    chan_h4_bsp_na: bool = False  # 与 chan_h4_bsp 搭配：改为只禁止反向，无买卖点时允许
    chan_h1_bsp_na: bool = False  # 1小时最近已知买卖点不得与方向相反（无买卖点允许）
    min_stop_pct: float = 0.25  # 最小止损距离 %（与 Pine 参数同名同义）
    breakeven_r: float = 0.0    # >0：浮盈达到该倍止损距离后把止损移到入场价（Pine 未实现，仅研究）


def chan_profiles():
    base = dict(h1_filter=True, h1_exit=True, h4_separation=0.3, max_daily=6, cooldown=6)
    return [Profile('v3_fixed'),
            Profile('chan_h1_pos', chan_h1_pos=True, **base),
            Profile('chan_h4_bi', chan_h4_bi=True, **base),
            Profile('chan_m5_fx', chan_m5_fx=True, **base),
            Profile('chan_h1_pos_h4_bi', chan_h1_pos=True, chan_h4_bi=True, **base),
            Profile('chan_all', chan_h1_pos=True, chan_h4_bi=True, chan_m5_fx=True, **base),
            Profile('chan_all_exit', chan_h1_pos=True, chan_h4_bi=True, chan_m5_fx=True, chan_h1_exit=True, **base),
            Profile('chan_h4_bsp', chan_h4_bsp='all', **base),
            Profile('chan_h4_bsp23', chan_h4_bsp='23', **base),
            Profile('chan_h4_bsp_h1_na', chan_h4_bsp='all', chan_h1_bsp_na=True, **base),
            Profile('chan_h4_bsp23_h1_na', chan_h4_bsp='23', chan_h1_bsp_na=True, **base),
            Profile('chan_h4_bsp_h1_pos', chan_h4_bsp='all', chan_h1_pos=True, **base),
            Profile('chan_h4_bsp23a', chan_h4_bsp='23a', **base),
            Profile('chan_h4_bsp23a_na', chan_h4_bsp='23a', chan_h4_bsp_na=True, **base),
            Profile('chan_h4_bsp23_na', chan_h4_bsp='23', chan_h4_bsp_na=True, **base)]


def profiles():
    return [Profile('v2_reference', False, False, 0.0, 20, 3),
            Profile('h1_filter_only', True, False, 0.0, 20, 3),
            Profile(), Profile('v3_double_cost', fee=0.001, slippage_ticks=4)]


def indicators(rows):
    f = pd.DataFrame(rows).copy()
    for n in (20, 60):
        f['ema'+str(n)] = f.c.ewm(span=n, adjust=False).mean()
    tr = pd.concat([f.h-f.l, (f.h-f.c.shift()).abs(), (f.l-f.c.shift()).abs()], axis=1).max(axis=1)
    atr = np.full(len(f), np.nan)
    if len(f) >= 14:
        atr[13] = tr.iloc[:14].mean()
        for i in range(14, len(f)):
            atr[i] = (13*atr[i-1] + tr.iloc[i])/14
    f['atr'] = atr
    f['prev_fast'] = f.ema20.shift()
    f['index'] = np.arange(len(f))
    return f


def chan_columns(data, cache_dir):
    """高周期缠论因果状态列 + 5分钟已知分型列（只用历史，known_idx 之后才可见）。"""
    try:
        from .chan_features import causal_states
    except ImportError:
        from chan_features import causal_states
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from chan_strokes import merge_klines, find_fenxing
    out = {}
    for bar, prefix in [('4H', 'h4'), ('1H', 'h1')]:
        st = causal_states(data[bar], bar, cache_dir)
        out[prefix] = pd.DataFrame([dict(close_time=s.get('close_time', data[bar][j]['close_time']),
                                         pos=s.get('pos', 'none'), zg=s.get('zg'), zd=s.get('zd'),
                                         bi_dir=s.get('bi_dir'), bsp=s.get('bsp'), bsp23=s.get('bsp23'), bsp23a=s.get('bsp23a')) for j, s in enumerate(st)])
        out[prefix].columns = [prefix + '_chan_' + c if c != 'close_time' else prefix + '_close_time' for c in out[prefix].columns]
    m5 = data['5m']
    fx = find_fenxing(merge_klines(m5), m5)
    n = len(m5)
    bot_idx, bot_px, top_idx, top_px = [-1]*n, [np.nan]*n, [-1]*n, [np.nan]*n
    events = sorted(fx, key=lambda f: f['known_idx'])
    e = 0
    cur = {'bottom': (-1, np.nan), 'top': (-1, np.nan)}
    for i in range(n):
        while e < len(events) and events[e]['known_idx'] <= i:
            cur[events[e]['type']] = (events[e]['k_idx'], events[e]['price'])
            e += 1
        bot_idx[i], bot_px[i] = cur['bottom']
        top_idx[i], top_px[i] = cur['top']
    out['m5'] = pd.DataFrame(dict(fx_bot_idx=bot_idx, fx_bot_px=bot_px, fx_top_idx=top_idx, fx_top_px=top_px))
    return out


def features(data, chan=None):
    f = indicators(data['5m'])
    if chan is not None:
        f = pd.concat([f, chan['m5']], axis=1)
    f['prev_h'] = f.h.shift()
    f['prev_l'] = f.l.shift()
    f['prev_c'] = f.c.shift()
    f['prev_slow'] = f.ema60.shift()
    for bar, prefix in [('4H','h4'), ('1H','h1')]:
        ht = indicators(data[bar])
        cols = ['close_time','c','ema20','ema60','prev_fast','atr','index']
        ht = ht[cols].rename(columns={c:prefix+'_'+c for c in cols})
        # HTF在5m开盘时已经收盘才可见，与[1]+lookahead_on一致。
        f = pd.merge_asof(f.sort_values('ts'), ht.sort_values(prefix+'_close_time'),
                          left_on='ts', right_on=prefix+'_close_time', direction='backward')
        if chan is not None:
            f = pd.merge_asof(f, chan[prefix].sort_values(prefix+'_close_time'),
                              left_on='ts', right_on=prefix+'_close_time', direction='backward', suffixes=('', '_dup'))
    return f


def direction(r, p):
    if r['h4_index'] < 180 or not math.isfinite(r['h4_prev_fast']):
        return 0
    bull = r['h4_ema20'] > r['h4_ema60'] and r['h4_c'] > r['h4_ema60'] and r['h4_ema20'] > r['h4_prev_fast']
    bear = r['h4_ema20'] < r['h4_ema60'] and r['h4_c'] < r['h4_ema60'] and r['h4_ema20'] < r['h4_prev_fast']
    if abs(r['h4_ema20']-r['h4_ema60']) < p.h4_separation*r['h4_atr']:
        return 0
    side = 1 if bull else -1 if bear else 0
    if p.h1_filter:
        if r['h1_index'] < 300 or not math.isfinite(r['h1_prev_fast']):
            return 0
        if side == 1 and not (r['h1_ema20'] > r['h1_ema60'] and r['h1_c'] > r['h1_ema60'] and r['h1_ema20'] > r['h1_prev_fast']):
            return 0
        if side == -1 and not (r['h1_ema20'] < r['h1_ema60'] and r['h1_c'] < r['h1_ema60'] and r['h1_ema20'] < r['h1_prev_fast']):
            return 0
    if p.chan_h1_pos and r.get('h1_chan_pos') != ('above' if side == 1 else 'below'):
        return 0
    if p.chan_h4_bi and r.get('h4_chan_bi_dir') != ('up' if side == 1 else 'down'):
        return 0
    if p.chan_h4_bsp:
        t = r.get({'all': 'h4_chan_bsp', '23': 'h4_chan_bsp23', '23a': 'h4_chan_bsp23a'}[p.chan_h4_bsp])
        if isinstance(t, str):
            if t[0] != ('B' if side == 1 else 'S'):
                return 0
        elif not p.chan_h4_bsp_na:
            return 0
    if p.chan_h1_bsp_na:
        t = r.get('h1_chan_bsp')
        if isinstance(t, str) and t[0] != ('B' if side == 1 else 'S'):
            return 0
    return side


def protective_fill(pos, bar, tick, slip):
    s, stop, target = pos['side'], pos['stop'], pos['target']
    stop_hit = bar['l'] <= stop if s == 1 else bar['h'] >= stop
    # 限价要求穿透1 tick。开盘有利跳价仍保守按原目标成交。
    target_hit = bar['h'] >= target+tick if s == 1 else bar['l'] <= target-tick
    if s*(bar['o']-stop) <= 0:
        return bar['o']-s*slip, 'stop_gap', int(stop_hit and target_hit)
    if s*(bar['o']-target) >= tick:
        return target, 'target', 0
    if stop_hit:
        return stop-s*slip, 'stop', int(target_hit)
    if target_hit:
        return target, 'target', 0
    return None


def order_plan(side, extreme, r, equity, tick, step, min_qty, p):
    slip = tick*p.slippage_ticks
    entry = r['c']+side*slip
    mindist = max(r['atr'], entry*p.min_stop_pct/100)
    stop = min(extreme-r['atr']*0.2, entry-mindist) if side==1 else max(extreme+r['atr']*0.2, entry+mindist)
    stop = (math.floor(stop/tick) if side==1 else math.ceil(stop/tick))*tick
    distance = side*(entry-stop)
    loss = distance+slip+p.fee*(entry+stop-side*slip)
    target = entry+side*(p.rr*loss+2*p.fee*entry)/(1-side*p.fee)
    target = (math.ceil(target/tick) if side==1 else math.floor(target/tick))*tick
    qty = math.floor(min(equity*p.risk_pct/100/loss, equity*0.99/entry)/step)*step if loss>0 else 0
    if min(stop,target)<=0 or distance/entry>0.015 or qty<min_qty:
        return None
    return dict(side=side, stop=stop, target=target, qty=qty, signal_ts=int(r['close_time']),
                planned_entry=entry, planned_risk=loss*qty)


def run(data, f, funding, instrument, p, start, end):
    tick = float(instrument['tickSz'])
    if instrument['ctValCcy'] != 'ETH' or instrument['settleCcy'] != 'USDT' or instrument['ctType'] != 'linear':
        raise ValueError('只支持ETH线性USDT永续')
    ctval = float(instrument['ctVal']) * float(instrument.get('ctMult') or 1)
    step = ctval*float(instrument['lotSz'])
    min_qty = ctval*float(instrument['minSz'])
    minutes = {r['ts']: r for r in data['1m']}
    rates = {int(r['fundingTime']): float(r.get('realizedRate') or r['fundingRate']) for r in funding}
    cash, pos, pending, close_pending = 1000.0, None, None, None
    phase = side = 0
    setup_i = pull_i = last_exit = -100000
    extreme = 0.0
    day, day_equity, day_entries, paused = None, 1000.0, 0, False
    last_equity = 1000.0
    trades, curve, signals = [], [], []
    counts = Counter()

    def close(price, ts, reason):
        nonlocal cash, pos, last_exit
        exit_fee = pos['qty']*price*p.fee
        gross = pos['qty']*pos['side']*(price-pos['entry'])
        cash += gross-exit_fee
        pos.update(exit=price, exit_ts=int(ts), reason=reason, gross=gross,
                   exit_fee=exit_fee, net=gross-pos['entry_fee']-exit_fee-pos['funding'])
        trades.append(pos)
        pos = None
        last_exit = i

    selected = f[(f.ts>=start)&(f.close_time<=end)]
    for r in selected.to_dict('records'):
        i = int(r['index'])
        # 一根5m内的5根真实1m，缺失直接拒绝。
        for j in range(5):
            t = int(r['ts'])+j*60000
            m = minutes[t]
            if pos is not None and t in rates:
                cost = pos['side']*pos['qty']*m['o']*rates[t]
                cash -= cost
                pos['funding'] += cost
            if j==0:
                if pos is not None and close_pending:
                    close(m['o']-pos['side']*tick*p.slippage_ticks,t,close_pending)
                close_pending = None
                if pending:
                    entry = m['o']+pending['side']*tick*p.slippage_ticks
                    fee = entry*pending['qty']*p.fee
                    if entry*pending['qty']+fee <= cash:
                        pos = dict(pending, entry=entry, entry_ts=t, entry_fee=fee, funding=0.0)
                        cash -= fee
                    else:
                        counts['margin_rejected'] += 1
                    pending = None
            if pos is not None:
                if p.breakeven_r>0 and not pos.get('be'):
                    dist0 = pos['side']*(pos['entry']-pos['stop'])
                    reached = m['h']>=pos['entry']+p.breakeven_r*dist0 if pos['side']==1 else m['l']<=pos['entry']-p.breakeven_r*dist0
                    if reached:
                        pos['stop']=pos['entry']; pos['be']=True
                fill = protective_fill(pos,m,tick,tick*p.slippage_ticks)
                if fill:
                    counts['ambiguous_minutes'] += fill[2]
                    close(fill[0],t+60000,fill[1])
            equity = cash + (pos['qty']*pos['side']*(m['c']-pos['entry']) if pos else 0)
            curve.append(dict(ts=t+60000,equity=equity))
        equity = curve[-1]['equity']
        current_day = int(r['ts'])//86400000
        if current_day != day:
            day, day_equity, day_entries, paused = current_day,last_equity,0,False
        paused = paused or (day_equity-equity)/day_equity >= 0.02
        last_equity = equity
        d = direction(r,p)
        allowed = i>=180 and pos is None and i-last_exit>=p.cooldown and not paused and day_entries<p.max_daily and equity>0
        candidate, candidate_extreme = 0, None
        if not allowed:
            phase=side=0
        else:
            if phase:
                invalid = (r['ema20']<=r['ema60'] or r['c']<=r['ema60']) if side==1 else (r['ema20']>=r['ema60'] or r['c']>=r['ema60'])
                if d!=side or invalid or i-setup_i>36 or (phase==2 and i-pull_i>12):
                    phase=side=0
                    counts['invalidated']+=1
            if phase==0:
                impulse = d==1 and r['ema20']>r['ema60'] and r['c']>r['ema20']+0.5*r['atr']
                impulse_s = d==-1 and r['ema20']<r['ema60'] and r['c']<r['ema20']-0.5*r['atr']
                if impulse or impulse_s:
                    side=1 if impulse else -1
                    phase, setup_i=1,i
                    counts['setups']+=1
            elif phase==1:
                touch = r['l']<=r['ema20'] if side==1 else r['h']>=r['ema20']
                if touch:
                    phase,pull_i=2,i
                    extreme=r['l'] if side==1 else r['h']
            elif phase==2:
                extreme=min(extreme,r['l']) if side==1 else max(extreme,r['h'])
                confirm = (r['c']>r['ema20'] and r['c']>r['prev_h'] and r['c']>r['o']) if side==1 else (r['c']<r['ema20'] and r['c']<r['prev_l'] and r['c']<r['o'])
                if p.chan_m5_fx and confirm:
                    # 回踩极值必须已经是可知的分型极值（分型右侧K线已出现）
                    fx_i, fx_p = (r['fx_bot_idx'], r['fx_bot_px']) if side==1 else (r['fx_top_idx'], r['fx_top_px'])
                    if not (fx_i >= pull_i and fx_p == extreme):
                        confirm = False
                        counts['fx_not_ready'] += 1
                if i-pull_i>=2 and confirm:
                    candidate,candidate_extreme=side,extreme
                    counts['confirmations']+=1
                    phase=side=0
        if candidate:
            pending=order_plan(candidate,candidate_extreme,r,equity,tick,step,min_qty,p)
            signals.append(dict(ts=int(r['close_time']),side=candidate,accepted=pending is not None,
                                h4_known_at=int(r['h4_close_time']),h1_known_at=int(r['h1_close_time'])))
            if pending:
                day_entries+=1
                counts['submitted']+=1
            else:
                counts['risk_rejected']+=1
        if pos:
            s=pos['side']
            reverse = (r['h4_ema20']<r['h4_ema60'] and r['h4_c']<r['h4_ema60'] and r['h4_ema20']<r['h4_prev_fast']) if s==1 else (r['h4_ema20']>r['h4_ema60'] and r['h4_c']>r['h4_ema60'] and r['h4_ema20']>r['h4_prev_fast'])
            local = (r['c']<r['ema60'] and r['prev_c']<r['prev_slow']) if s==1 else (r['c']>r['ema60'] and r['prev_c']>r['prev_slow'])
            h1failure = r['h1_c']<r['h1_ema60'] if s==1 else r['h1_c']>r['h1_ema60']
            if p.chan_h1_exit:
                h1failure = r.get('h1_chan_pos') != ('above' if s==1 else 'below')
            if paused or reverse or (h1failure if p.h1_exit else local):
                close_pending='daily_risk' if paused else 'h4_reverse' if reverse else ('h1_chan_exit' if p.chan_h1_exit else 'h1_failure') if p.h1_exit else 'm5_failure'
    terminal = copy.deepcopy(pos)
    if pos and len(selected):
        last=selected.iloc[-1]
        close(float(last.c)-pos['side']*tick*p.slippage_ticks,int(last.close_time),'end_liquidation')
        curve[-1]['equity']=cash
    wins = [t['net'] for t in trades if t['net']>0]
    losses = [t['net'] for t in trades if t['net']<0]
    eq=np.array([1000.]+[r['equity'] for r in curve])
    dd=1-eq/np.maximum.accumulate(eq)
    summary=dict(profile=asdict(p),start=iso(start),end=iso(end),trades=len(trades),
        net_usdt=cash-1000,return_pct=(cash/1000-1)*100,win_pct=100*len(wins)/len(trades) if trades else 0,
        profit_factor=sum(wins)/abs(sum(losses)) if losses else None,
        max_1m_close_drawdown_pct=float(dd.max()*100),
        average_win=float(np.mean(wins)) if wins else 0,average_loss=float(np.mean(losses)) if losses else 0,
        fees=sum(t['entry_fee']+t['exit_fee'] for t in trades),funding_estimate=sum(t['funding'] for t in trades),
        exit_reasons=dict(Counter(t['reason'] for t in trades)),counters=dict(counts),
        terminal_position_before_liquidation=terminal,unfilled_final_signal=pending,
        note='独立Python镜像；资金费按1m成交开盘价估值，非结算标记价；未建清算/盘口/延迟模型；当前合约规格用于全历史')
    return dict(summary=summary,trades=trades,equity=curve,signals=signals)



def crosscheck_timeframes(data):
    """独立接口的重叠区间必须能由完整1分钟OHLCV聚合重现。"""
    m = pd.DataFrame(data['1m']).set_index('ts')
    results = {}
    for bar in ('5m', '15m', '1H', '4H'):
        groups = m.groupby(m.index // BARS[bar] * BARS[bar])
        agg = groups.agg(o=('o','first'), h=('h','max'), l=('l','min'), c=('c','last'),
                         vol=('vol','sum'), n=('c','size'))
        agg = agg[agg.n == BARS[bar] // 60000]
        high = pd.DataFrame(data[bar]).set_index('ts')
        common = high.index.intersection(agg.index)
        if len(common) == 0:
            raise ValueError('周期之间无完整重叠: ' + bar)
        for field in ('o','h','l','c','vol'):
            if not np.allclose(high.loc[common,field],agg.loc[common,field],rtol=1e-8,atol=1e-7):
                raise ValueError('1分钟聚合与独立接口不符: ' + bar + ' ' + field)
        results[bar] = {'matched_bars': len(common), 'status': 'matched'}
    return results

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--data',type=Path,default=Path('var/eth_mtf'))
    ap.add_argument('--output',type=Path,default=None)
    ap.add_argument('--chan',action='store_true',help='运行缠论结构开关配置组（输出默认 var/eth_mtf_chan_results）')
    a=ap.parse_args()
    if a.output is None:
        a.output=Path('var/eth_mtf_chan_results' if a.chan else 'var/eth_mtf_results')
    manifest=json.loads((a.data/'manifest.json').read_text())
    if manifest['status']!='complete':
        raise ValueError('下载清单未完整，拒绝回测')
    for name,digest in manifest['sha256'].items():
        if hashlib.sha256((a.data/name).read_bytes()).hexdigest()!=digest:
            raise ValueError('数据哈希不符: '+name)
    data={bar:json.loads((a.data/(bar+'.json')).read_text()) for bar in BARS}
    start,end=manifest['start_ms'],manifest['end_ms']
    for bar, rows in data.items():
        warm=300*BARS[bar] if bar in ('4H','1H') else 0
        if quality(rows,bar,start-warm,end)['status']!='complete':
            raise ValueError('数据缺口: '+bar)
    crosscheck=crosscheck_timeframes(data)
    chan=chan_columns(data, a.data.parent/'eth_mtf_chan') if a.chan else None
    f=features(data, chan)
    funding=json.loads((a.data/'funding.json').read_text())
    if not funding:
        raise ValueError('缺资金费，拒绝给出永续净收益')
    instrument=json.loads((a.data/'instrument.json').read_text())[0]
    a.output.mkdir(parents=True,exist_ok=True)
    # 在看结果前固定4种配置，不作网格挑选。末1/3为一次性时间留出。
    holdout=start+((end-start)*2//3)//300000*300000
    summaries=[]
    for p in (chan_profiles() if a.chan else profiles()):
        for label,lo,hi in [('full',start,end),('development',start,holdout),('holdout',holdout,end)]:
            result=run(data,f,funding,instrument,p,lo,hi)
            save(a.output/(p.name+'_'+label+'.json'),result)
            summaries.append(dict(split=label,**result['summary']))
            print(p.name,label,'trades',result['summary']['trades'],'net',round(result['summary']['net_usdt'],3),'PF',result['summary']['profit_factor'],flush=True)
    save(a.output/'summary.json',dict(data_manifest_sha256=hashlib.sha256((a.data/'manifest.json').read_bytes()).hexdigest(),
        source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        rule_ids=['D01','D02','D03','B01','B02','B03','B04'],runs=summaries,crosscheck=crosscheck,
        selection='4 profiles fixed before first run; no optimized winner; chronological holdout starts flat'))


if __name__=='__main__':
    main()
