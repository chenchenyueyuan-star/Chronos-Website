import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from streamlit.testing.v1 import AppTest
from measurement_store import MeasurementStore

ROOT=Path(__file__).resolve().parents[1]

class ProfileUITests(unittest.TestCase):
    @patch('dotenv.load_dotenv',return_value=False)
    def test_welcome_choices_and_guest_view(self, _dotenv):
        app=AppTest.from_file(str(ROOT/'app.py')).run()
        labels={b.label for b in app.button}
        self.assertTrue({'Continue as guest','🔑 Sign In','📝 Sign Up'}.issubset(labels))
        self.assertFalse(app.exception)
        app.button[0].click().run()
        self.assertFalse(app.exception)
        self.assertNotIn('My session results',{b.label for b in app.button})
        self.assertFalse(app.toggle)

    @patch('dotenv.load_dotenv',return_value=False)
    def test_measurements_only_inside_profile(self, _dotenv):
        with tempfile.TemporaryDirectory() as temp:
            store=MeasurementStore(Path(temp)/'private'/'data.sqlite3')
            token=store.issue_ticket('profile-test','Profile Test',{'title':'Test run','duration_seconds':60})
            run=store.start(token)
            store.event(token,run,'completed',60)
            with patch('measurement_ui.services',return_value=(store,None)):
                app=AppTest.from_file(str(ROOT/'app.py')).run()
                intro=json.loads((ROOT/'data'/'intro questionnaire.json').read_text())
                values={'signed_up':True,'guest':False,'user_email':'profile-test','measurement_identity':'profile-test','username':'Profile Test','questionnaire_done':True,'session_q':8,
                        'answers':[{'field':q['field'],'value':q['options']['A']['value'],'Question':q['text'],'Answer':q['options']['A']['text']} for q in intro['items']]}
                for key,value in values.items():app.session_state[key]=value
                app.session_state['account_database']=SimpleNamespace(user_id='test-user-id',current=lambda:{'profile-test': {'name':'Profile Test'}})
                app.run()
                self.assertFalse(app.exception)
                self.assertEqual({b.label for b in app.sidebar.button},{'🌲 Forest','🌌 Roadtrip Sunset'})
                popover=app.get('popover')[0]
                self.assertFalse(any('### Preferences' in x.value for x in popover.markdown))
                self.assertIn('My saved session results',{x.label for x in popover.expander})
                self.assertIn('Session measurements',{x.label for x in popover.expander})
                self.assertIn('Download my run data',{x.proto.label for x in popover.get('download_button')})
                self.assertNotIn('My session results',{x.value for x in app.title})
                self.assertTrue(any(x.label=='Enable measurements during my sessions' for x in popover.toggle))

if __name__=='__main__':unittest.main()
