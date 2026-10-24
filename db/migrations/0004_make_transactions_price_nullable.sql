-- 0004_make_transactions_price_nullable.sql
-- Allows transactions.price to be NULL. DVF+ returns a null valeurfonc for a small share
-- of ordinary sales (8 of about 7,000 rows in two communes checked). Those mutations are
-- kept, not dropped. Nothing else depends on the NOT NULL constraint.

ALTER TABLE transactions
    ALTER COLUMN price DROP NOT NULL;