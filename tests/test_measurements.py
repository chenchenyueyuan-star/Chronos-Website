import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import streamlit as st
from measurement_store import MeasurementStore, summarise
from measurement_receiver import start_receiver
from measurement_ui import player_config
from audio_player import player_html


class MeasurementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = MeasurementStore(Path(self.temp.name) / 'private' / 'test.sqlite3')
        self.alice = self.store.issue_ticket('alice', 'Alice', {'title':'Quiet','duration_seconds':60})
        self.bob = self.store.issue_ticket('bob', 'Bob', {'title':'Quiet','duration_seconds':60})
    def tearDown(self):
        self.temp.cleanup()

    def test_no_guest_ticket_and_no_cross_account_access(self):
        with self.assertRaises(PermissionError): self.store.issue_ticket('', 'Guest', {})
        run = self.store.start(self.alice)
        self.assertEqual(len(self.store.runs_for('alice')),1)
        self.assertEqual(self.store.runs_for('bob'),[])
        with self.assertRaises(PermissionError):self.store.result('bob',run)
        with self.assertRaises(PermissionError):self.store.event(self.bob,run,'completed',60)

    def test_pause_resume_completion_replay_and_revocation(self):
        run = self.store.start(self.alice)
        self.store.event(self.alice,run,'paused',20)
        self.assertEqual(self.store.start(self.alice,run),run)
        self.store.event(self.alice,run,'completed',60)
        with self.assertRaises(PermissionError):self.store.start(self.alice,run)
        replay = self.store.start(self.alice)
        self.assertNotEqual(run,replay)
        self.store.revoke(self.alice)
        self.assertEqual(self.store.result('alice',replay)['run']['status'],'interrupted')
        with self.assertRaises(PermissionError):self.store.start(self.alice)

    def test_sample_deduplication_and_quality(self):
        run=self.store.start(self.alice)
        for index in range(61):
            metrics={'cardio':{'pulseRate':[{'timestamp':index,'value':80-index/10,'confidence':90,'stable':True}]},'breathing':{'rate':[{'timestamp':index,'value':14,'confidence':10,'stable':False}]}}
            self.store.ingest(self.alice,run,metrics,index,{'voice_on':True,'background_on':False})
            self.store.ingest(self.alice,run,metrics,index,{'voice_on':True,'background_on':False})
        result=self.store.result('alice',run)
        self.assertEqual(len(result['samples']),122)
        self.assertLess(result['summary']['pulse']['change'],0)
        self.assertEqual(result['summary']['breathing']['comparison'],'insufficient data')
        self.assertFalse(result['samples'][0]['background_on'])
        self.store.event(self.alice,run,'paused',60)
        self.store.ingest(self.alice,run,{'cardio':{'pulseRate':[{'timestamp':99,'value':70}]}},60,{})
        self.assertEqual(len(self.store.result('alice',run)['samples']),122)

    def test_expression_scores_are_saved_deduplicated_and_account_scoped(self):
        run = self.store.start(self.alice)
        metrics = {'face':{'expression':[{'timestamp':123,'stable':True,'scores':[{'type':5,'confidence':78},{'type':6,'confidence':22},{'type':1,'confidence':float('nan')}]}]}}
        for _ in range(2):self.store.ingest(self.alice,run,metrics,15,{'voice_on':True})
        result = self.store.result('alice',run)
        self.assertEqual({s['metric'] for s in result['samples']},{'expression:happy','expression:neutral'})
        self.assertEqual(len(result['samples']),2)
        with self.assertRaises(PermissionError):self.store.result('bob',run)

    def test_malformed_point_does_not_discard_valid_neighbours(self):
        run = self.store.start(self.alice)
        metrics = {'cardio':{'pulseRate':[{'timestamp':1,'value':75,'confidence':90,'stable':True},None,'bad',{'timestamp':2,'value':73,'confidence':90,'stable':True}]}, 'face':{'expression':[None,{'timestamp':3,'stable':True,'scores':[None,{'type':5,'confidence':80}]}]}}
        self.store.ingest(self.alice,run,metrics,20,{})
        saved=self.store.result('alice',run)['samples']
        self.assertEqual(len(saved),3)
        self.assertEqual([s['value'] for s in saved if s['metric']=='pulse'],[75,73])

    def test_callback_timestamp_matches_standalone_fallback(self):
        run = self.store.start(self.alice)
        metrics = {'cardio': {'pulseRate': [{'value': 72, 'confidence': 90, 'stable': True}]},
                   'breathing': {'rate': [{'value': 14, 'confidence': 90, 'stable': True}]}}
        self.store.ingest(self.alice, run, metrics, 20, {}, timestamp_us=123456)
        self.store.ingest(self.alice, run, metrics, 20, {}, timestamp_us=123456)
        samples = self.store.result('alice', run)['samples']
        self.assertEqual({s['metric'] for s in samples}, {'pulse', 'breathing'})
        self.assertEqual(len(samples), 2)
        self.assertEqual({s['timestamp_us'] for s in samples}, {'123456'})

    def test_bad_optional_face_group_does_not_rollback_vitals(self):
        run = self.store.start(self.alice)
        for index, face in enumerate([{'expression': [{'timestamp': 1, 'scores': None}]}, 'invalid', {'expression': 42}]):
            self.store.ingest(self.alice, run, {
                'cardio': {'pulseRate': [{'timestamp': index, 'value': 72}]},
                'breathing': {'rate': [{'timestamp': index, 'value': 14}]},
                'face': face,
            }, 20, {})
        samples = self.store.result('alice', run)['samples']
        self.assertEqual(len(samples), 6)
        self.assertEqual({s['metric'] for s in samples}, {'pulse', 'breathing'})

    def test_missing_readings_do_not_make_a_live_run_stale(self):
        run = self.store.start(self.alice)
        with self.store.connect() as db:
            db.execute('UPDATE runs SET last_seen=0 WHERE id=?',(run,))
        self.store.heartbeat(self.alice,run)
        self.assertEqual(self.store.runs_for('alice')[0]['status'],'recording')
        self.store.event(self.alice,run,'paused',10)
        self.store.heartbeat(self.alice,run)
        self.assertEqual(self.store.result('alice',run)['run']['status'],'paused')

    def test_paused_run_exposes_partial_results_immediately(self):
        run=self.store.start(self.alice)
        self.store.ingest(self.alice,run,{'cardio':{'pulseRate':[{'timestamp':1,'value':72,'stable':True,'confidence':90}]}},10,{'voice_on':True})
        self.store.event(self.alice,run,'paused',12)
        result=self.store.result('alice',run)
        self.assertTrue(result['partial'])
        self.assertEqual(result['run']['status'],'paused')
        self.assertEqual(result['captured_until_seconds'],12)
        self.assertEqual(len(result['samples']),1)
        self.store.start(self.alice,run)
        self.store.event(self.alice,run,'completed',60)
        self.assertFalse(self.store.result('alice',run)['partial'])

    def test_bad_values_and_empty_data_do_not_claim_benefit(self):
        run=self.store.start(self.alice)
        self.store.ingest(self.alice,run,{'cardio':{'pulseRate':[{'timestamp':1,'value':float('nan')},{'timestamp':2,'value':True}]}},0,{})
        result=self.store.result('alice',run)
        self.assertEqual(result['samples'],[])
        self.assertEqual(result['summary']['pulse']['comparison'],'insufficient data')

    def test_receiver_requires_ticket(self):
        server=start_receiver(self.store,0)
        url=f'http://127.0.0.1:{server.server_address[1]}/start'
        try:
            request=Request(url,data=b'{}',headers={'Authorization':'Bearer invalid'},method='POST')
            with self.assertRaises(HTTPError) as error:urlopen(request)
            self.assertEqual(error.exception.code,403)
            error.exception.close()
            request=Request(url,data=b'{}',headers={'Authorization':'Bearer '+self.alice},method='POST')
            with urlopen(request) as response:self.assertTrue(json.load(response)['run_id'])
        finally:server.shutdown();server.server_close()

    def test_receiver_saves_all_three_metrics_with_callback_timestamp(self):
        server = start_receiver(self.store, 0)
        base = f'http://127.0.0.1:{server.server_address[1]}'
        def post(path, payload):
            request = Request(base + path, data=json.dumps(payload).encode(),
                              headers={'Authorization': 'Bearer ' + self.alice, 'Content-Type': 'application/json'}, method='POST')
            with urlopen(request) as response:
                return json.load(response)
        try:
            run = post('/start', {})['run_id']
            post('/metrics', {'run_id': run, 'timestampUs': 123456, 'position': 20, 'levels': {}, 'metrics': {
                'cardio': {'pulseRate': [{'value': 72, 'stable': True, 'confidence': 90}]},
                'breathing': {'rate': [{'value': 14, 'stable': True, 'confidence': 90}]},
                'face': {'expression': [{'stable': True, 'scores': [{'type': 5, 'confidence': 80}]}]},
            }})
            saved = self.store.result('alice', run)['samples']
            self.assertEqual({s['metric'] for s in saved}, {'pulse', 'breathing', 'expression:happy'})
            self.assertEqual(len(saved), 3)
        finally:
            server.shutdown()
            server.server_close()

    def test_guest_or_unverified_accounts_never_get_player_ticket(self):
        for state in [{'guest':True,'signed_up':True},{'guest':False,'signed_up':True,'user_email':'alice'}]:
            with patch('measurement_ui.st.session_state',state), patch('measurement_ui.services') as services:
                self.assertIsNone(player_config({'title':'Quiet'},lambda:{}))
                services.assert_not_called()

    def test_signed_in_playback_requests_permission_unless_explicitly_disabled(self):
        state = {'guest':False,'signed_up':True,'user_email':'alice','measurement_identity':'alice','username':'Alice'}
        with patch('measurement_ui.st.session_state',state), patch('measurement_ui.services',return_value=(self.store,None)):
            config = player_config({'title':'Quiet','duration_seconds':60},lambda:{'alice':{}})
            self.assertTrue(config['token'])
            self.assertIsNone(player_config({'title':'Quiet','duration_seconds':60},lambda:{'alice':{'measurement_consent':False}}))

    def test_camera_code_only_in_opted_in_player(self):
        session={'title':'Quiet','duration_seconds':60,'voice':None,'background':None}
        self.assertNotIn('getUserMedia',player_html(session))
        html=player_html(session,{'url':'http://127.0.0.1:3001','token':'test-ticket'})
        self.assertIn('getUserMedia',html)
        self.assertIn('monitorStop',html)
        self.assertNotIn('<video id=',html)
