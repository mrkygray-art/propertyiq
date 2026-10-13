-- Phase 0.5 (owner decisions 2026-10-12): four roles, fund-scoped screening deals and an access audit log.
--
-- Roles: viewer (read only), analyst (works deals), credit_officer (approves adjustments and clears flags,
-- edits lender policy), admin (everything, plus the Admin page). The old restricted_analyst role becomes an
-- analyst whose grants cover one fund: what you can see is set by user_portfolio_access, not by the role.
-- The guest keeps full access, so it becomes a credit officer.
ALTER TABLE demo_users DROP CONSTRAINT IF EXISTS demo_users_role_check;
UPDATE demo_users SET role = 'analyst' WHERE role = 'restricted_analyst';
UPDATE demo_users SET role = 'credit_officer' WHERE username = 'guest';
ALTER TABLE demo_users ADD CONSTRAINT demo_users_role_check
    CHECK (role IN ('viewer', 'analyst', 'credit_officer', 'admin'));

-- Each screening deal belongs to one fund, and is visible only to users granted that fund.
-- No foreign key to portfolios on purpose: scripts/seed.py reloads portfolios with TRUNCATE ... CASCADE,
-- which would delete every deal. The API checks the fund against the user's grants instead.
-- The default only keeps an older API version working during the upgrade; the current API always sets it.
ALTER TABLE screening_deals ADD COLUMN fund_id text NOT NULL DEFAULT 'FUND-I'
    CHECK (fund_id ~ '^FUND-[A-Z0-9]{1,10}$');
UPDATE screening_deals SET fund_id = 'FUND-II' WHERE name ILIKE 'Juniper Court%';
CREATE INDEX screening_deals_fund_idx ON screening_deals (fund_id);

-- Who did what, when, and whether it was allowed. No passwords, tokens, document contents or free text
-- typed by the user. Kept 90 days (PIQ_AUDIT_RETENTION_DAYS). Written only by piq_audit_writer
-- (scripts/access_setup.py); read by the API's read-only login.
CREATE TABLE access_audit (
    event_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at   timestamptz NOT NULL DEFAULT now(),
    username     text NOT NULL,               -- '(unknown)' for a sign-in with a name that doesn't exist
    role         text,
    action       text NOT NULL CHECK (action ~ '^[a-z_]+\.[a-z_]+$'),
    outcome      text NOT NULL CHECK (outcome IN ('allowed', 'refused')),
    reason       text CHECK (reason IN ('role', 'fund', 'sign_in')),
    target_type  text,
    target_id    text,
    fund_id      text,
    CHECK ((outcome = 'refused') = (reason IS NOT NULL))
);

CREATE INDEX access_audit_created_idx ON access_audit (created_at DESC);
CREATE INDEX access_audit_user_idx ON access_audit (username, created_at DESC);
