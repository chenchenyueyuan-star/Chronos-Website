"""Bridge the existing UI to the downloaded TigerData/Snowflake integration.

Identity used by local Presage reports remains stable across account migration.
No cloud writes occur merely by importing this module or rendering a guest page.
"""
from copy import deepcopy
import hmac
import json
from pathlib import Path

import chronos_store as store
import chronos_v3_bridge as v3


class AccountDatabase:
    def __init__(self, root):
        self.root = Path(root)
        self.user_id = None
        self.identity = None
        self.profile = None
        self._ready = False
        self._banks = None

    def initialize(self):
        if not self._ready:
            store.initialize()
            self._ready = True

    def legacy_accounts(self):
        path = self.root / 'users_db.json'
        try:
            data = json.loads(path.read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def legacy_account(self, username):
        records = self.legacy_accounts()
        if username in records:
            return username, records[username]
        matches = [(key, value) for key, value in records.items()
                   if isinstance(key, str) and key.strip().casefold() == username.strip().casefold()]
        return matches[0] if len(matches) == 1 else (None, None)

    def bind(self, user_id, identity):
        profile = store.get_preferences(user_id)
        settings = profile.get('settings') or {}
        questions = json.loads((self.root / 'data' / 'intro questionnaire.json').read_text())['items']
        entries = v3.restore_intro_entries(profile['answers'], questions)
        self.user_id = user_id
        self.identity = settings.get('legacy_measurement_identity') or 'cloud:' + str(user_id)
        self.profile = {**settings, 'name': settings.get('display_name') or identity,
                        'answers': entries, 'questionnaire_done': len(entries) == len(questions)}
        return {'user_id': self.user_id, 'identity': self.identity, **deepcopy(self.profile)}

    def signup(self, username, password):
        self.initialize()
        if username.strip().lower() in {str(key).lower() for key in self.legacy_accounts()}:
            raise ValueError('This username has an existing local account. Sign in to keep its saved results.')
        user_id = store.signup(username, password)
        return self.bind(user_id, username.strip().lower())

    def login(self, username, password):
        self.initialize()
        account = store.login(username, password)
        if account:
            legacy_identity, legacy = self.legacy_account(username)
            if isinstance(legacy, dict) and isinstance(legacy.get('password'), str) and hmac.compare_digest(legacy['password'].encode(), password.encode()):
                settings = store.get_preferences(account['user_id']).get('settings') or {}
                settings['legacy_measurement_identity'] = legacy_identity
                settings.setdefault('display_name', legacy.get('name') or username)
                if 'measurement_consent' in legacy:
                    settings.setdefault('measurement_consent', legacy['measurement_consent'])
                store.update_settings(account['user_id'], settings)
            return self.bind(account['user_id'], account['username'])
        # Existing local accounts are migrated only after their password is verified.
        legacy_identity, legacy = self.legacy_account(username)
        if not isinstance(legacy, dict) or not isinstance(legacy.get('password'), str):
            return None
        if not hmac.compare_digest(legacy['password'].encode(), password.encode()):
            return None
        user_id = store.import_verified_legacy(legacy_identity, password, legacy)
        return self.bind(user_id, legacy_identity)

    def google(self, subject, email, name):
        """Caller must already verify the Google token and its email_verified claim."""
        self.initialize()
        legacy = self.legacy_accounts().get(email, {})
        user_id = store.google_identity(subject, email, name, legacy)
        return self.bind(user_id, email)

    def current(self):
        return {self.identity: deepcopy(self.profile)} if self.identity and self.profile else {}

    def question_banks(self):
        if self._banks is None:
            self._banks = v3.load_question_banks()
        return self._banks

    def save_current(self, records):
        if not self.user_id or set(records) != {self.identity}:
            raise PermissionError('Only the signed-in profile can be updated.')
        incoming = deepcopy(records[self.identity])
        if incoming.get('answers') != self.profile.get('answers'):
            questions, _ = self.question_banks()
            v3.save_intro(self.user_id, incoming['answers'], questions)
        settings = {k: v for k, v in incoming.items() if k not in ('answers', 'questionnaire_done', 'password')}
        settings['display_name'] = incoming.get('name', self.identity)
        settings['legacy_measurement_identity'] = self.identity
        store.update_settings(self.user_id, settings)
        self.profile = incoming

    def start_session(self, plan, voice_id, responses=None):
        if not self.user_id:
            raise PermissionError('Sign in before saving a session.')
        _, questions = self.question_banks()
        choices = plan['session_choices'] if responses is None else responses
        codes = {}
        for question in questions:
            field = question['field']
            if field not in choices:
                raise ValueError('A session response is missing.')
            matches = [code for code, option in question['options'].items()
                       if type(option['value']) is type(choices[field]) and option['value'] == choices[field]]
            if len(matches) != 1:
                raise ValueError('The database question choices do not match this app.')
            codes[question['id']] = matches[0]
        profile = self.current()
        profile[self.identity]['narrator'] = plan.get('voice_choice')
        self.save_current(profile)
        session_id = store.start_session(self.user_id, choices, {'choices': plan['session_choices']},
                                         plan['duration_minutes'], voice_id or None)
        v3.save_pre_session(self.user_id, session_id, codes)
        return session_id

    def save_script(self, session_id, session, plan, voice_id):
        text = session.get('script', '')
        if text.strip():
            import os
            v3.save_script(self.user_id, session_id, session['title'], text,
                           os.getenv('GEMINI_MODEL', ''), voice_id or None,
                           {'intro_preferences': plan['onboarding_preferences'],
                            'session_choices': plan['session_choices'],
                            'duration_minutes': plan['duration_minutes']})
        else:
            store.attach_script(self.user_id, session_id, session['title'], '')
        session.update(database_user_id=self.user_id, database_session_id=session_id)
