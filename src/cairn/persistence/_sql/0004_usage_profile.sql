-- 0004_usage_profile.sql
-- Add a `profile` column to `model_usage` so the UI can render
-- per-profile cost windows. Existing rows stay NULL — that means
-- "before profile tracking", honestly reported in the all-profiles
-- total but excluded from current-profile filters. New rows always
-- carry the active profile (orchestrator stamps it at record time).

ALTER TABLE model_usage ADD COLUMN profile TEXT;
CREATE INDEX idx_usage_profile_timestamp ON model_usage(profile, timestamp);
