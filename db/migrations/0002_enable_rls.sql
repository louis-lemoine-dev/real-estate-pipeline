-- 0002_enable_rls.sql
-- Enable Row Level Security on all tables. No policies are added here:
-- with RLS on and zero policies, the anon/authenticated roles (used only
-- if the Data API is ever re-enabled) are fully locked out by default,
-- rather than left wide open. Our own connection uses the table-owning
-- role, which bypasses RLS regardless, so this has no effect on the
-- pipeline's own scripts.

ALTER TABLE public.listings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.transactions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.price_change_events ENABLE ROW LEVEL SECURITY;