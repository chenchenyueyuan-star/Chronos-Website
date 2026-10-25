"""TigerData PostgreSQL storage for Chronos; no Streamlit imports or secret logging."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import uuid
from contextlib import contextmanager
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / '.env', override=False)
load_dotenv(ROOT / '.env.local', override=False)


def database_url():
    return os.getenv('TIMESCALE_SERVICE_URL') or os.getenv('DATABASE_URL')


def connected():
    return bool(database_url())


@contextmanager
def connect():
    if not connected():
        raise RuntimeError('TigerData is not configured. Add TIMESCALE_SERVICE_URL to .env.')
    import psycopg
    with psycopg.connect(database_url(), connect_timeout=20) as conn:
        yield conn


def initialize():
    """Safe to repeat; adds missing columns without removing existing records."""
    with connect() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS user_profiles (
                user_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                intro_answers JSONB NOT NULL DEFAULT '{}'::jsonb,
                settings JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS intro_questionnaire_history (
                response_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id UUID NOT NULL REFERENCES user_profiles(user_id),
                answers JSONB NOT NULL,
                completed_at TIMESTAMPTZ DEFAULT NOW()
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS sessions (
                session_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id UUID NOT NULL REFERENCES user_profiles(user_id),
                pre_answers JSONB,
                post_answers JSONB,
                started_at TIMESTAMPTZ DEFAULT NOW(),
                completed_at TIMESTAMPTZ
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS chronos_auth_v2 (
                username TEXT PRIMARY KEY,
                user_id UUID NOT NULL UNIQUE REFERENCES user_profiles(user_id),
                password_hash TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT username_not_blank CHECK (length(trim(username)) > 0)
            )
        ''')
        # Some earlier Chronos builds created a chronos_accounts table with a
        # different shape. Don't overwrite it; use chronos_auth_v2 instead.
        # Preserve earlier hashed accounts when the legacy columns match.
        old_columns = {row[0] for row in conn.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'chronos_accounts'
        """).fetchall()}
        if {'username', 'user_id', 'password_hash'}.issubset(old_columns):
            conn.execute("""
                INSERT INTO chronos_auth_v2(username, user_id, password_hash)
                SELECT a.username, a.user_id, a.password_hash
                FROM chronos_accounts a
                JOIN user_profiles p ON p.user_id = a.user_id
                WHERE a.username IS NOT NULL
                  AND a.password_hash LIKE 'pbkdf2_sha256$%'
                ON CONFLICT (username) DO NOTHING
            """)
        conn.execute("ALTER TABLE sessions ADD COLUMN IF NOT EXISTS session_settings JSONB NOT NULL DEFAULT '{}'::jsonb")
        conn.execute("ALTER TABLE sessions ADD COLUMN IF NOT EXISTS script_text TEXT")
        conn.execute("ALTER TABLE sessions ADD COLUMN IF NOT EXISTS session_title TEXT")
        conn.execute("ALTER TABLE sessions ADD COLUMN IF NOT EXISTS duration_minutes INTEGER")
        conn.execute("ALTER TABLE sessions ADD COLUMN IF NOT EXISTS voice_id TEXT")


def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    iterations = 310_000
    key = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, iterations)
    return f'pbkdf2_sha256${iterations}${salt.hex()}${key.hex()}'


def _verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, count, salt, expected = encoded.split('$')
        if algorithm != 'pbkdf2_sha256':
            return False
        result = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt), int(count))
        return hmac.compare_digest(result, bytes.fromhex(expected))
    except (ValueError, TypeError, OverflowError):
        return False


def _username(value):
    name = value.strip().lower()
    if not (3 <= len(name) <= 40) or not all(x.isalnum() or x in '._-' for x in name):
        raise ValueError('Username must be 3–40 characters (letters, numbers, . _ -).')
    return name


def signup(username, password):
    name = _username(username)
    if not (8 <= len(password) <= 128):
        raise ValueError('Password must be between 8 and 128 characters.')
    import psycopg
    with connect() as conn:
        try:
            user_id = conn.execute('INSERT INTO user_profiles DEFAULT VALUES RETURNING user_id').fetchone()[0]
            conn.execute('INSERT INTO chronos_auth_v2(username, user_id, password_hash) VALUES (%s,%s,%s)',
                         (name, user_id, _hash_password(password)))
        except psycopg.errors.UniqueViolation as exc:
            raise ValueError('That username is already taken.') from exc
    return str(user_id)


def login(username, password):
    # Existing accounts may predate the 3-character signup rule.
    name = username.strip().lower()
    if not name:
        return None
    with connect() as conn:
        row = conn.execute('SELECT user_id, password_hash FROM chronos_auth_v2 WHERE username=%s', (name,)).fetchone()
    if not row or not _verify_password(password, row[1]):
        return None
    return {'user_id': str(row[0]), 'username': name}


def get_preferences(user_id):
    with connect() as conn:
        row = conn.execute('SELECT intro_answers, settings FROM user_profiles WHERE user_id=%s', (uuid.UUID(user_id),)).fetchone()
    if not row:
        raise ValueError('Profile not found.')
    return {'answers': row[0] or {}, 'settings': row[1] or {}}


def save_intro(user_id, answers):
    data = json.dumps(answers)
    with connect() as conn:
        with conn.transaction():
            cur = conn.execute('UPDATE user_profiles SET intro_answers=%s::jsonb WHERE user_id=%s',
                               (data, uuid.UUID(user_id)))
            if cur.rowcount != 1:
                raise ValueError('Profile not found.')
            conn.execute('INSERT INTO intro_questionnaire_history(user_id, answers) VALUES (%s,%s::jsonb)',
                         (uuid.UUID(user_id), data))


def update_settings(user_id, settings):
    with connect() as conn:
        cur = conn.execute('UPDATE user_profiles SET settings=%s::jsonb WHERE user_id=%s',
                           (json.dumps(settings), uuid.UUID(user_id)))
        if cur.rowcount != 1:
            raise ValueError('Profile not found.')


def start_session(user_id, pre_answers, settings, duration_minutes, voice_id):
    with connect() as conn:
        row = conn.execute('''
            INSERT INTO sessions(user_id, pre_answers, session_settings, duration_minutes, voice_id)
            VALUES (%s,%s::jsonb,%s::jsonb,%s,%s) RETURNING session_id
        ''', (uuid.UUID(user_id), json.dumps(pre_answers), json.dumps(settings), duration_minutes, voice_id)).fetchone()
    return str(row[0])


def attach_script(user_id, session_id, title, script):
    with connect() as conn:
        cur = conn.execute('''UPDATE sessions SET session_title=%s, script_text=%s
                               WHERE session_id=%s AND user_id=%s''',
                           (title, script, uuid.UUID(session_id), uuid.UUID(user_id)))
        if cur.rowcount != 1:
            raise ValueError('Session not found for this account.')


def finish_session(user_id, session_id, post_answers):
    with connect() as conn:
        cur = conn.execute('''UPDATE sessions SET post_answers=%s::jsonb, completed_at=NOW()
                               WHERE session_id=%s AND user_id=%s''',
                           (json.dumps(post_answers), uuid.UUID(session_id), uuid.UUID(user_id)))
        if cur.rowcount != 1:
            raise ValueError('Session not found for this account.')


def session_count(user_id):
    with connect() as conn:
        return conn.execute('SELECT count(*) FROM sessions WHERE user_id=%s', (uuid.UUID(user_id),)).fetchone()[0]


def safe_db_error(exc):
    """Human-safe, credential-free status for the app and for support requests."""
    if not connected():
        return 'Account database is not configured. Add TIMESCALE_SERVICE_URL to your hosting Secrets or environment variables, then restart the app.'
    code = getattr(exc, 'sqlstate', None)
    if code == '42P01':
        return 'A database table is missing (42P01). Use Retry TigerData connection to initialise tables.'
    if code == '42501':
        return 'The database user does not have permission to create or edit tables (42501).'
    if code == '23505':
        return 'That account already exists (23505). Try signing in.'
    if code == '23503':
        return 'A linked user profile is missing (23503).'
    if code == '42703':
        return 'A database column is missing (42703). Run the included check_tigerdata.py to identify the table.'
    if code == '28P01':
        return 'Database authentication failed (28P01). Check the locally stored password.'
    if code and code.startswith('08'):
        return f'Network/database connection failed ({code}). Please retry.'
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return 'The database could not be reached. Check your internet and TigerData service.'
    try:
        import psycopg
        if isinstance(exc, psycopg.OperationalError):
            return 'The database connection failed or timed out. Check the TigerData service and .env.'
    except ImportError:
        pass
    if isinstance(exc, (KeyError, ValueError, TypeError)):
        return f'Application data error ({type(exc).__name__}).'
    # SQL error class and code only, never raw text: raw psycopg exceptions can contain URLs.
    error_type = type(exc).__name__
    return f'Unexpected database error ({error_type}{", SQLSTATE " + code if code else ""}).'


def import_verified_legacy(username, password, profile):
    """Caller has verified the old password; never overwrite an existing cloud login."""
    name = username.strip().lower()
    if not name:
        raise ValueError('Enter your existing username.')
    answers = {a['field']: a['value'] for a in profile.get('answers', [])
               if isinstance(a, dict) and 'field' in a and 'value' in a}
    settings = {'display_name': profile.get('name') or username,
                'legacy_measurement_identity': username}
    if 'measurement_consent' in profile:
        settings['measurement_consent'] = profile['measurement_consent']
    with connect() as conn:
        user_id = conn.execute('''INSERT INTO user_profiles(intro_answers, settings)
                                 VALUES (%s::jsonb,%s::jsonb) RETURNING user_id''',
                               (json.dumps(answers), json.dumps(settings))).fetchone()[0]
        conn.execute('''INSERT INTO chronos_auth_v2(username,user_id,password_hash)
                        VALUES (%s,%s,%s)''', (name,user_id,_hash_password(password)))
        if answers:
            conn.execute('INSERT INTO intro_questionnaire_history(user_id,answers) VALUES (%s,%s::jsonb)',
                         (user_id,json.dumps(answers)))
    return str(user_id)


def google_identity(subject, email, display_name, legacy=None):
    """Resolve a verified Google subject, separate from password usernames."""
    if not subject or not email:
        raise ValueError('Verified Google identity is missing.')
    legacy = legacy if isinstance(legacy, dict) else {}
    answers = {a['field']: a['value'] for a in legacy.get('answers', [])
               if isinstance(a, dict) and 'field' in a and 'value' in a}
    settings = {'display_name': display_name, 'legacy_measurement_identity': email}
    if 'measurement_consent' in legacy:
        settings['measurement_consent'] = legacy['measurement_consent']
    with connect() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS chronos_external_auth (
            provider TEXT NOT NULL, subject TEXT NOT NULL,
            user_id UUID NOT NULL REFERENCES user_profiles(user_id),
            PRIMARY KEY(provider,subject))''')
        # Serialize creation per external subject; a concurrent first sign-in must
        # not create two profiles for the same verified identity.
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ('google:'+subject,))
        row = conn.execute("SELECT user_id FROM chronos_external_auth WHERE provider='google' AND subject=%s", (subject,)).fetchone()
        if row:
            return str(row[0])
        user_id = conn.execute('''INSERT INTO user_profiles(intro_answers,settings)
                                 VALUES (%s::jsonb,%s::jsonb) RETURNING user_id''',
                               (json.dumps(answers),json.dumps(settings))).fetchone()[0]
        conn.execute("INSERT INTO chronos_external_auth VALUES ('google',%s,%s)", (subject,user_id))
    return str(user_id)
