"""Chronos v3 TigerData helpers. Import these in the app after schema setup.

Does not replace existing chronos_store.py; it adds normalised storage while
mirroring to the older JSONB fields for compatibility with the current UI.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

import psycopg
from psycopg.types.json import Jsonb
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / '.env')
load_dotenv(ROOT.parent / '.env')
load_dotenv(ROOT / '.env.local')
load_dotenv(ROOT.parent / '.env.local')


def connect() -> psycopg.Connection:
    url = os.getenv('TIMESCALE_SERVICE_URL')
    if not url:
        raise RuntimeError('Configure TIMESCALE_SERVICE_URL in your private .env file.')
    return psycopg.connect(url, connect_timeout=10)


def signup_questions() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute('''
            SELECT question_id, question_order, category, field_key, question_text,
                   choices, source_version
            FROM chronos_signup_questions WHERE enabled ORDER BY question_order
        ''').fetchall()
    return [dict(zip(('id', 'order', 'category', 'field', 'text', 'options', 'version'), r)) for r in rows]


def session_questions(phase: str) -> list[dict[str, Any]]:
    if phase not in ('pre', 'post'):
        raise ValueError('phase must be pre or post')
    with connect() as conn:
        rows = conn.execute('''
            SELECT question_id, question_order, field_key, question_text,
                   choices, source_version
            FROM chronos_session_questions
            WHERE phase = %s AND enabled ORDER BY question_order
        ''', (phase,)).fetchall()
    return [dict(zip(('id', 'order', 'field', 'text', 'options', 'version'), r)) for r in rows]


def _validated_answers(conn, table: str, phase: str | None, answers: dict[str, str]) -> tuple[list[tuple], dict, str]:
    if not isinstance(answers, dict) or not answers:
        raise ValueError('Answers must be a nonempty dictionary: question ID -> A or B')
    if table == 'signup':
        rows = conn.execute('''
            SELECT question_id, field_key, choices, source_version
            FROM chronos_signup_questions WHERE enabled
        ''').fetchall()
    else:
        rows = conn.execute('''
            SELECT question_id, field_key, choices, source_version
            FROM chronos_session_questions WHERE enabled AND phase=%s
        ''', (phase,)).fetchall()
    lookup = {r[0]: r[1:] for r in rows}
    prepared = []
    legacy = {}
    versions = set()
    for question_id, answer_code in answers.items():
        if question_id not in lookup or answer_code not in ('A', 'B'):
            raise ValueError(f'Unknown question or invalid A/B choice: {question_id!r}')
        field, choices, version = lookup[question_id]
        option = choices[answer_code]
        value = option.get('value', answer_code)
        prepared.append((question_id, answer_code, value))
        legacy[field or question_id] = value
        versions.add(version)
    return prepared, legacy, ','.join(sorted(versions))


def save_signup_submission(user_id: str, answers: dict[str, str]) -> str:
    """Save a full/retaken questionnaire; preserve current + historical answers."""
    uid = uuid.UUID(user_id)
    with connect() as conn:
        with conn.transaction():
            prepared, current, form_version = _validated_answers(conn, 'signup', None, answers)
            (submission_id,) = conn.execute('''
                INSERT INTO chronos_signup_submissions(user_id, questionnaire_version)
                VALUES (%s, %s) RETURNING submission_id
            ''', (uid, form_version)).fetchone()
            for question_id, code, value in prepared:
                conn.execute('''
                    INSERT INTO chronos_signup_answers
                        (submission_id, question_id, answer_code, answer_value)
                    VALUES (%s,%s,%s,%s)
                ''', (submission_id, question_id, code, Jsonb(value)))
            # The existing Streamlit code can still read the user's current profile.
            updated = conn.execute('''
                UPDATE user_profiles SET intro_answers=%s, updated_at=NOW()
                WHERE user_id=%s RETURNING user_id
            ''', (Jsonb(current), uid)).fetchone()
            if updated is None:
                raise ValueError('Unknown user_id')
            # Maintain legacy history for components still reading that table.
            conn.execute('''
                INSERT INTO intro_questionnaire_history(user_id, answers)
                VALUES (%s,%s)
            ''', (uid, Jsonb(current)))
    return str(submission_id)


def save_session_answers(user_id: str, session_id: str, phase: str,
                         answers: dict[str, str]) -> None:
    """Persist one pre/post check-in; reflects it in legacy sessions JSONB."""
    if phase not in ('pre', 'post'):
        raise ValueError('phase must be pre or post')
    uid, sid = uuid.UUID(user_id), uuid.UUID(session_id)
    with connect() as conn:
        with conn.transaction():
            found = conn.execute('''
                SELECT 1 FROM sessions WHERE user_id=%s AND session_id=%s
            ''', (uid, sid)).fetchone()
            if found is None:
                raise ValueError('Session not found for this user')
            prepared, legacy, _ = _validated_answers(conn, 'session', phase, answers)
            for question_id, code, value in prepared:
                conn.execute('''
                    INSERT INTO chronos_session_answers
                        (session_id, phase, question_id, answer_code, answer_value)
                    VALUES (%s,%s,%s,%s,%s)
                    ON CONFLICT (session_id, phase, question_id)
                    DO UPDATE SET answer_code=EXCLUDED.answer_code,
                                  answer_value=EXCLUDED.answer_value,
                                  answered_at=NOW()
                ''', (sid, phase, question_id, code, Jsonb(value)))
            column = 'pre_answers' if phase == 'pre' else 'post_answers'
            # Column selection is fixed by the validated phase (not user SQL input).
            conn.execute(f'''UPDATE sessions SET {column}=%s WHERE session_id=%s''',
                         (Jsonb(legacy), sid))


def save_generated_script(user_id: str, session_id: str, script_text: str,
                          title: str = '', provider: str | None = None,
                          model: str | None = None, voice_id: str | None = None,
                          source_kind: str = 'generated',
                          context: dict[str, Any] | None = None) -> str:
    """Store a script version and update the legacy session's chosen text."""
    if not script_text or not script_text.strip():
        raise ValueError('script_text must not be empty')
    if source_kind not in ('generated', 'user_supplied', 'edited'):
        raise ValueError('Invalid source_kind')
    uid, sid = uuid.UUID(user_id), uuid.UUID(session_id)
    with connect() as conn:
        with conn.transaction():
            if not conn.execute('''
                SELECT 1 FROM sessions WHERE user_id=%s AND session_id=%s
            ''', (uid, sid)).fetchone():
                raise ValueError('Session does not belong to the user')
            conn.execute('''
                UPDATE chronos_generated_scripts SET is_selected=FALSE
                WHERE session_id=%s
            ''', (sid,))
            (script_id,) = conn.execute('''
                INSERT INTO chronos_generated_scripts
                    (user_id, session_id, script_title, script_text, source_kind,
                     generator_provider, generator_model, selected_voice_id,
                     personalization_context)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING script_id
            ''', (uid, sid, title, script_text, source_kind, provider, model,
                  voice_id, Jsonb(context or {}))).fetchone()
            conn.execute('''
                UPDATE sessions SET session_title=%s, script_text=%s, voice_id=%s
                WHERE session_id=%s
            ''', (title, script_text, voice_id, sid))
    return str(script_id)
