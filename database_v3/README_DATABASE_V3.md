# Chronos — database redesign v3

This is a **non-destructive database schema update** for the Chronos OxHack project. It is **not** a replacement `app.py`. The running Streamlit UI stays unchanged until the app is explicitly wired into the new storage helper methods.

## The data model

| System | Table | Contents |
|---|---|---|
| TigerData | `chronos_signup_questions` | The original 20 signup A/B questions and choices, one question per row |
| TigerData | `chronos_session_questions` | The 8 original pre-session A/B questions; later approved post-session questions belong here too |
| TigerData | `user_profiles` | User ID, current signup preference summary (JSONB), settings, optional display name/email |
| TigerData | `chronos_auth_v2` | Existing username + password hash (UNCHANGED by this migration) |
| TigerData | `chronos_signup_submissions` | One record per completed signup or retake |
| TigerData | `chronos_signup_answers` | One answer per question within a signup submission |
| TigerData | `sessions` | User's relaxation sessions and session settings; keeps legacy JSONB answers for compatibility |
| TigerData | `chronos_session_answers` | One question/answer per session and phase (`pre` or `post`) |
| TigerData | `chronos_generated_scripts` | Multiple saved or edited narration scripts per user/session, with optional provider/model/voice ID |
| Snowflake | `CHRONOS_BIOMETRIC_SAMPLES` | Timestamped optional heart rate, blinks per second, estimated emotion/confidence, *labelled* self-reported versus model-estimated anxiety |
| Snowflake | `CHRONOS_SESSION_METRICS` | Session duration, averages and summary statistics |
| Snowflake | `CHRONOS_SESSION_GRAPHS` | JSON graph points (VARIANT), which Streamlit can plot; optional graph image ID |
| Snowflake | `CHRONOS_MEDIA_ASSETS` | Records of staged images, videos, narration audio and exported graph images (references, not raw blobs) |
| Snowflake | `@CHRONOS_MEDIA_STAGE` | Protected storage for real image/audio/video files, when opted in |

**Key relationships:** `user_profiles.user_id` -> submissions, sessions, and scripts. `sessions.session_id` -> per-session answers and scripts. Those same UUID strings identify rows in Snowflake; foreign keys do **not** work automatically across TigerData and Snowflake, so the application must verify ownership.

## Exactly what your original questionnaire files contain

- `intro questionnaire.json`: **20** A/B questions in ASMR (6), narration (8), nature (6).
- `session questionnaire.json`: **8 actual A/B items** in its `items` list, although the metadata says **9**. The seed script uses the actual 8 rather than inventing a ninth.
- The provided files have no final after-session questionnaire; its `post` rows must be defined and approved later. The SQL supports this phase already.
- Broad signup preferences do not override explicit choices for each session.

## Setup — TigerData, one stage at a time

1. Keep your current `.env`, `.venv`, `chronos_store.py`, `db_setup.py`, questionnaires, and `app.py` in place. Make a backup or database snapshot before changing a production instance. **Never publish your `.env` or keys.**
2. **Recommended:** extract the ZIP into `OxHack-2026\database_v3\` (a new subfolder). The helpers look for `.env` in the parent project folder, so there is no need to copy your private credentials. No existing `app.py`, `.env` or questionnaires are overwritten.
3. **Review** `01_tigerdata_schema.sql`. It only creates missing tables and adds columns; it does not `DROP` or `TRUNCATE`.
4. Run in the VS Code PowerShell terminal, from your `OxHack-2026` project folder:

   ```powershell
   .\.venv\Scripts\python.exe .\database_v3\apply_tigerdata_schema.py
   ```

5. Import the source-of-truth question definitions:

   ```powershell
   .\.venv\Scripts\python.exe .\database_v3\seed_chronos_questionnaires.py
   ```

6. To connect the **existing** Streamlit app later, import `database_v3.chronos_tigerdata_v3` and use `signup_questions`, `session_questions`, `save_signup_submission`, `save_session_answers`, and `save_generated_script`. These do **not** replace the current auth system. Your current UI may still use legacy `chronos_store` functions until specifically migrated.

### Example API usage (only after the UI is integrated)

```python
from database_v3.chronos_tigerdata_v3 import (
    save_signup_submission, save_session_answers, save_generated_script
)

# Answers keyed by the original JSON question IDs, using literal A/B codes.
submission_id = save_signup_submission(user_id, {'p1': 'A'})  # demo partial only
save_session_answers(user_id, session_id, 'pre', {'s1': 'B'})
script_id = save_generated_script(user_id, session_id, 'A calm forest opening...',
                                  title='Forest break', provider='gemini',
                                  model='configured-model')
```

For **real signup** pass the entire 20-answer dictionary. The example illustrates the structure only.

## Setup — Snowflake (later, once your account is connected)

1. Create/configure a Snowflake account, database role/warehouse permissions, and **private** credentials. See `snowflake_env.example` for variable *names* only; add real values to your existing git-ignored `.env` locally, never to chat or GitHub.
2. Run `02_snowflake_schema.sql` once using the Snowflake Snowsight worksheet with a role that can create the database/schema/stage. This makes four Snowflake tables and a protected stage.
3. Install `snowflake-connector-python` in your venv when you are ready to test the live account.
4. Your application can then import `database_v3.chronos_snowflake_v3.add_biometric_sample`, `save_graph_series`, `save_session_summary`, and `stage_media`. The helper checks **per-session consent in TigerData** before every upload/write. No capturing is implemented here: the optional Presage/camera component must provide each measurement.

### Example metrics row

`session_id=...`, `session_offset_seconds=12.0`, `heart_rate_bpm=72.0`, `blinks_per_second=0.3`, `emotion_label="neutral"`, `emotion_confidence=0.64`, `measurement_source="camera_estimate"`, `model_version="..."`.

Graph `SERIES` example: `[{"seconds": 0, "value": 70}, {"seconds": 5, "value": 72}]`.

For audio, frames and video, the table stores stage paths like `<session_uuid>/<asset_uuid>/audio.mp3`. Actual files reside inside `@CHRONOS_MEDIA_STAGE` with access controls. Snowflake supports this model for unstructured data: https://docs.snowflake.com/en/sql-reference/data-types-unstructured . Local files can be uploaded with PUT from a Snowflake connector: https://docs.snowflake.com/en/sql-reference/sql/put .

## Sensitive data rules (important)

- **Biometric collection off by default.** Opt in separately for each session with `sessions.biometrics_opt_in = TRUE`.
- **Media storage off by default.** Require `media_storage_opt_in = TRUE`; storing raw camera/microphone captures also requires `raw_media_opt_in = TRUE` plus an explicit expiry.
- Do not upload every camera frame. Use lightweight, sparse time-series samples where possible; otherwise storage/privacy costs and lag will be significant.
- Do not infer a *medical anxiety level* from facial appearance alone. Keep self-reported scores separate from experimental estimates; preserve algorithm/source/confidence details.
- `EXPIRES_AT` is metadata **only**. You must implement a real deletion/retention job that removes expired stage files **and** metadata. This package does not automatically delete media.
- Store only what is needed, restrict Snowflake permissions, and never use a Snowflake password or database URL in public source code.
- Snowflake is for analytics/media, not the source of truth for user login or passwords.

## What this package actually does / does not do

✅ Defines a clean additive schema for your requested data, imports real question definitions, offers Python helper calls, and retains legacy TigerData fields for existing UI compatibility.

❌ Does **not** automatically change your current running Streamlit app, create a Snowflake account, capture webcam video, run Presage, or prove live Snowflake/TigerData compatibility without testing on your account.

Do not run the SQL against a shared database without checking permissions and migration impact with your team.
