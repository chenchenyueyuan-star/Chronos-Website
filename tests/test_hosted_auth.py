"""Exercise the deployed form flow without real accounts or network writes."""
import os
import unittest
from pathlib import Path
from unittest.mock import patch
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]

class HostedAuthTests(unittest.TestCase):
    def app(self):
        app = AppTest.from_file(str(ROOT/'app.py'))
        app.secrets['TIMESCALE_SERVICE_URL'] = 'postgres://test-only.invalid/db'
        return app.run()

    def signup_form(self, app):
        next(b for b in app.button if b.label == '📝 Sign Up').click().run()
        return app

    @patch('dotenv.load_dotenv', return_value=False)
    def test_secret_reaches_database_environment(self, _):
        with patch.dict(os.environ, {}, clear=True):
            app = self.app()
            self.assertFalse(app.exception)
            self.assertEqual(os.environ['TIMESCALE_SERVICE_URL'], 'postgres://test-only.invalid/db')

    @patch('dotenv.load_dotenv', return_value=False)
    def test_blank_and_duplicate_signup(self, _):
        app = self.signup_form(self.app())
        next(b for b in app.button if b.label == 'Sign Up').click().run()
        self.assertTrue(any('Enter a username' in e.value for e in app.error))
        app.text_input[0].set_value('tester');app.text_input[1].set_value('test-password')
        with patch('account_database.AccountDatabase.signup', side_effect=ValueError('That username is already taken.')):
            next(b for b in app.button if b.label == 'Sign Up').click().run()
        self.assertFalse(app.exception)
        self.assertTrue(any('already taken' in e.value for e in app.error))
        self.assertFalse(app.session_state['signed_up'])

    @patch('dotenv.load_dotenv', return_value=False)
    def test_successful_signup_opens_questionnaire(self, _):
        app = self.signup_form(self.app())
        app.text_input[0].set_value('tester');app.text_input[1].set_value('test-password')
        def signup(account, username, password):
            account.user_id = 'test-uid'
            return dict(user_id='test-uid', identity='cloud:test-uid', name='tester', answers=[], questionnaire_done=False)
        with patch('account_database.AccountDatabase.signup', new=signup):
            next(b for b in app.button if b.label == 'Sign Up').click().run()
        self.assertFalse(app.exception)
        self.assertTrue(app.session_state['signed_up'])
        self.assertEqual(app.session_state['user_id'],'test-uid')
        self.assertIsNotNone(app.button(key='answer_a'))
