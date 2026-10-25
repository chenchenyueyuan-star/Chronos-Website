"""Offline structural tests. Does not connect to TigerData or Snowflake."""
from pathlib import Path
import ast
import json

ROOT = Path(__file__).resolve().parent
for name in ('apply_tigerdata_schema.py','seed_chronos_questionnaires.py',
             'chronos_tigerdata_v3.py','chronos_snowflake_v3.py'):
    ast.parse((ROOT/name).read_text(encoding='utf-8'), filename=name)

intro = json.loads((ROOT/'intro questionnaire.json').read_text(encoding='utf-8'))
session = json.loads((ROOT/'session questionnaire.json').read_text(encoding='utf-8'))
assert len(intro['items']) == 20
assert len(session['items']) == 8
assert len(set(q['id'] for q in intro['items'])) == 20
assert len(set(q['id'] for q in session['items'])) == 8
for q in intro['items'] + session['items']:
    assert set(q['options']) == {'A','B'}

pg = (ROOT/'01_tigerdata_schema.sql').read_text(encoding='utf-8')
sf = (ROOT/'02_snowflake_schema.sql').read_text(encoding='utf-8')
for name in ('chronos_signup_questions','chronos_session_questions',
             'chronos_signup_submissions','chronos_signup_answers',
             'chronos_session_answers','chronos_generated_scripts'):
    assert f'CREATE TABLE IF NOT EXISTS {name}' in pg
for name in ('CHRONOS_MEDIA_ASSETS','CHRONOS_BIOMETRIC_SAMPLES',
             'CHRONOS_SESSION_METRICS','CHRONOS_SESSION_GRAPHS'):
    assert f'CREATE TABLE IF NOT EXISTS {name}' in sf
for script in (pg,sf):
    statements = '\n'.join(line for line in script.splitlines() if not line.lstrip().startswith('--')).upper()
    assert 'DROP TABLE' not in statements
    assert 'TRUNCATE' not in statements
print('PASS: Python syntax, 20+8 real questions, A/B choices and all 10 new tables.')
print('OFFLINE ONLY: no network/database permissions or live integrations tested.')
