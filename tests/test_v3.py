import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

import scanner
from v3.backtest import run_frame_backtest
from v3.ingest import normalize_candidate
from v3.lifecycle import evaluate_status, initial_status
from v3.lifecycle import sessions_since
from v3.paper import update_paper_portfolio
from v3.quality import validate_ohlcv
from v3.queue import DatabaseDeliveryQueue, next_delivery_time
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

    def test_local_store_preserves_lifecycle_during_ingest(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=LocalStore(Path(tmp)); row=normalize_candidate(self.candidate()).to_dict()
            row['status']='active'; row['delivered_at']='2026-01-02T00:00:00+00:00'
            store.upsert_signals([row])
            incoming={**row,'status':'confirmed','delivered_at':None,'close':105.0}
            store.upsert_signals([incoming],preserve_lifecycle=True)
            saved=store.list_signals()[0]
            self.assertEqual(saved['status'],'active')
            self.assertEqual(saved['delivered_at'],'2026-01-02T00:00:00+00:00')
            self.assertEqual(saved['close'],105.0)

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

    def test_completed_weekly_frame_excludes_partial_week(self):
        idx=pd.bdate_range('2024-01-01',periods=503)
        idx=idx[idx.weekday != 4]
        values=np.linspace(100,140,len(idx))
        frame=pd.DataFrame({'Open':values,'High':values+1,'Low':values-1,'Close':values,'Volume':1_000_000},index=idx)
        weekly=scanner.weekly_frame(frame)
        self.assertTrue(len(weekly))
        self.assertLessEqual(weekly.index[-1],frame.index[-1])
        self.assertEqual(weekly.index[-1].weekday(),4)

    def test_sessions_use_market_calendar(self):
        self.assertEqual(sessions_since('2026-01-02','2026-01-05','US Stock'),1)
        self.assertEqual(sessions_since('2026-01-02','2026-01-05','Crypto Spot'),3)

    def test_data_quality_rejects_invalid_ohlc(self):
        idx=pd.date_range('2026-01-01',periods=60,freq='D')
        frame=pd.DataFrame({'Open':100.0,'High':101.0,'Low':99.0,'Close':100.0,'Volume':1000.0},index=idx)
        frame.loc[idx[-1],'High']=98.0
        ok,issues,meta=validate_ohlcv(frame,minimum_rows=50)
        self.assertFalse(ok)
        self.assertTrue(any('Invalid OHLC' in issue for issue in issues))
        self.assertEqual(meta['invalid_ohlc'],1)

    def test_database_queue_payload_and_claim(self):
        fake=SupabaseStore.__new__(SupabaseStore)
        fake._request=MagicMock(side_effect=[2,[{'queue_id':7,'signal_id':self.candidate()['id'],'payload':self.candidate(),'attempts':1}],1])
        queue=DatabaseDeliveryQueue(fake)
        self.assertEqual(queue.enqueue([self.candidate()]),2)
        claim=queue.claim('us')
        self.assertEqual(claim.candidates[0]['symbol'],'AAPL')
        claim.complete([self.candidate()['id']],{'telegram_message_id':123})
        self.assertEqual(fake._request.call_args_list[0].args[1],'rpc/enqueue_delivery_signals')
        self.assertEqual(fake._request.call_args_list[1].args[1],'rpc/claim_delivery_batch')
        self.assertEqual(fake._request.call_args_list[2].args[1],'rpc/complete_delivery_batch')

    def test_next_delivery_boundary(self):
        before=datetime(2026,7,21,11,59,tzinfo=timezone.utc)
        after=datetime(2026,7,21,12,1,tzinfo=timezone.utc)
        self.assertEqual(next_delivery_time(before),'2026-07-21T12:00:00+00:00')
        self.assertEqual(next_delivery_time(after),'2026-07-22T12:00:00+00:00')

    def test_paper_portfolio_marks_open_positions_to_market(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=LocalStore(Path(tmp)); signal=normalize_candidate(self.candidate()).to_dict()
            with patch('v3.paper.get_store',return_value=store):
                opened=update_paper_portfolio([signal])
                self.assertEqual(len(opened['positions']),1)
                signal={**signal,'close':105.0,'status':'active'}
                marked=update_paper_portfolio([signal])
                self.assertGreater(marked['unrealized_pnl'],0)
                self.assertGreater(marked['equity'],marked['cash'])


if __name__=='__main__': unittest.main()
