"""补充下载 OKX ETH-USDT-SWAP 日线/周线（UTC 对齐 1Dutc/1Wutc），供大级别缠论方向使用。"""
import argparse
import json
import math
from pathlib import Path

try:
    from . import download_okx as dl
    from .download_okx import Client, save, iso
except ImportError:
    import download_okx as dl
    from download_okx import Client, save, iso

STEP = {'1D': 86400000, '1W': 604800000}
OFFSET = {'1D': 0, '1W': 345600000}  # 周线从周一 00:00 UTC 开始（纪元起点是周四）
OKX_BAR = {'1D': '1Dutc', '1W': '1Wutc'}


def fetch(client, bar, count, inst):
    found = {}
    cursor = None
    while len(found) < count:
        params = dict(instId=inst, bar=OKX_BAR[bar], limit='100')
        if cursor:
            params['after'] = str(cursor)
        raw = client.get('/api/v5/market/history-candles', **params)
        if not raw:
            break
        for r in raw:
            t = int(r[0]); o, h, l, c, vol, base, quote = [float(x) for x in r[1:8]]
            if (t - OFFSET[bar]) % STEP[bar] or r[8] != '1':
                if r[8] == '1':
                    raise ValueError('时间未对齐: ' + bar + ' ' + iso(t))
                continue
            if l > min(o, c) or h < max(o, c) or min(o, h, l, c) <= 0:
                raise ValueError('OHLC不合法 ' + iso(t))
            found[t] = dict(ts=t, close_time=t + STEP[bar], available_at=t + STEP[bar], o=o, h=h, l=l, c=c,
                            vol=vol, base_vol=base, quote_vol=quote, confirm=1)
        oldest = min(int(r[0]) for r in raw)
        if cursor is not None and oldest >= cursor:
            break
        cursor = oldest
    rows = sorted(found.values(), key=lambda r: r['ts'])
    ordered = all(rows[i + 1]['ts'] - rows[i]['ts'] == STEP[bar] for i in range(len(rows) - 1))
    return rows, dict(count=len(rows), first_open=iso(rows[0]['ts']) if rows else None,
                      last_close=iso(rows[-1]['close_time']) if rows else None,
                      status='complete' if rows and ordered else 'incomplete')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, default=Path('var/eth_mtf_latest'))
    ap.add_argument('--proxy')
    ap.add_argument('--days', type=int, default=600)
    ap.add_argument('--weeks', type=int, default=300)
    a = ap.parse_args()
    client = Client(a.proxy)
    manifest = json.loads((a.output / 'manifest.json').read_text())
    for bar, n in (('1D', a.days), ('1W', a.weeks)):
        rows, q = fetch(client, bar, n, manifest['instrument'])
        # 只保留在研究截止时间之前已收盘的K线，避免用到截止后的数据
        rows = [r for r in rows if r['close_time'] <= manifest['end_ms']]
        q['count'] = len(rows); q['last_close'] = iso(rows[-1]['close_time']) if rows else None
        save(a.output / (bar + '.json'), rows)
        manifest['datasets'][bar] = q
        print(bar, q, flush=True)
    save(a.output / 'manifest.json', manifest)


if __name__ == '__main__':
    main()
