import csv
import io
import json
import unittest
from result_report import clock, overview, text_report, readings_csv, camera_hint

class ReportTests(unittest.TestCase):
    def test_readable_partial_report_and_csv(self):
        result={'run':{'name':'Alex','started':'2026-10-25T12:00:00+00:00','status':'paused','metadata':json.dumps({'title':'Quiet moment','duration_seconds':300})},'partial':True,'captured_until_seconds':75,
                'samples':[{'metric':'pulse','playback_seconds':70,'value':72,'confidence':90,'stable':1,'voice_on':1,'background_on':0}], 'events':[],
                'summary':{'pulse':{'samples':1,'usable_samples':1},'breathing':{'samples':0,'usable_samples':0}}}
        self.assertEqual(overview(result)['Results saved through'],'1:15')
        report=text_report(result)
        self.assertIn('partial results',report)
        self.assertIn('not enough reliable data',report)
        self.assertNotIn('token_hash',report)
        rows=list(csv.DictReader(io.StringIO(readings_csv(result))))
        self.assertEqual(rows[0]['unit'],'beats/min')
        self.assertEqual(rows[0]['playback_time_mm_ss'],'1:10')
        self.assertEqual(clock(0),'0:00')

    def test_empty_results_explain_camera_framing(self):
        result={'run':{'name':'Alex','started':'2026-10-25T12:00:00+00:00','status':'paused','metadata':'{}'},'partial':True,'captured_until_seconds':15,'samples':[],
                'events':[{'kind':'paused','detail':json.dumps({'validation':{'code':7,'hint':'Place more of the chest in view.'}})}],
                'summary':{'pulse':{'samples':0,'usable_samples':0},'breathing':{'samples':0,'usable_samples':0}}}
        self.assertEqual(camera_hint(result),'Place more of the chest in view.')
        self.assertIn('No readings were collected.',text_report(result))
        self.assertIn('Place more of the chest in view.',text_report(result))
