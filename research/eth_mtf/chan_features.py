"""高周期缠论因果特征：每根已收盘K线只用其之前的固定窗口重算，不读未来。

复用项目 chan.analyze（笔/中枢/背驰/买卖点）。窗口默认600根，与网页研究窗口一致；
窗口左端滑动会改变初始结构，这里按"当时可见"的结论记录，允许后来被修正。
"""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from chan import analyze  # noqa: E402
from decision import active_signal_cutoff  # noqa: E402

WINDOW = 600


def _state(result, close):
    zss = result['zhongshu_display']
    bis = [b for b in result['bi'] if not b.get('unfinished')]
    st = dict(zg=None, zd=None, gg=None, dd=None, zs_bi_end=None, zs_end_idx=None, pos='none',
              bi_dir=None, bi_end_idx=None, bi_locked=None,
              bc_dir=None, bc_kind=None, bc_idx=None,
              bl_dir=None, bl_idx=None, bsp=None, bsp_idx=None, bsp23=None, bsp23_idx=None, bsp23a=None, bsp23a_idx=None)
    if zss:
        z = zss[-1]
        st.update(zg=z['zg'], zd=z['zd'], gg=z.get('gg'), dd=z.get('dd'), zs_bi_end=z['bi_end'], zs_end_idx=z['end_idx'])
        st['pos'] = 'above' if close > z['zg'] else 'below' if close < z['zd'] else 'inside'
    if bis:
        b = bis[-1]
        st.update(bi_dir=b['dir'], bi_end_idx=b['end_idx'], bi_locked=bool(b.get('locked')))
    if result['beichi']:
        bc = result['beichi'][-1]
        st.update(bc_dir=bc['dir'], bc_kind=bc['kind'], bc_idx=bc['k_idx'])
    if result['beili']:
        bl = result['beili'][-1]
        st.update(bl_dir='down' if bl['type'] == 'DB' else 'up', bl_idx=bl['k_idx'])
    if result['bsp']:
        p = result['bsp'][-1]
        st.update(bsp=p['type'], bsp_idx=p['k_idx'])
        p23 = [q for q in result['bsp'] if q['type'][1] in '23']
        if p23:
            st.update(bsp23=p23[-1]['type'], bsp23_idx=p23[-1]['k_idx'])
            # Pine 引擎口径：只保留活跃范围内（最新锁定笔端点起）的二三类点
            cutoff = active_signal_cutoff(bis)
            act = [q for q in p23 if q['k_idx'] >= cutoff]
            if act:
                st.update(bsp23a=act[-1]['type'], bsp23a_idx=act[-1]['k_idx'])
    return st


def causal_states(rows, bar, cache_dir=None, window=WINDOW):
    """rows: 升序已收盘K线。返回与 rows 等长的列表，第 j 项只依赖 rows[:j+1]。"""
    key = None
    if cache_dir is not None:
        digest = hashlib.sha256(json.dumps([(r['ts'], r['c']) for r in rows]).encode()).hexdigest()[:16]
        key = Path(cache_dir) / ('chan_v4_%s_%d_%s.json' % (bar, window, digest))
        if key.exists():
            return json.loads(key.read_text())
    out = []
    for j in range(len(rows)):
        if j < 30:
            out.append(_state({'zhongshu_display': [], 'bi': [], 'beichi': [], 'beili': [], 'bsp': []}, rows[j]['c']))
            continue
        lo = max(0, j + 1 - window)
        seg = [dict(r, close_ts=r['close_time']) for r in rows[lo:j + 1]]
        res = analyze(seg, bar)
        st = _state(res, rows[j]['c'])
        # 索引换算回全序列
        for k in ('zs_end_idx', 'bi_end_idx', 'bc_idx', 'bl_idx', 'bsp_idx', 'bsp23_idx', 'bsp23a_idx'):
            if st[k] is not None:
                st[k] += lo
        st['idx'] = j
        st['close_time'] = rows[j]['close_time']
        out.append(st)
    if key is not None:
        key.parent.mkdir(parents=True, exist_ok=True)
        key.write_text(json.dumps(out))
    return out


if __name__ == '__main__':
    import time
    data_dir = ROOT / 'var/eth_mtf'
    for bar in ('4H', '1H'):
        rows = json.load(open(data_dir / (bar + '.json')))
        t = time.time()
        st = causal_states(rows, bar, ROOT / 'var/eth_mtf_chan')
        print(bar, len(st), '%.1fs' % (time.time() - t), st[-1])
