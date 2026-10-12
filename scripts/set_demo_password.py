"""Set the demo sign-in password: prompts for it (hidden), writes PIQ_DEMO_PASSWORD to .env,
and updates both demo users in the database. Nothing is echoed.

Usage: .\\.venv\\Scripts\\python.exe scripts\\set_demo_password.py
"""
import getpass
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
sys.path.insert(0, str(ROOT / "services" / "api"))
from propertyiq.auth import hash_password  # noqa: E402
from propertyiq.config import get_settings  # noqa: E402


def main() -> None:
    pw = getpass.getpass("New demo password (hidden): ")
    if len(pw) < 6 or any(c in pw for c in " #\"'"):
        sys.exit("Use at least 6 characters, with no spaces, # or quotes.")
    if getpass.getpass("Type it again: ") != pw:
        sys.exit("The two entries didn't match. Nothing was changed.")

    lines = ENV.read_text(encoding="utf-8-sig").splitlines()
    lines = [l for l in lines if not l.startswith("PIQ_DEMO_PASSWORD=")] + [f"PIQ_DEMO_PASSWORD={pw}"]
    ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with psycopg.connect(get_settings().dsn("piq_owner")) as conn:
        for (username,) in conn.execute("SELECT username FROM demo_users").fetchall():
            conn.execute("UPDATE demo_users SET password_hash = %s WHERE username = %s", (hash_password(pw), username))
    print("Done. Sign in as analyst or restricted with the new password.")


if __name__ == "__main__":
    main()
