"""Back up the PropertyIQ database and uploaded documents, then prove the backup restores.

  .\\.venv\\Scripts\\python.exe scripts\\backup.py            # back up + restore test
  .\\.venv\\Scripts\\python.exe scripts\\backup.py --keep 30  # keep the newest 30 backups (default 14)

1. pg_dump runs inside the Postgres container (local connection; no password on any command line).
2. data/uploads/ is zipped next to the dump.
3. Restore test: the dump is loaded into a scratch database, every table's row count is compared with the
   live database, plus the seed hash; then the scratch database is dropped. The result is written to
   last-restore-test.json, which the Admin page's security checklist reads.

Backups go to PIQ_BACKUP_DIR (default: %USERPROFILE%\\PropertyIQ-backups), outside the repo. They hold synthetic
data only. Database roles and passwords aren't in the dump; scripts/db_bootstrap.py recreates them from .env.
Copy the folder to an external drive for an off-machine copy.
"""
import argparse
import json
import os
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "services" / "api"))
from propertyiq.config import get_settings  # noqa: E402

CONTAINER = "propertyiq-postgres"
SCRATCH = "piq_restore_test"
DOCKER = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "DockerDesktop" / "resources" / "bin" / "docker.exe"
BACKUP_DIR = Path(os.environ.get("PIQ_BACKUP_DIR", Path.home() / "PropertyIQ-backups"))
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

COUNTS_SQL = """
SELECT string_agg(format('%s=%s', t, (xpath('/row/c/text()',
       query_to_xml(format('SELECT count(*) AS c FROM piq.%I', t), false, true, '')))[1]::text), ',' ORDER BY t)
FROM (SELECT tablename AS t FROM pg_tables WHERE schemaname = 'piq') x
"""
SEED_SQL = "SELECT coalesce((SELECT content_sha256 FROM piq.seed_runs ORDER BY seed_run_id DESC LIMIT 1), 'none')"


def docker(*args: str, stdout=subprocess.PIPE, check: bool = True) -> subprocess.CompletedProcess:
    exe = str(DOCKER) if DOCKER.exists() else "docker"
    return subprocess.run([exe, *args], stdout=stdout, stderr=subprocess.PIPE, check=check, creationflags=NO_WINDOW)


def psql(db: str, sql: str) -> str:
    return docker("exec", CONTAINER, "psql", "-U", "postgres", "-d", db, "-tAc", sql).stdout.decode().strip()


def counts(db: str) -> dict[str, int]:
    raw = psql(db, COUNTS_SQL)
    return {k: int(v) for k, v in (p.split("=") for p in raw.split(","))} if raw else {}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", type=int, default=14)
    args = ap.parse_args()
    db = get_settings().db_name
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dump = BACKUP_DIR / f"propertyiq-{stamp}.dump"

    # 1. Dump (custom format, compressed). Written by the host from the container's stdout.
    with dump.open("wb") as f:
        docker("exec", CONTAINER, "pg_dump", "-U", "postgres", "-d", db, "-Fc", "--no-acl", stdout=f)
    print(f"database dump: {dump} ({dump.stat().st_size / 1e6:.1f} MB)")

    # 2. Uploaded documents.
    uploads = ROOT / "data" / "uploads"
    if uploads.exists():
        z = BACKUP_DIR / f"propertyiq-{stamp}-uploads.zip"
        with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in uploads.rglob("*"):
                if p.is_file():
                    zf.write(p, p.relative_to(uploads))
        print(f"uploads: {z} ({len(zf.namelist())} files)")

    # 3. Restore test into a scratch database.
    live = counts(db)
    live_seed = psql(db, SEED_SQL)
    psql("postgres", f"DROP DATABASE IF EXISTS {SCRATCH}")
    psql("postgres", f"CREATE DATABASE {SCRATCH}")
    try:
        with dump.open("rb") as f:
            r = subprocess.run([str(DOCKER), "exec", "-i", CONTAINER, "pg_restore", "-U", "postgres", "-d", SCRATCH,
                                "--no-owner", "--no-acl"], stdin=f, capture_output=True, creationflags=NO_WINDOW)
        restored = counts(SCRATCH)
        restored_seed = psql(SCRATCH, SEED_SQL)
    finally:
        psql("postgres", f"DROP DATABASE IF EXISTS {SCRATCH}")

    diffs = {t: (live.get(t), restored.get(t)) for t in set(live) | set(restored) if live.get(t) != restored.get(t)}
    ok = r.returncode == 0 and not diffs and live_seed == restored_seed and len(live) > 0
    result = {"ok": ok, "at": datetime.now().isoformat(timespec="seconds"), "dump": dump.name,
              "tables": len(live), "rows": sum(live.values()), "differences": diffs,
              "seed_sha256_match": live_seed == restored_seed, "pg_restore_exit": r.returncode,
              "pg_restore_errors": r.stderr.decode(errors="ignore")[-2000:] if r.returncode else ""}
    (BACKUP_DIR / "last-restore-test.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"restore test: {'PASSED' if ok else 'FAILED'} - {result['tables']} tables, {result['rows']} rows"
          + ("" if ok else f"; differences: {diffs}; pg_restore exit {r.returncode}"))

    # 4. Keep the newest N backups.
    for old in sorted(BACKUP_DIR.glob("propertyiq-*.dump"))[:-args.keep]:
        old.unlink()
        old.with_name(old.stem + "-uploads.zip").unlink(missing_ok=True)
        print(f"removed old backup {old.name}")
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
