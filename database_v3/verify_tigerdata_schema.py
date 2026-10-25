"""Read-only v3 TigerData schema verification; displays no credentials or answers."""
import os
from pathlib import Path
import psycopg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / '.env')
load_dotenv(ROOT.parent / '.env')
url = os.getenv('TIMESCALE_SERVICE_URL')
if not url:
    raise RuntimeError('TIMESCALE_SERVICE_URL missing in local .env')

names = (
    'user_profiles', 'chronos_auth_v2', 'sessions',
    'chronos_signup_questions', 'chronos_session_questions',
    'chronos_signup_submissions', 'chronos_signup_answers',
    'chronos_session_answers', 'chronos_generated_scripts'
)
with psycopg.connect(url, connect_timeout=10) as conn:
    with conn.cursor() as cur:
        cur.execute('''
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name=ANY(%s)
        ''', (list(names),))
        found = {row[0] for row in cur.fetchall()}
        for name in names:
            print(('OK     ' if name in found else 'MISSING') + ' ' + name)
        if {'chronos_signup_questions', 'chronos_session_questions'} <= found:
            cur.execute('SELECT COUNT(*) FROM chronos_signup_questions WHERE enabled')
            print('Signup questionnaire question count:', cur.fetchone()[0])
            cur.execute("SELECT COUNT(*) FROM chronos_session_questions WHERE enabled AND phase='pre'")
            print('Pre-session question count:', cur.fetchone()[0])
print('Read-only check complete. No user details, passwords, or answers printed.')
