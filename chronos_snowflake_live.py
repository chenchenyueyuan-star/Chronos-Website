"""Snowflake integration for the ACTUAL Snowflake CHRONOS_ANALYTICS.WELLNESS tables.

Uses the encrypted RSA key in ~/.chronos_keys/rsa_key.p8 and the Snowflake
CHRONOS_SERVICE / CHRONOS_APP service role. No key/passphrase is saved here.

Only an explicit Streamlit user action calls save_generated_audio().
No camera, microphone, or biometric recording starts automatically.
"""
from __future__ import annotations

import mimetypes
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(PROJECT_DIR / ".env")

STAGE = "@CHRONOS_ANALYTICS.WELLNESS.CHRONOS_MEDIA_STAGE"
DATABASE = "CHRONOS_ANALYTICS"
SCHEMA = "WELLNESS"


def _uuid(text: str) -> str:
    return str(uuid.UUID(str(text)))


def _tigerdata():
    """Access the existing TigerData instance, not a new database."""
    import psycopg

    url = os.getenv("TIMESCALE_SERVICE_URL")
    if not url:
        raise RuntimeError("TigerData is not configured in .env")
    return psycopg.connect(url, connect_timeout=10)


def connect_snowflake(passphrase: str):
    """Connect with the key pair that has already passed the user's test.

    Never accepts an account password or embeds an RSA private key.
    """
    if not passphrase:
        raise ValueError("Enter the RSA key passphrase locally to connect")
    key = Path.home() / ".chronos_keys" / "rsa_key.p8"
    if not key.is_file():
        raise FileNotFoundError("RSA private key is missing from ~/.chronos_keys")
    import snowflake.connector

    return snowflake.connector.connect(
        account=os.getenv("SNOWFLAKE_ACCOUNT", "XGVDJMC-ON91639"),
        user=os.getenv("SNOWFLAKE_USER", "CHRONOS_SERVICE"),
        role="CHRONOS_APP",
        warehouse=os.getenv("SNOWFLAKE_WAREHOUSE", "COMPUTE_WH"),
        database=DATABASE,
        schema=SCHEMA,
        authenticator="SNOWFLAKE_JWT",
        private_key_file=str(key),
        private_key_password=passphrase,
        login_timeout=30,
        network_timeout=30,
        client_session_keep_alive=False,
    )


def _session_owner(user_id: str, session_id: str, *, permission: str | None = None) -> None:
    uid, sid = _uuid(user_id), _uuid(session_id)
    with _tigerdata() as pg:
        row = pg.execute(
            """SELECT biometrics_opt_in, media_storage_opt_in, raw_media_opt_in,
                      media_retention_until
               FROM sessions WHERE user_id = %s AND session_id = %s""",
            (uid, sid),
        ).fetchone()
    if row is None:
        raise PermissionError("This session does not belong to the signed-in user")
    biometrics, media, raw, retention = row
    if permission == "media" and not media:
        raise PermissionError("Media storage consent has not been recorded for this session")
    if permission == "biometrics" and not biometrics:
        raise PermissionError("Biometric consent has not been recorded for this session")
    if retention is not None and retention <= datetime.now(timezone.utc):
        raise PermissionError("This session's data retention permission has expired")


def consent_to_generated_audio_storage(user_id: str, session_id: str) -> None:
    """Call only after a deliberate checkbox + button action on the session page.

    This permits storage of generated audio only. Raw recordings remain off.
    """
    uid, sid = _uuid(user_id), _uuid(session_id)
    with _tigerdata() as pg:
        updated = pg.execute(
            """UPDATE sessions
               SET media_storage_opt_in = TRUE
               WHERE user_id = %s AND session_id = %s
               RETURNING session_id""",
            (uid, sid),
        ).fetchone()
        if not updated:
            raise PermissionError("No matching TigerData session was found")


def _validated_generated_audio_path(file_path: str, generated_dir: Path) -> Path:
    """Do not allow Streamlit to upload arbitrary files from the user's computer."""
    root = generated_dir.resolve(strict=True)
    source = Path(file_path).expanduser().resolve(strict=True)
    if not source.is_file() or not source.is_relative_to(root):
        raise ValueError("Only files in this project's generated_sessions folder may be uploaded")
    if source.suffix.lower() not in (".mp3", ".wav", ".ogg"):
        raise ValueError("Only generated MP3, WAV or OGG tracks are supported")
    if source.stat().st_size > 50 * 1024 * 1024:
        raise ValueError("Track is over the 50 MB upload limit")
    return source


def _sql_file_uri(path: Path) -> str:
    """Snowflake PUT accepts a quoted file:// URI on Windows."""
    return "'file://" + path.as_posix().replace("'", "''") + "'"


def save_generated_audio(
    user_id: str,
    session_id: str,
    file_path: str,
    passphrase: str,
    *,
    generated_dir: Path,
) -> dict[str, Any]:
    """Upload a user-approved generated audio track and insert its metadata.

    No upload happens without recorded, session-specific TigerData consent.
    This function intentionally refuses raw camera/video/microphone capture.
    """
    uid, sid = _uuid(user_id), _uuid(session_id)
    _session_owner(uid, sid, permission="media")
    source = _validated_generated_audio_path(file_path, generated_dir)
    asset_uuid = str(uuid.uuid4())
    prefix = f"{sid}/{asset_uuid}/"  # UUID-only stage prefix, cannot escape the stage
    stage_prefix = f"{STAGE}/{prefix}"
    stage_path = prefix + source.name
    uploaded = False
    with connect_snowflake(passphrase) as snowflake:
        with snowflake.cursor() as cur:
            try:
                # PUT is executed from the Python driver, not from Snowsight SQL UI.
                results = cur.execute(
                    f"PUT {_sql_file_uri(source)} {stage_prefix} "
                    "AUTO_COMPRESS=FALSE OVERWRITE=FALSE"
                ).fetchall()
                if not results or not any(
                    len(row) >= 7 and str(row[6]).upper() == "UPLOADED"
                    for row in results
                ):
                    raise RuntimeError("Snowflake did not confirm the media upload")
                uploaded = True
                cur.execute(
                    """INSERT INTO CHRONOS_ANALYTICS.WELLNESS.CHRONOS_MEDIA_ASSETS
                        (USER_ID, SESSION_ID, MEDIA_TYPE, FILE_NAME, STAGE_PATH,
                         MIME_TYPE, FILE_SIZE_BYTES, CONSENT_TO_STORE)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        uid, sid, "audio", source.name, stage_path,
                        mimetypes.guess_type(source.name)[0] or "audio/mpeg",
                        source.stat().st_size, True,
                    ),
                )
            except Exception:
                # If inserting metadata fails, try not to leave an orphan upload.
                if uploaded:
                    try:
                        cur.execute(f"REMOVE {stage_prefix}")
                    except Exception:
                        pass
                raise
    return {"file": source.name, "stage_path": stage_path, "size_bytes": source.stat().st_size}


def record_biometric_sample(
    user_id: str,
    session_id: str,
    passphrase: str,
    *,
    elapsed_seconds: float,
    heart_rate_bpm: float | None = None,
    blinks_per_second: float | None = None,
    emotion_label: str | None = None,
    emotion_confidence: float | None = None,
    anxiety_score: float | None = None,
    anxiety_score_source: str | None = None,
    data_source: str,
) -> None:
    """Storage hook for real future measurements; does NOT measure biometrics.

    Consent must already be in TigerData. Never treat emotion/anxiety
    predictions as clinical assessments.
    """
    uid, sid = _uuid(user_id), _uuid(session_id)
    _session_owner(uid, sid, permission="biometrics")
    if elapsed_seconds < 0:
        raise ValueError("elapsed_seconds must be non-negative")
    if blinks_per_second is not None and blinks_per_second < 0:
        raise ValueError("blinks_per_second must be non-negative")
    if emotion_confidence is not None and not 0 <= emotion_confidence <= 1:
        raise ValueError("emotion_confidence must be between 0 and 1")
    if not data_source:
        raise ValueError("data_source must identify the real measurement provider")
    with connect_snowflake(passphrase) as sf:
        with sf.cursor() as cur:
            cur.execute(
                """INSERT INTO CHRONOS_ANALYTICS.WELLNESS.CHRONOS_BIOMETRIC_SAMPLES
                    (USER_ID, SESSION_ID, ELAPSED_SECONDS, HEART_RATE_BPM,
                     BLINKS_PER_SECOND, EMOTION_LABEL, EMOTION_CONFIDENCE,
                     ANXIETY_SCORE, ANXIETY_SCORE_SOURCE, DATA_SOURCE)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (uid, sid, elapsed_seconds, heart_rate_bpm, blinks_per_second,
                 emotion_label, emotion_confidence, anxiety_score,
                 anxiety_score_source, data_source),
            )


def save_graph_series(
    user_id: str, session_id: str, passphrase: str,
    *, graph_type: str, title: str,
    series: list[dict[str, Any]],
) -> None:
    """Save actual measurement series to Snowflake VARIANT; no fake graph points."""
    import json

    uid, sid = _uuid(user_id), _uuid(session_id)
    _session_owner(uid, sid, permission="biometrics")
    if not isinstance(series, list) or not series:
        raise ValueError("Graph requires actual time-series points")
    if len(json.dumps(series)) > 5_000_000:
        raise ValueError("Graph data exceeds the size limit")
    if graph_type not in ("heart_rate", "blinking", "emotion", "anxiety", "other"):
        raise ValueError("Unrecognised graph type")
    with connect_snowflake(passphrase) as sf:
        with sf.cursor() as cur:
            cur.execute(
                """INSERT INTO CHRONOS_ANALYTICS.WELLNESS.CHRONOS_SESSION_GRAPHS
                    (USER_ID, SESSION_ID, GRAPH_TYPE, GRAPH_TITLE, SERIES_DATA)
                    SELECT %s,%s,%s,%s,PARSE_JSON(%s)""",
                (uid, sid, graph_type, title[:255], json.dumps(series)),
            )


def save_session_metrics(
    user_id: str, session_id: str, passphrase: str,
    *, actual_duration_seconds: float,
    average_heart_rate_bpm: float | None = None,
    average_blinks_per_second: float | None = None,
    measurement_count: int = 0,
    self_reported_anxiety_before: float | None = None,
    self_reported_anxiety_after: float | None = None,
) -> None:
    """Store real completed-session measurements; not planned session duration."""
    uid, sid = _uuid(user_id), _uuid(session_id)
    _session_owner(uid, sid, permission="biometrics")
    if actual_duration_seconds < 0 or measurement_count < 0:
        raise ValueError("Actual duration and count cannot be negative")
    with connect_snowflake(passphrase) as sf:
        with sf.cursor() as cur:
            # Snowflake primary keys are informational in standard tables. MERGE
            # avoids duplicating a session summary on repeat writes.
            cur.execute(
                """MERGE INTO CHRONOS_ANALYTICS.WELLNESS.CHRONOS_SESSION_METRICS t
                   USING (SELECT %s AS SESSION_ID) s ON t.SESSION_ID = s.SESSION_ID
                   WHEN MATCHED THEN UPDATE SET
                     DURATION_SECONDS = %s,
                     AVERAGE_HEART_RATE_BPM = %s,
                     AVERAGE_BLINKS_PER_SECOND = %s,
                     MEASUREMENT_COUNT = %s,
                     SELF_REPORTED_ANXIETY_BEFORE = %s,
                     SELF_REPORTED_ANXIETY_AFTER = %s
                   WHEN NOT MATCHED THEN INSERT (
                     SESSION_ID, USER_ID, DURATION_SECONDS,
                     AVERAGE_HEART_RATE_BPM, AVERAGE_BLINKS_PER_SECOND,
                     MEASUREMENT_COUNT, SELF_REPORTED_ANXIETY_BEFORE,
                     SELF_REPORTED_ANXIETY_AFTER
                   ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (sid, actual_duration_seconds, average_heart_rate_bpm,
                 average_blinks_per_second, measurement_count,
                 self_reported_anxiety_before, self_reported_anxiety_after,
                 sid, uid, actual_duration_seconds, average_heart_rate_bpm,
                 average_blinks_per_second, measurement_count,
                 self_reported_anxiety_before, self_reported_anxiety_after),
            )


def read_media_metadata(user_id: str, session_id: str, passphrase: str) -> list[dict[str, Any]]:
    """Read only this signed-in user's media metadata, never stage file contents."""
    uid, sid = _uuid(user_id), _uuid(session_id)
    _session_owner(uid, sid)
    with connect_snowflake(passphrase) as sf:
        with sf.cursor() as cur:
            rows = cur.execute(
                """SELECT MEDIA_TYPE, FILE_NAME, STAGE_PATH, FILE_SIZE_BYTES, CREATED_AT
                   FROM CHRONOS_ANALYTICS.WELLNESS.CHRONOS_MEDIA_ASSETS
                   WHERE USER_ID = %s AND SESSION_ID = %s ORDER BY CREATED_AT DESC""",
                (uid, sid),
            ).fetchall()
    return [dict(zip(("media_type", "file_name", "stage_path", "size_bytes", "created_at"), row)) for row in rows]
