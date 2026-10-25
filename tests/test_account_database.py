import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from account_database import AccountDatabase

ROOT=Path(__file__).resolve().parents[1]
UID='37ad5c46-3df7-49a1-86e6-335ebc972a86'

class AccountDatabaseTests(unittest.TestCase):
    def test_wrong_legacy_password_never_migrates_an_account(self):
        account=AccountDatabase(ROOT);account._ready=True
        with patch('account_database.store.login',return_value=None),patch.object(account,'legacy_accounts',return_value={'old':{'password':'secret'}}),patch('account_database.store.import_verified_legacy') as migrate:
            self.assertIsNone(account.login('old','wrong'))
            migrate.assert_not_called()

    def test_verified_legacy_account_keeps_its_presage_identity(self):
        account=AccountDatabase(ROOT);account._ready=True
        legacy={'password':'old-secret','name':'Old user'}
        with patch('account_database.store.login',return_value=None),patch.object(account,'legacy_accounts',return_value={'old':legacy}),patch('account_database.store.import_verified_legacy',return_value=UID) as migrate,patch('account_database.store.get_preferences',return_value={'answers':{},'settings':{'legacy_measurement_identity':'old','display_name':'Old user'}}):
            result=account.login('old','old-secret')
            self.assertEqual(result['identity'],'old');self.assertEqual(result['user_id'],UID)
            migrate.assert_called_once_with('old','old-secret',legacy)

    def test_cloud_username_alone_never_grants_old_local_reports(self):
        account=AccountDatabase(ROOT);account._ready=True
        with patch('account_database.store.login',return_value={'user_id':UID,'username':'old'}),patch.object(account,'legacy_accounts',return_value={'old':{'password':'different-password'}}),patch('account_database.store.get_preferences',return_value={'answers':{},'settings':{}}),patch('account_database.store.update_settings') as update:
            result=account.login('old','cloud-password')
            self.assertEqual(result['identity'],'cloud:'+UID)
            update.assert_not_called()

    def test_signup_does_not_take_over_a_legacy_username(self):
        account=AccountDatabase(ROOT);account._ready=True
        with patch.object(account,'legacy_accounts',return_value={'Old':{}}),patch('account_database.store.signup') as signup:
            with self.assertRaises(ValueError):account.signup('old','new-password')
            signup.assert_not_called()

    def test_failed_intro_save_does_not_replace_cached_preferences(self):
        account=AccountDatabase(ROOT);account.user_id=UID;account.identity='old';account.profile={'answers':[],'name':'Old'}
        with patch.object(account,'question_banks',return_value=([],[])),patch('account_database.v3.save_intro',side_effect=ConnectionError),patch('account_database.store.update_settings') as settings:
            with self.assertRaises(ConnectionError):account.save_current({'old':{'answers':[{'choice':'A'}]}})
            self.assertEqual(account.profile['answers'],[]);settings.assert_not_called()
        with self.assertRaises(PermissionError):account.save_current({'another-user':{}})

    def test_normalized_session_answers_preserve_original_choices(self):
        account=AccountDatabase(ROOT);account.user_id=UID;account.identity='old';account.profile={'answers':[],'name':'Old'}
        questions=json.loads((ROOT/'data'/'session questionnaire.json').read_text())['items']
        raw={q['field']:q['options']['A']['value'] for q in questions}
        effective={**raw,'breathing_allowed':False}
        plan={'session_choices':effective,'duration_minutes':3,'voice_choice':'Female voice 1'}
        with patch.object(account,'question_banks',return_value=([],questions)),patch('account_database.store.update_settings'),patch('account_database.store.start_session',return_value='session-id') as start,patch('account_database.v3.save_pre_session') as before:
            self.assertEqual(account.start_session(plan,'whisper-voice',raw),'session-id')
            self.assertEqual(start.call_args.args[1],raw)
            self.assertEqual(start.call_args.args[2]['choices'],effective)
            self.assertEqual(before.call_args.args[2],{q['id']:'A' for q in questions})

    def test_legacy_login_ignores_case_and_surrounding_spaces_but_preserves_owner(self):
        account=AccountDatabase(ROOT);account._ready=True
        legacy={'password':'legacy-password','name':'Existing user'}
        with patch('account_database.store.login',return_value=None),patch.object(account,'legacy_accounts',return_value={'Chen_y':legacy}),patch('account_database.store.import_verified_legacy',return_value=UID) as migrate,patch('account_database.store.get_preferences',return_value={'answers':{},'settings':{'legacy_measurement_identity':'Chen_y'}}):
            result=account.login('  chen_Y  ','legacy-password')
            self.assertEqual(result['identity'],'Chen_y')
            migrate.assert_called_once_with('Chen_y','legacy-password',legacy)

    def test_short_existing_username_can_authenticate_without_relaxing_signup(self):
        import chronos_store as store
        from unittest.mock import MagicMock
        conn=MagicMock()
        conn.execute.return_value.fetchone.return_value=(UID,store._hash_password('existing-password'))
        with patch.object(store,'connect') as connect:
            connect.return_value.__enter__.return_value=conn
            self.assertEqual(store.login('a','existing-password')['user_id'],UID)
            with self.assertRaises(ValueError):store.signup('a','new-password')
