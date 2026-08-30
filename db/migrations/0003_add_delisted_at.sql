-- 0003_add_delisted_at.sql
-- Adds delisted_at to track listings no longer appearing in ParuVendu scrapes.
-- Set once when a listing is confirmed delisted (2 consecutive missed scrapes);
-- never cleared automatically. Null = currently active or not yet delisted.

ALTER TABLE listings
    ADD COLUMN delisted_at timestamptz NULL;