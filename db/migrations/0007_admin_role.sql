-- Adds an 'admin' role for the test Admin account (security checklist and network lockdown).
-- The admin user itself is created by scripts/set_demo_password.py --user admin, which sets its password.
ALTER TABLE demo_users DROP CONSTRAINT IF EXISTS demo_users_role_check;
ALTER TABLE demo_users ADD CONSTRAINT demo_users_role_check
    CHECK (role IN ('analyst', 'restricted_analyst', 'admin'));
