"""合成边界测试，不是历史盈利证据。"""
import copy
import unittest

import pandas as pd

from research.eth_mtf.download_okx import normalize, quality
from research.eth_mtf.backtest import features, protective_fill, run, Profile, order_plan


class DataTests(unittest.TestCase):
    def test_bad_ohlc_and_confirmation_rejected(self):
        with self.assertRaises(ValueError):
            normalize(['0','100','99','98','100','1','0.1','10','1'],'1m')
        with self.assertRaises(ValueError):
            normalize(['0','100','101','98','100','1','0.1','10','9'],'1m')

    def test_gap_not_filled(self):
        rows=[dict(ts=0,close_time=60000),dict(ts=120000,close_time=180000)]
        q=quality(rows,'1m',0,180000)
        self.assertEqual(q['missing_count'],1)
        self.assertEqual(q['status'],'incomplete')

    def test_duplicate_rejected(self):
        r=dict(ts=0,close_time=60000)
        self.assertEqual(quality([r,r], "1m", 0, 60000)["status"], "incomplete")

    def test_htf_availability_and_prefix(self):
        def row(t,step,c):
            return dict(ts=t,close_time=t+step,o=c,h=c+1,l=c-1,c=c,vol=1,confirm=1)
        data={'5m':[row(t,300000,100) for t in range(0,30000000,300000)],
              '1H':[row(t,3600000,100+t/3600000) for t in range(0,36000000,3600000)],
              '4H':[row(t,14400000,100+t/14400000) for t in range(0,57600000,14400000)]}
        f=features(data)
        self.assertTrue(pd.isna(f.loc[f.ts==14100000,'h4_c'].iloc[0]))
        self.assertEqual(f.loc[f.ts==14400000,'h4_c'].iloc[0],100)
        changed=copy.deepcopy(data)
        changed['4H'][1]['c']=100000
        changed['1H'][4]['c']=100000
        f2=features(changed)
        pd.testing.assert_frame_equal(f[f.ts<18000000],f2[f2.ts<18000000])
        prefix=copy.deepcopy(data)
        prefix['5m']=prefix['5m'][:50]
        pd.testing.assert_frame_equal(f.iloc[:50].reset_index(drop=True),features(prefix))


class FillTests(unittest.TestCase):
    def test_stop_gap_worse_than_stop(self):
        p=dict(side=1,stop=98,target=104)
        self.assertEqual(protective_fill(p,dict(o=95,h=97,l=94),.01,.02),(94.98,'stop_gap',0))

    def test_ambiguous_stop_first(self):
        p=dict(side=1,stop=98,target=104)
        self.assertEqual(protective_fill(p,dict(o=100,h=105,l=97),.01,.02),(97.98,'stop',1))

    def test_limit_touch_not_fill(self):
        p=dict(side=-1,stop=102,target=96)
        self.assertIsNone(protective_fill(p,dict(o=100,h=101,l=96),.01,.02))
        self.assertEqual(protective_fill(p,dict(o=100,h=101,l=95.99),.01,.02)[1],'target')

    def test_favorable_open_before_later_stop(self):
        p=dict(side=1,stop=98,target=104)
        self.assertEqual(protective_fill(p,dict(o=105,h=106,l=97),.01,.02),(104,'target',0))

    def test_risk_budget_costs(self):
        p=Profile()
        order=order_plan(1,99.8,dict(c=101,atr=.5,close_time=0),1000,.01,.001,.001,p)
        self.assertIsNotNone(order)
        self.assertLessEqual(order['planned_risk'],2.5+1e-9)
        self.assertLessEqual(order['qty']*order['planned_entry'],990+1e-9)

    def fixture(self):
        # 推动、回踩两根、确认、下一根入场、持仓至期末。
        prices=[(100.5,101.2,100.4,101),(100.6,100.8,99.8,100.5),
                (100.4,100.6,99.9,100.3),(100.3,101.2,100.2,101),
                (101,101.3,100.8,101.1),(101.1,101.4,100.9,101.2)]
        rows,minutes=[],[]
        start=14400000
        for n,(o,h,l,c) in enumerate(prices):
            t=start+n*300000
            rows.append(dict(ts=t,close_time=t+300000,o=o,h=h,l=l,c=c,index=200+n,
                atr=.5,ema20=100,ema60=99,prev_c=prices[n-1][3] if n else 100,
                prev_h=prices[n-1][1] if n else 101,prev_l=prices[n-1][2] if n else 100,
                prev_slow=99,h4_index=400,h4_ema20=100,h4_ema60=98,h4_prev_fast=99,
                h4_c=101,h4_atr=2,h4_close_time=start,h1_index=400,h1_ema20=100,h1_ema60=98,
                h1_prev_fast=99,h1_c=101,h1_close_time=start))
            for j in range(5):
                minutes.append(dict(ts=t+j*60000,o=o,h=h,l=l,c=c))
        inst=dict(tickSz='.01',ctValCcy='ETH',settleCcy='USDT',ctType='linear',ctVal='.1',ctMult='1',lotSz='.01',minSz='.01')
        return dict(**{'1m':minutes}),pd.DataFrame(rows),inst,start,start+1800000

    def test_next_open_funding_terminal_and_ledger(self):
        data,f,inst,start,end=self.fixture()
        funding=[dict(fundingTime=str(start+1500000),realizedRate='.001')]
        result=run(data,f,funding,inst,Profile(),start,end)
        self.assertEqual(len(result['trades']),1)
        t=result['trades'][0]
        self.assertEqual(t['signal_ts'],start+1200000)
        self.assertEqual(t['entry_ts'],t['signal_ts']) # 次根开盘=上一根收盘时间
        self.assertAlmostEqual(t['entry'],101.02)
        self.assertGreater(t['funding'],0)
        self.assertEqual(t['reason'],'end_liquidation')
        self.assertAlmostEqual(result['summary']['net_usdt'],sum(t['net'] for t in result['trades']))
        self.assertIsNotNone(result['summary']['terminal_position_before_liquidation'])

    def test_signal_prefix_invariance(self):
        data,f,inst,start,end=self.fixture()
        a=run(data,f,[],inst,Profile(),start,end)
        b=run(data,f.iloc[:4],[],inst,Profile(),start,start+1200000)
        self.assertEqual(a['signals'][:1],b['signals'])
        self.assertTrue(all(s['h4_known_at']<=s['ts']-300000 for s in a['signals']))


if __name__=='__main__':
    unittest.main()
