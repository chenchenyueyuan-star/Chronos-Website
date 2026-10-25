"""Connect the Chronos Streamlit UI to the additive TigerData v3 schema.

Requires database_v3/ with the applied and seeded v3 schema.
All user/session writes are scoped to a user or verified session.  This module
contains no credentials, no destructive migrations and no Streamlit UI code.
"""
from __future__ import annotations

from typing import Any

from database_v3 import chronos_tigerdata_v3 as db


EXPECTED_SIGNUP = 20
EXPECTED_PRE = 8


def load_question_banks() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Load the 20 signup and 8 pre-session question definitions from TigerData."""
    signup = db.signup_questions()
    before = db.session_questions('pre')
    if len(signup) != EXPECTED_SIGNUP or len(before) != EXPECTED_PRE:
        raise ValueError('TigerData questionnaire question counts do not match the seeded schema.')
    if not all(q.get('id') and q.get('field') and q.get('options') for q in signup + before):
        raise ValueError('Some TigerData questions are incomplete.')
    return signup, before


def restore_intro_entries(saved: Any, questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert both older list-of-answers and v3 field/value JSON to UI entries."""
    if isinstance(saved, list):
        results = []
        by_field = {q['field']: q for q in questions}
        for item in saved:
            if not isinstance(item, dict):
                continue
            q = by_field.get(item.get('field'))
            if not q:
                continue
            choice = item.get('choice')
            if choice not in ('A', 'B'):
                choice = next((code for code in ('A', 'B')
                               if q['options'][code]['value'] == item.get('value')), None)
            if choice in ('A', 'B'):
                results.append(_display_entry(q, choice))
        return results

    if not isinstance(saved, dict):
        return []
    results = []
    for q in questions:
        if q['field'] not in saved:
            continue
        value = saved[q['field']]
        code = next((c for c in ('A', 'B') if q['options'][c]['value'] == value), None)
        if code is not None:
            results.append(_display_entry(q, code))
    return results


def _display_entry(q: dict[str, Any], code: str) -> dict[str, Any]:
    option = q['options'][code]
    return {'Question': q['text'], 'Answer': option['text'], 'field': q['field'],
            'question_id': q['id'], 'value': option['value'], 'choice': code}


def intro_choice_map(entries: list[dict[str, Any]], questions: list[dict[str, Any]]) -> dict[str, str]:
    """Build exact question-ID -> A/B responses for the v3 normalised tables."""
    by_field = {entry.get('field'): entry for entry in entries if isinstance(entry, dict)}
    if len(entries) != len(questions):
        raise ValueError('Please complete every introduction question before saving.')
    answer_map = {}
    for q in questions:
        entry = by_field.get(q['field'])
        if not entry or entry.get('choice') not in ('A', 'B'):
            raise ValueError('One introduction answer is missing or invalid.')
        answer_map[q['id']] = entry['choice']
    return answer_map


def save_intro(user_id: str, entries: list[dict[str, Any]], questions: list[dict[str, Any]]) -> str:
    """Save the retakable form into the new v3 submissions and answer rows."""
    return db.save_signup_submission(user_id, intro_choice_map(entries, questions))


def save_pre_session(user_id: str, session_id: str, codes: dict[str, str]) -> None:
    """Save selected A/B choices. An empty check-in is intentionally allowed."""
    if codes:
        db.save_session_answers(user_id, session_id, 'pre', codes)


def save_script(user_id: str, session_id: str, title: str, text: str,
                model: str, voice_id: str | None, context: dict[str, Any]) -> str:
    return db.save_generated_script(user_id, session_id, text, title=title,
                                     provider='gemini', model=model, voice_id=voice_id,
                                     source_kind='generated', context=context)


def save_post_feedback(user_id: str, session_id: str, feedback: dict[str, str]) -> None:
    """Persist the existing three-choice post-session form without forcing it into A/B.

    Adds one extra table: chronos_post_feedback.  These existing app questions
    have three choices, so they cannot be stored in the v3 A/B-only table.
    The original sessions.post_answers JSONB is retained for compatibility.
    """
    import uuid
    from psycopg.types.json import Jsonb

    uid, sid = uuid.UUID(user_id), uuid.UUID(session_id)
    allowed = {'feeling_after', 'session_fit', 'try_again'}
    if any(key not in allowed or not isinstance(value, str) or not value.strip()
           for key, value in feedback.items()):
        raise ValueError('Post-session feedback has an unexpected field or value.')
    with db.connect() as conn:
        # Additive table; does not delete existing data or change earlier tables.
        conn.execute('''
            CREATE TABLE IF NOT EXISTS chronos_post_feedback (
                session_id UUID NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
                question_key TEXT NOT NULL,
                response_text TEXT NOT NULL,
                answered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (session_id, question_key)
            )
        ''')
        result = conn.execute('''
            SELECT 1 FROM sessions WHERE session_id=%s AND user_id=%s
        ''', (sid, uid)).fetchone()
        if result is None:
            raise ValueError('Session does not belong to this account.')
        for key, value in feedback.items():
            conn.execute('''
                INSERT INTO chronos_post_feedback(session_id, question_key, response_text)
                VALUES (%s,%s,%s)
                ON CONFLICT (session_id, question_key)
                DO UPDATE SET response_text=EXCLUDED.response_text, answered_at=NOW()
            ''', (sid, key, value))
        conn.execute('''
            UPDATE sessions
            SET post_answers=%s, completed_at=NOW()
            WHERE session_id=%s AND user_id=%s
        ''', (Jsonb(feedback), sid, uid))
