"""下载 OKX ETH 线性永续公开数据；无需密钥，不连接账户或下单。"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import time
from urllib.request import getproxies

import requests

BARS = {'1m': 60000, '5m': 300000, '15m': 900000, '1H': 3600000, '4H': 14400000}
INST = 'ETH-USDT-SWAP'


def save(path, data):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    tmp.replace(path)


def iso(ts):
    return datetime.fromtimestamp(ts / 1000, timezone.utc).isoformat()


class Client:
    def __init__(self, proxy=None):
        self.session = requests.Session()
        # requests 不一定读取 macOS 系统代理；显式继承系统配置，不改系统设置。
        proxies = getproxies()
        self.session.proxies.update({k: v for k, v in proxies.items() if k in ('http', 'https')})
        if proxy:
            self.session.proxies.update(http=proxy, https=proxy)

    def get(self, path, **params):
        last = None
        for attempt in range(5):
            try:
                response = self.session.get('https://www.okx.com' + path, params=params, proxies=self.session.proxies, timeout=25)
                response.raise_for_status()
                body = response.json()
                if body.get('code') != '0':
                    raise ValueError('OKX: ' + str(body.get('code')) + ' ' + body.get('msg', ''))
                time.sleep(0.12)
                return body['data']
            except (requests.RequestException, ValueError) as exc:
                last = exc
                time.sleep(min(2 ** attempt, 8))
        raise RuntimeError('公开接口请求失败: ' + path + ': ' + str(last))


def normalize(raw, bar):
    if len(raw) != 9 or raw[8] not in ('0', '1'):
        raise ValueError('K线格式/收盘状态异常')
    t = int(raw[0])
    nums = [float(x) for x in raw[1:8]]
    o, h, l, c, vol, base, quote = nums
    if t % BARS[bar] or not all(math.isfinite(v) for v in nums):
        raise ValueError('时间对齐或有限值校验失败')
    if min(o, h, l, c) <= 0 or min(vol, base, quote) < 0 or l > min(o, c) or h < max(o, c):
        raise ValueError('OHLCV不合法')
    return dict(ts=t, close_time=t+BARS[bar], available_at=t+BARS[bar],
                o=o, h=h, l=l, c=c, vol=vol, base_vol=base, quote_vol=quote, confirm=int(raw[8]))


def quality(rows, bar, start, end):
    step = BARS[bar]
    expected_first = (start + step - 1) // step * step
    expected_last = end // step * step - step
    missing = []
    actual = {r['ts'] for r in rows}
    ordered = all(rows[i]['ts'] < rows[i+1]['ts'] for i in range(len(rows)-1))
    duplicates = len(rows)-len(actual)
    for t in range(expected_first, expected_last + 1, step):
        if t not in actual:
            missing.append(t)
    return dict(count=len(rows), first_open=iso(rows[0]['ts']) if rows else None,
                last_close=iso(rows[-1]['close_time']) if rows else None,
                missing_count=len(missing), duplicate_count=duplicates, strictly_ordered=ordered,
                missing_first_20=[iso(t) for t in missing[:20]],
                status='complete' if rows and not missing and ordered and not duplicates else 'incomplete')


def candles(client, bar, start, end, path):
    # 同一时间范围断点续传；已收盘历史冲突报错，不静默覆盖。
    cache = json.loads(path.read_text()) if path.exists() else []
    found = {r['ts']: r for r in cache if start <= r['ts'] and r['close_time'] <= end}
    cursor = end
    if found and max(found) >= end // BARS[bar] * BARS[bar] - BARS[bar]:
        cursor = min(found)
    pages = 0
    while cursor > start:
        raw = client.get('/api/v5/market/history-candles', instId=INST, bar=bar, after=str(cursor), limit='300')
        if not raw:
            break
        oldest = min(int(r[0]) for r in raw)
        if oldest >= cursor:
            raise ValueError('分页游标没有向历史推进')
        for r in raw:
            row = normalize(r, bar)
            if row['confirm'] == 1 and row['ts'] >= start and row['close_time'] <= end:
                if row['ts'] in found and row != found[row['ts']]:
                    raise ValueError('同一历史K线内容冲突')
                found[row['ts']] = row
        cursor = oldest
        pages += 1
        if pages % 20 == 0:
            save(path, sorted(found.values(), key=lambda r: r['ts']))
            print(bar, len(found), '根，已到', iso(oldest), flush=True)
    rows = sorted(found.values(), key=lambda r: r['ts'])
    save(path, rows)
    return quality(rows, bar, start, end)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--days', type=int, default=60)
    parser.add_argument('--output', type=Path, default=Path('var/eth_mtf'))
    parser.add_argument('--proxy')
    parser.add_argument('--inst', default='ETH-USDT-SWAP', help='OKX 线性USDT永续合约ID，例如 BTC-USDT-SWAP')
    parser.add_argument('--end', help='固定UTC截止时间，例如2026-09-28T00:00:00Z；续传请保持一致')
    args = parser.parse_args()
    global INST
    INST = args.inst
    if not 1 <= args.days <= 90:
        parser.error('days必须在1至90之间；资金费接口历史有保留期限制')
    args.output.mkdir(parents=True, exist_ok=True)
    client = Client(args.proxy)
    server = int(client.get('/api/v5/public/time')[0]['ts'])
    end = int(datetime.fromisoformat(args.end.replace('Z', '+00:00')).timestamp()*1000) if args.end else server // 60000 * 60000
    if args.end and datetime.fromisoformat(args.end.replace('Z', '+00:00')).tzinfo is None:
        parser.error('--end 必须含时区')
    if end > server:
        parser.error('截止时间不能在交易所当前时间之后')
    start = end - args.days * 86400000
    manifest = dict(profile='eth-mtf-research-v3', instrument=INST, market='linear_perpetual',
                    exchange='OKX', retrieved_at=iso(server), start=iso(start), end=iso(end),
                    start_ms=start, end_ms=end, timezone='UTC', source='https://www.okx.com/api/v5',
                    volume_unit='contracts', base_volume_unit=INST.split('-')[0], quote_volume_unit='USDT',
                    available_at_assumption='close_time; 网络传播延迟未模拟', rule_ids=['D01','D02','D03'],
                    status='running', datasets={})
    save(args.output/'manifest.json', manifest)
    try:
        info = client.get('/api/v5/public/instruments', instType='SWAP', instId=INST)
        save(args.output/'instrument.json', info)
        for bar in ('4H', '1H', '15m', '5m', '1m'):
            # 高周期额外预热5×60根。低周期预热由首段样本完成。
            warm = 300 * BARS[bar] if bar in ('4H', '1H') else 0
            result = candles(client, bar, start-warm, end, args.output/(bar+'.json'))
            manifest['datasets'][bar] = result
            save(args.output/'manifest.json', manifest)
            print(bar, result, flush=True)
        funding = {}
        cursor = end
        while cursor > start:
            rows = client.get('/api/v5/public/funding-rate-history', instId=INST, after=str(cursor), limit='100')
            if not rows:
                break
            oldest = min(int(r['fundingTime']) for r in rows)
            if oldest >= cursor:
                raise ValueError('资金费分页没有推进')
            for r in rows:
                if start <= int(r['fundingTime']) < end:
                    funding[int(r['fundingTime'])] = r
            cursor = oldest
        save(args.output/'funding.json', [funding[k] for k in sorted(funding)])
        manifest['datasets']['funding'] = dict(count=len(funding), status='retrieved',
            note='实际结算事件，不假定固定8小时；未取得历史结算标记价格，回测用同期成交价估值')
        # 请求上限1000；API可能仅返回500条，以实际数量记录。不是60天全量逐笔。
        trades = client.get('/api/v5/market/trades', instId=INST, limit='1000')
        save(args.output/'recent_trades.json', trades)
        manifest['datasets']['trades'] = dict(count=len(trades), scope='latest_snapshot_only',
            first=iso(min(int(r['ts']) for r in trades)), last=iso(max(int(r['ts']) for r in trades)),
            size_unit='contracts', historical_backtest_input=False)
        for name, endpoint, params in [('ticker','/api/v5/market/ticker',{}),
                                      ('orderbook','/api/v5/market/books',{'sz':'50'}),
                                      ('open_interest','/api/v5/public/open-interest',{'instType':'SWAP'})]:
            save(args.output/(name+'.json'), client.get(endpoint, instId=INST, **params))
        manifest['status'] = 'complete' if all(manifest['datasets'][b]['status']=='complete' for b in BARS) else 'incomplete'
    except Exception as exc:
        manifest['status'] = 'failed'
        manifest['error'] = str(exc)
        raise
    finally:
        manifest['download_finished_at'] = datetime.now(timezone.utc).isoformat()
        manifest['sha256'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in args.output.glob('*.json') if p.name != 'manifest.json'}
        save(args.output/'manifest.json', manifest)
    print('数据目录:', args.output.resolve(), flush=True)
    if manifest['status'] != 'complete':
        raise SystemExit('存在数据缺口，禁止直接用于完整回测')


if __name__ == '__main__':
    main()
