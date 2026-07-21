import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd

import scanner
from v3.backtest import run_frame_backtest
from v3.ingest import normalize_candidate
from v3.lifecycle import evaluate_status, initial_status
from v3.paper import update_paper_portfolio
from v3.risk import build_risk_plan, classify_cluster, correlation_warnings
from v3.storage import LocalStore, SupabaseStore


class V3Tests(unittest.TestCase):
    def candidate(self):
        return {
            'id':'US Stock|AAPL|1D|bullish|cloud_breakout|2026-01-01',
            'market':'US Stock','symbol':'AAPL','name':'Apple Inc.','direction':'bullish',
            'signal_type':'cloud_breakout','date':'2026-01-01','close':100.0,'score':8,'grade':'A',
            'weekly_alignment':'aligned','reasons':['x'],'warnings':[],
            'metrics':{'atr':2.0,'kijun':98.0,'cloud_top':97.0,'cloud_bottom':94.0,'kijun_distance_atr':1.0}
        }

    def test_risk_plan(self):
        plan=build_risk_plan(self.candidate())
        self.assertLess(plan.invalidation,100)
        self.assertGreater(plan.reward_reference,100)
        self.assertGreater(plan.suggested_units_per_1000_risk,0)

    def test_statuses(self):
        c=self.candidate(); self.assertEqual(initial_status(c),'confirmed')
        self.assertEqual(evaluate_status({'direction':'bullish','signal_date':'2026-01-01','status':'active'}, {'close':90,'kijun':95,'cloud_bottom':92,'cloud_top':98,'atr':2,'date':'2026-01-03'}),'invalidated')

    def test_normalize_candidate(self):
        record=normalize_candidate(self.candidate())
        self.assertEqual(record.cluster,'us-other')
        self.assertEqual(record.grade,'A')

    def test_clusters(self):
        self.assertEqual(classify_cluster('Crypto Spot','ETHBTC'),'crypto-btc')
        warnings=correlation_warnings([{'cluster':'x','symbol':str(i)} for i in range(4)],3)
        self.assertIn('x',warnings)

    def test_local_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=LocalStore(Path(tmp)); row=normalize_candidate(self.candidate()).to_dict()
            store.upsert_signals([row]); self.assertEqual(store.list_signals()[0]['symbol'],'AAPL')
            store.record_event(row['id'],'detected',{}); self.assertTrue((Path(tmp)/'v3_events.json').exists())

    def test_new_supabase_secret_uses_apikey_only(self):
        fake = SimpleNamespace(
            supabase_url='https://example.supabase.co',
            supabase_service_role_key='sb_secret_example',
        )
        with patch('v3.storage.settings', fake):
            store = SupabaseStore()
        self.assertEqual(store.headers['apikey'], 'sb_secret_example')
        self.assertNotIn('Authorization', store.headers)

    def test_legacy_service_role_keeps_bearer_header(self):
        fake = SimpleNamespace(
            supabase_url='https://example.supabase.co',
            supabase_service_role_key='legacy.jwt.value',
        )
        with patch('v3.storage.settings', fake):
            store = SupabaseStore()
        self.assertEqual(store.headers['Authorization'], 'Bearer legacy.jwt.value')

    def test_backtest_no_lookahead_runs(self):
        n=500; idx=pd.date_range('2020-01-01',periods=n,freq='D')
        trend=np.linspace(50,150,n)+np.sin(np.arange(n)/7)*5
        frame=pd.DataFrame({'Open':trend-0.2,'High':trend+1,'Low':trend-1,'Close':trend,'Volume':np.full(n,1_000_000)},index=idx)
        result=run_frame_backtest(scanner,frame,'US Stock','TEST')
        self.assertIn('signals',result.summary)
        self.assertEqual(result.parameters['daily'],[20,60,120,30])


if __name__=='__main__': unittest.main()
