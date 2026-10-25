"""Snowflake helpers for optional Chronos analytics/media.

This module does NOT collect camera images or infer anxiety. Call these
functions only after explicit per-session opt-in has been recorded in TigerData.
Run 02_snowflake_schema.sql separately before using these helpers.
"""
from __future__ import annotations

import json
import mimetypes
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / '.env')
load_dotenv(ROOT.parent / '.env')
load_dotenv(ROOT / '.env.local')
load_dotenv(ROOT.parent / '.env.local')


def connect_snowflake():
    import snowflake.connector
    required = ('SNOWFLAKE_ACCOUNT', 'SNOWFLAKE_USER', 'SNOWFLAKE_PASSWORD',
                'SNOWFLAKE_WAREHOUSE')
    missing = [key for key in required if not os.getenv(key)]
    if missing:
        raise RuntimeError('Missing private Snowflake configuration: ' + ', '.join(missing))
    arguments = dict(
        account=os.environ['SNOWFLAKE_ACCOUNT'],
        user=os.environ['SNOWFLAKE_USER'],
        password=os.environ['SNOWFLAKE_PASSWORD'],
        warehouse=os.environ['SNOWFLAKE_WAREHOUSE'],
        database='CHRONOS_ANALYTICS',
        schema='WELLNESS',
        login_timeout=15,
    )
    if os.getenv('SNOWFLAKE_ROLE'):
        arguments['role'] = os.environ['SNOWFLAKE_ROLE']
    return snowflake.connector.connect(**arguments)


def _consent(session_id: str, *, media=False, raw=False) -> str:
    """Fail closed. Verify owner and session-specific permissions in TigerData."""
    from .chronos_tigerdata_v3 import connect
    session_uuid = uuid.UUID(session_id)
    with connect() as pg:
        row = pg.execute('''
            SELECT user_id, biometrics_opt_in, media_storage_opt_in, raw_media_opt_in,
                   media_retention_until
            FROM sessions WHERE session_id=%s
        ''', (session_uuid,)).fetchone()
    if row is None:
        raise PermissionError('Session not found in TigerData')
    user_id, biometrics_opt_in, media_storage_opt_in, raw_media_opt_in, expiry = row
    if not biometrics_opt_in and not media:
        raise PermissionError('Biometric recording has not been enabled for this session')
    if media and not media_storage_opt_in:
        raise PermissionError('Media storage has not been enabled for this session')
    if raw and not raw_media_opt_in:
        raise PermissionError('Raw camera/microphone recording has not been enabled')
    if media and raw and expiry is None:
        raise PermissionError('Raw media requires a retention expiry in TigerData')
    if expiry is not None and expiry <= datetime.now(timezone.utc):
        raise PermissionError('This session\'s data retention permission has expired')
    return str(user_id)


def add_biometric_sample(session_id: str, *, offset_seconds: float,
                         heart_rate_bpm: float | None = None,
                         blinks_per_second: float | None = None,
                         emotion_label: str | None = None,
                         emotion_confidence: float | None = None,
                         self_reported_anxiety_level: float | None = None,
                         anxiety_model_estimate: float | None = None,
                         measurement_source: str = 'unspecified',
                         model_version: str | None = None,
                         observed_at: datetime | None = None) -> str:
    """One time point. Labels/estimates are not medical diagnoses."""
    user_id = _consent(session_id)
    if offset_seconds < 0:
        raise ValueError('offset_seconds must be >= 0')
    if blinks_per_second is not None and blinks_per_second < 0:
        raise ValueError('blinks_per_second must be >= 0')
    if emotion_confidence is not None and not (0 <= emotion_confidence <= 1):
        raise ValueError('emotion_confidence must be between 0 and 1')
    observed_at = observed_at or datetime.now(timezone.utc)
    if observed_at.tzinfo is None:
        raise ValueError('observed_at must include a time zone')
    sample_id = str(uuid.uuid4())
    with connect_snowflake() as sf:
        with sf.cursor() as cur:
            cur.execute('''
                INSERT INTO CHRONOS_BIOMETRIC_SAMPLES
                  (SAMPLE_ID, USER_ID, SESSION_ID, OBSERVED_AT, SESSION_OFFSET_SECONDS,
                   HEART_RATE_BPM, BLINKS_PER_SECOND, EMOTION_LABEL, EMOTION_CONFIDENCE,
                   SELF_REPORTED_ANXIETY_LEVEL, ANXIETY_MODEL_ESTIMATE,
                   MEASUREMENT_SOURCE, MODEL_VERSION)
                VALUES (%s,%s,%s,TO_TIMESTAMP_TZ(%s),%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ''', (sample_id, user_id, session_id, observed_at.isoformat(),
                  offset_seconds, heart_rate_bpm, blinks_per_second,
                  emotion_label, emotion_confidence, self_reported_anxiety_level,
                  anxiety_model_estimate, measurement_source, model_version))
    return sample_id


def save_graph_series(session_id: str, graph_type: str,
                      series: list[dict[str, Any]]) -> str:
    """Save reusable chart data; the frontend can redraw it without an image."""
    user_id = _consent(session_id)
    if not isinstance(series, list):
        raise TypeError('series must be a JSON-compatible list of points')
    graph_id = str(uuid.uuid4())
    with connect_snowflake() as sf:
        with sf.cursor() as cur:
            cur.execute('''
                INSERT INTO CHRONOS_SESSION_GRAPHS
                    (GRAPH_ID, USER_ID, SESSION_ID, GRAPH_TYPE, SERIES)
                SELECT %s, %s, %s, %s, PARSE_JSON(%s)
            ''', (graph_id, user_id, session_id, graph_type, json.dumps(series)))
    return graph_id


def save_session_summary(session_id: str, duration_seconds: float,
                         *, average_heart_rate_bpm: float | None = None,
                         average_blinks_per_second: float | None = None,
                         sample_count: int | None = None,
                         anxiety_before: float | None = None,
                         anxiety_after: float | None = None,
                         summary: dict[str, Any] | None = None) -> str:
    user_id = _consent(session_id)
    metric_id = str(uuid.uuid4())
    with connect_snowflake() as sf:
        with sf.cursor() as cur:
            cur.execute('''
                INSERT INTO CHRONOS_SESSION_METRICS
                    (METRIC_ID, USER_ID, SESSION_ID, DURATION_SECONDS,
                     AVERAGE_HEART_RATE_BPM, AVERAGE_BLINKS_PER_SECOND, SAMPLE_COUNT,
                     SELF_REPORTED_ANXIETY_BEFORE, SELF_REPORTED_ANXIETY_AFTER, SUMMARY)
                SELECT %s,%s,%s,%s,%s,%s,%s,%s,%s,PARSE_JSON(%s)
            ''', (metric_id, user_id, session_id, duration_seconds,
                  average_heart_rate_bpm, average_blinks_per_second, sample_count,
                  anxiety_before, anxiety_after, json.dumps(summary or {})))
    return metric_id


def stage_media(session_id: str, file_path: str, media_type: str, *,
                origin: str, raw_capture: bool = False,
                session_offset_seconds: float | None = None,
                expires_at: datetime | None = None) -> str:
    """Opt-in local file upload to a protected Snowflake internal stage.

    Never runs from a Streamlit page render; call only on an explicit user action.
    Does not upload any camera/video file automatically.
    """
    user_id = _consent(session_id, media=True, raw=raw_capture)
    if media_type not in ('image_frame', 'video', 'audio', 'graph_image'):
        raise ValueError('Invalid media_type')
    if origin not in ('camera', 'microphone', 'generated', 'chart_export'):
        raise ValueError('Invalid origin')
    if origin in ('camera', 'microphone') and not raw_capture:
        raise ValueError('Camera/microphone recordings must be marked as raw_capture')
    local = Path(file_path).expanduser().resolve(strict=True)
    if not local.is_file():
        raise ValueError('Media path is not a file')
    if local.suffix.lower() not in ('.png','.jpg','.jpeg','.webp','.mp4','.webm','.mp3','.wav','.ogg'):
        raise ValueError('Unsupported media file format')
    if local.stat().st_size > 50 * 1024 * 1024:
        raise ValueError('File exceeds the 50 MB upload limit')
    if raw_capture and expires_at is None:
        raise ValueError('Set an explicit expiry for raw recordings')
    if expires_at is not None:
        if expires_at.tzinfo is None or expires_at <= datetime.now(timezone.utc):
            raise ValueError('expires_at must be a future timezone-aware datetime')
    if session_offset_seconds is not None and session_offset_seconds < 0:
        raise ValueError('session_offset_seconds must be >= 0')

    asset_id = str(uuid.uuid4())
    # UUID prefix prevents collisions; Snowflake PUT preserves the basename.
    relative_path = f'{uuid.UUID(session_id)}/{asset_id}/{local.name}'
    stage_dir = f'@CHRONOS_MEDIA_STAGE/{uuid.UUID(session_id)}/{asset_id}/'
    file_uri = 'file://' + local.as_posix()
    # PUT does not accept identifier/path bind parameters. Escape the quoted URI;
    # the stage destination is derived only from validated UUIDs.
    quoted_uri = "'" + file_uri.replace("'", "''") + "'"
    put_command = (f'PUT {quoted_uri} {stage_dir} '
                   'AUTO_COMPRESS=FALSE OVERWRITE=FALSE')

    with connect_snowflake() as sf:
        with sf.cursor() as cur:
            cur.execute(put_command)
            upload_results = cur.fetchall()
            # A reported failure should not create a misleading reference record.
            if upload_results and len(upload_results[0]) >= 7:
                status = str(upload_results[0][6]).upper()
                if status not in ('UPLOADED', 'SKIPPED'):
                    raise RuntimeError('Snowflake stage upload did not succeed')
            cur.execute('''
                INSERT INTO CHRONOS_MEDIA_ASSETS
                    (ASSET_ID, USER_ID, SESSION_ID, MEDIA_TYPE, ORIGIN,
                     STAGE_PATH, CONTENT_TYPE, FILE_SIZE_BYTES,
                     SESSION_OFFSET_SECONDS, IS_RAW_CAPTURE, CONSENT_RECORDED,
                     EXPIRES_AT)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,TRUE,%s)
            ''', (asset_id, user_id, session_id, media_type, origin,
                  relative_path, mimetypes.guess_type(local.name)[0],
                  local.stat().st_size, session_offset_seconds, raw_capture,
                  expires_at.isoformat() if expires_at else None))
    return asset_id
