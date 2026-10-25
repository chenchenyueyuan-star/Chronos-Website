import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from streamlit.testing.v1 import AppTest
from measurement_store import MeasurementStore

ROOT=Path(__file__).resolve().parents[1]
UID='37ad5c46-3df7-49a1-86e6-335ebc972a86'
SID='f5ebf465-9b32-4b09-a7c9-4de0bc7c1cc7'

class DatabaseAppFlowTests(unittest.TestCase):
    @patch('dotenv.load_dotenv', return_value=False)
    def test_cloud_signin_questionnaire_generation_and_profile(self, _dotenv):
        intro=json.loads((ROOT/'data'/'intro questionnaire.json').read_text())['items']
        pre=json.loads((ROOT/'data'/'session questionnaire.json').read_text())['items']
        with tempfile.TemporaryDirectory() as tmp:
            measurements=MeasurementStore(Path(tmp)/'measurements.sqlite3')
            audio_path=Path(tmp)/'voice.mp3';audio_path.write_bytes(b'test-audio')
            session={'title':'Cloud flow','voice':str(audio_path),'background':None,'duration_seconds':60,'script':'Test narration.'}
            with patch('chronos_store.initialize'),patch('chronos_store.login',return_value={'user_id':UID,'username':'cloud-test'}),patch('chronos_store.get_preferences',return_value={'answers':{q['field']:q['options']['A']['value'] for q in intro},'settings':{'measurement_consent':False}}),patch('chronos_store.update_settings'),patch('chronos_store.start_session',return_value=SID) as start,patch('chronos_v3_bridge.load_question_banks',return_value=(intro,pre)),patch('chronos_v3_bridge.save_pre_session') as before,patch('chronos_v3_bridge.save_script') as script_saved,patch('measurement_ui.services',return_value=(measurements,None)),patch('chronos_snowflake_live.connect_snowflake') as snowflake:
                app=AppTest.from_file(str(ROOT/'app.py')).run()
                next(b for b in app.button if b.label=='🔑 Sign In').click().run()
                app.text_input[0].set_value('cloud-test');app.text_input[1].set_value('test-password')
                next(b for b in app.button if b.label=='Sign In').click().run()
                self.assertFalse(app.exception)
                self.assertEqual(app.session_state['measurement_identity'],'cloud:'+UID)
                for _ in range(8):app.button(key='session_answer_a').click().run()
                self.assertFalse(app.exception);self.assertFalse(app.slider);self.assertFalse(app.radio)
                next(b for b in app.button if b.label=='Prepare audio plan').click().run()
                self.assertEqual(app.session_state['database_session_id'],SID)
                self.assertEqual(before.call_args.args,(UID,SID,{q['id']:'A' for q in pre}))
                with patch.dict(os.environ,{'GEMINI_API_KEY':'test','ELEVENLABS_API_KEY':'test','ELEVENLABS_MALE_VOICE_1_WHISPER_ID':'whisper-test'}),patch('audio_generation.generate_script',return_value={'title':'Cloud flow','paragraphs':['Test narration.']}),patch('audio_generation.render_tracks',return_value=(None,None)),patch('audio_generation.save_session',return_value=session):
                    app.button(key='generate_session').click().run()
                self.assertFalse(app.exception)
                self.assertEqual(script_saved.call_args.args[:4],(UID,SID,'Cloud flow','Test narration.'))
                self.assertEqual(app.session_state['generated_session']['database_user_id'],UID)
                self.assertEqual(start.call_count,1)
                app.run()
                popover=app.get('popover')[0]
                self.assertIn('My saved session results',{x.label for x in popover.expander})
                self.assertIn('Save generated audio to Snowflake',{x.label for x in popover.expander})
                snowflake.assert_not_called()
                self.assertTrue(app.get('iframe'))

    @patch('dotenv.load_dotenv', return_value=False)
    def test_empty_and_rejected_signin_always_show_a_message(self, _dotenv):
        app=AppTest.from_file(str(ROOT/'app.py')).run()
        next(b for b in app.button if b.label=='🔑 Sign In').click().run()
        next(b for b in app.button if b.label=='Sign In').click().run()
        self.assertFalse(app.exception)
        self.assertTrue(any('Enter your username and password' in x.value for x in app.error))
        app.text_input[0].set_value('a');app.text_input[1].set_value('wrong-password')
        with patch('account_database.AccountDatabase.login',return_value=None):
            next(b for b in app.button if b.label=='Sign In').click().run()
        self.assertFalse(app.exception)
        self.assertTrue(any('Invalid username or password' in x.value for x in app.error))
