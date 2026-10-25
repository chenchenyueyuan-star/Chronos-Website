"""Import the real Chronos A/B question definitions into TigerData.

Run AFTER 01_tigerdata_schema.sql, from the folder containing this script and
its questionnaire JSONs. This does not save any user responses.
"""
from __future__ import annotations
import json
import os
from pathlib import Path

from dotenv import load_dotenv
import psycopg
from psycopg.types.json import Jsonb

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / '.env')
load_dotenv(ROOT.parent / '.env')
load_dotenv(ROOT / '.env.local')
load_dotenv(ROOT.parent / '.env.local')


def find_file(filename: str) -> Path:
    for candidate in (ROOT / filename, ROOT / 'data' / filename,
                      ROOT.parent / filename, ROOT.parent / 'data' / filename):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f'{filename} was not found next to this script or in data/')


def load_form(filename: str) -> dict:
    return json.loads(find_file(filename).read_text(encoding='utf-8-sig'))


def seed(connection, intro: dict, session: dict) -> tuple[int, int]:
    intro_items = intro.get('items', [])
    session_items = session.get('items', [])
    if len(intro_items) != 20:
        raise ValueError(f'Expected 20 signup questions, found {len(intro_items)}')
    if len(session_items) != 8:
        raise ValueError(f'Expected 8 pre-session questions, found {len(session_items)}')

    with connection.transaction():
        for order, question in enumerate(intro_items, start=1):
            connection.execute('''
                INSERT INTO chronos_signup_questions
                    (question_id, question_order, category, field_key, question_text,
                     choices, source_version, enabled)
                VALUES (%s,%s,%s,%s,%s,%s,%s,TRUE)
                ON CONFLICT (question_id) DO UPDATE SET
                    question_order = EXCLUDED.question_order,
                    category = EXCLUDED.category,
                    field_key = EXCLUDED.field_key,
                    question_text = EXCLUDED.question_text,
                    choices = EXCLUDED.choices,
                    source_version = EXCLUDED.source_version,
                    updated_at = NOW()
            ''', (question['id'], order, question.get('category'), question.get('field'),
                  question['text'], Jsonb(question['options']),
                  str(intro.get('metadata', {}).get('version', 'unknown'))))
        for order, question in enumerate(session_items, start=1):
            connection.execute('''
                INSERT INTO chronos_session_questions
                    (phase, question_id, question_order, field_key, question_text,
                     choices, source_version, enabled)
                VALUES ('pre',%s,%s,%s,%s,%s,%s,TRUE)
                ON CONFLICT (phase, question_id) DO UPDATE SET
                    question_order = EXCLUDED.question_order,
                    field_key = EXCLUDED.field_key,
                    question_text = EXCLUDED.question_text,
                    choices = EXCLUDED.choices,
                    source_version = EXCLUDED.source_version,
                    updated_at = NOW()
            ''', (question['id'], order, question.get('field'), question['text'],
                  Jsonb(question['options']),
                  str(session.get('metadata', {}).get('version', 'unknown'))))
    return len(intro_items), len(session_items)


def main() -> None:
    url = os.getenv('TIMESCALE_SERVICE_URL')
    if not url:
        raise RuntimeError('TIMESCALE_SERVICE_URL missing from your private .env')
    intro = load_form('intro questionnaire.json')
    session = load_form('session questionnaire.json')
    with psycopg.connect(url, connect_timeout=10) as conn:
        n_intro, n_pre = seed(conn, intro, session)
    print(f'Seeded {n_intro} signup and {n_pre} pre-session questions; no user answers changed.')
    if session.get('metadata', {}).get('question_count') != n_pre:
        print('Note: session JSON metadata says 9 but the actual file contains 8 questions.')
    print('Post-session questionnaire definitions are intentionally not invented or seeded.')


if __name__ == '__main__':
    main()
