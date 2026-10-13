"""Set a sign-in password: prompts for it (hidden), saves it to .env, and updates the database.
Nothing is echoed.

  .\\.venv\\Scripts\\python.exe scripts\\set_demo_password.py               # analyst + restricted (PIQ_DEMO_PASSWORD)
  .\\.venv\\Scripts\\python.exe scripts\\set_demo_password.py --user guest  # guest only (PIQ_GUEST_PASSWORD)
  .\\.venv\\Scripts\\python.exe scripts\\set_demo_password.py --user admin  # test admin (PIQ_ADMIN_PASSWORD)
  .\\.venv\\Scripts\\python.exe scripts\\set_demo_password.py --user viewer # read-only viewer (PIQ_VIEWER_PASSWORD)
  .\\.venv\\Scripts\\python.exe scripts\\set_demo_password.py --user credit # credit1 to credit4 (PIQ_CREDIT_PASSWORD)

Every account that shares a setting gets the new password together, so they never drift apart.
"""
import argparse
import getpass
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
sys.path.insert(0, str(ROOT / "services" / "api"))
from propertyiq.auth import hash_password  # noqa: E402
from propertyiq.config import get_settings  # noqa: E402
from propertyiq.seed.generator import DEMO_USERS, PASSWORD_GROUPS, USER_PASSWORD_ENV  # noqa: E402


def main() -> None:
    shared = set(PASSWORD_GROUPS.values())
    choices = sorted({u for u, k in USER_PASSWORD_ENV.items() if k not in shared} | set(PASSWORD_GROUPS))
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", choices=choices, help="whose password to set (credit = all four credit officers)")
    args = parser.parse_args()
    env_key = PASSWORD_GROUPS.get(args.user) or USER_PASSWORD_ENV.get(args.user, "PIQ_DEMO_PASSWORD")
    users = [u for u in DEMO_USERS if USER_PASSWORD_ENV.get(u[0], "PIQ_DEMO_PASSWORD") == env_key]

    who = " and ".join(u[0] for u in users)
    pw = getpass.getpass(f"New password for {who} (hidden): ")
    if len(pw) < 6 or any(c in pw for c in " #\"'"):
        sys.exit("Use at least 6 characters, with no spaces, # or quotes.")
    if getpass.getpass("Type it again: ") != pw:
        sys.exit("The two entries didn't match. Nothing was changed.")

    lines = ENV.read_text(encoding="utf-8-sig").splitlines()
    lines = [l for l in lines if not l.startswith(f"{env_key}=")] + [f"{env_key}={pw}"]
    ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with psycopg.connect(get_settings().dsn("piq_owner")) as conn:
        for username, display, role, portfolios in users:
            conn.execute(
                "INSERT INTO demo_users (username, display_name, role, password_hash) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (username) DO UPDATE SET password_hash = EXCLUDED.password_hash, role = EXCLUDED.role",
                (username, display, role, hash_password(pw)))
            for p in portfolios:
                conn.execute("INSERT INTO user_portfolio_access (username, portfolio_id) VALUES (%s, %s) "
                             "ON CONFLICT DO NOTHING", (username, p))
    print(f"Done. Sign in as {who} with the new password.")


if __name__ == "__main__":
    main()
