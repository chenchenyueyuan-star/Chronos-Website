-- Chronos TigerData v3: additive migration, not a reset.
-- Retains user_profiles, sessions, intro_questionnaire_history and chronos_auth_v2.
-- Does not delete, rewrite or truncate existing user records.
BEGIN;

CREATE TABLE IF NOT EXISTS user_profiles (
    user_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    intro_answers JSONB NOT NULL DEFAULT '{}'::jsonb,
    settings JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS display_name TEXT;
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS contact_email TEXT;
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

CREATE TABLE IF NOT EXISTS intro_questionnaire_history (
    response_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES user_profiles(user_id),
    answers JSONB NOT NULL,
    completed_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES user_profiles(user_id),
    pre_answers JSONB,
    post_answers JSONB,
    started_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS session_settings JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS script_text TEXT; -- legacy compatibility
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS session_title TEXT; -- legacy compatibility
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS duration_minutes INTEGER;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS voice_id TEXT;
-- All optional capture is off until the individual session has explicit consent.
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS biometrics_opt_in BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS media_storage_opt_in BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS raw_media_opt_in BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS media_retention_until TIMESTAMPTZ;

-- Signup questionnaire template: one row per question.
CREATE TABLE IF NOT EXISTS chronos_signup_questions (
    question_id TEXT PRIMARY KEY,
    question_order INTEGER NOT NULL CHECK (question_order > 0),
    category TEXT,
    field_key TEXT,
    question_text TEXT NOT NULL,
    choices JSONB NOT NULL,
    source_version TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_chronos_signup_question_order
    ON chronos_signup_questions(question_order);

-- Session templates: pre and post are distinct phases, but share one definition table.
CREATE TABLE IF NOT EXISTS chronos_session_questions (
    phase TEXT NOT NULL CHECK (phase IN ('pre', 'post')),
    question_id TEXT NOT NULL,
    question_order INTEGER NOT NULL CHECK (question_order > 0),
    field_key TEXT,
    question_text TEXT NOT NULL,
    choices JSONB NOT NULL,
    source_version TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (phase, question_id)
);
CREATE INDEX IF NOT EXISTS idx_chronos_session_question_order
    ON chronos_session_questions(phase, question_order);

-- A user can retake the full signup questionnaire many times.
CREATE TABLE IF NOT EXISTS chronos_signup_submissions (
    submission_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES user_profiles(user_id),
    questionnaire_version TEXT NOT NULL,
    submitted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_chronos_signup_submissions_user
    ON chronos_signup_submissions(user_id, submitted_at DESC);

CREATE TABLE IF NOT EXISTS chronos_signup_answers (
    submission_id UUID NOT NULL REFERENCES chronos_signup_submissions(submission_id) ON DELETE CASCADE,
    question_id TEXT NOT NULL REFERENCES chronos_signup_questions(question_id),
    answer_code TEXT NOT NULL CHECK (answer_code IN ('A','B')),
    answer_value JSONB,
    answered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (submission_id, question_id)
);

-- Answers belong to a particular relaxation session and a before/after phase.
CREATE TABLE IF NOT EXISTS chronos_session_answers (
    session_id UUID NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    phase TEXT NOT NULL,
    question_id TEXT NOT NULL,
    answer_code TEXT NOT NULL CHECK (answer_code IN ('A','B')),
    answer_value JSONB,
    answered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (session_id, phase, question_id),
    FOREIGN KEY (phase, question_id) REFERENCES chronos_session_questions(phase, question_id)
);
CREATE INDEX IF NOT EXISTS idx_chronos_session_answers_phase
    ON chronos_session_answers(phase, session_id);

-- Multiple regenerations/edits can be kept as separate script versions.
CREATE TABLE IF NOT EXISTS chronos_generated_scripts (
    script_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES user_profiles(user_id),
    session_id UUID NOT NULL REFERENCES sessions(session_id),
    script_title TEXT,
    script_text TEXT NOT NULL CHECK (length(trim(script_text)) > 0),
    source_kind TEXT NOT NULL DEFAULT 'generated' CHECK (source_kind IN ('generated','user_supplied','edited')),
    generator_provider TEXT,
    generator_model TEXT,
    selected_voice_id TEXT,
    personalization_context JSONB NOT NULL DEFAULT '{}'::jsonb,
    is_selected BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_chronos_scripts_session
    ON chronos_generated_scripts(session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_chronos_scripts_user
    ON chronos_generated_scripts(user_id, created_at DESC);

COMMIT;
