"""Network lockdown: reads firewall state and asks Windows to run scripts/lockdown.ps1 as administrator.

The API itself never has admin rights. A change request opens the Windows "Do you want to allow this app
to make changes?" prompt on this PC's screen, and nothing happens until someone there clicks Yes.
"""
from __future__ import annotations

import json
import socket
import subprocess
from datetime import datetime
from pathlib import Path

from propertyiq.config import REPO_ROOT

SCRIPT = REPO_ROOT / "scripts" / "lockdown.ps1"
STATE_FILE = Path(r"C:\ProgramData\PropertyIQ\lockdown-state.json")
LOG_DIR = REPO_ROOT / "logs"
MODES = {"app": "App", "computer": "Computer", "off": "Off"}
PROBE = ("1.1.1.1", 443)   # a public address; used only to see whether a connection is possible
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def quick_mode() -> dict:
    """Cheap status for the header banner: read the state file the script writes while a lockdown is on."""
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8-sig"))
        return {"mode": str(state.get("mode", "app")).lower(), "since": state.get("applied_at")}
    except (OSError, ValueError):
        return {"mode": "off", "since": None}


def firewall_status(timeout: float = 30) -> dict:
    """Authoritative status from the firewall itself (no admin needed)."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT), "-Mode", "Status"],
            capture_output=True, text=True, timeout=timeout, creationflags=NO_WINDOW,
        )
        return json.loads(out.stdout.strip().splitlines()[-1])
    except (OSError, subprocess.TimeoutExpired, ValueError, IndexError) as exc:
        return {"mode": "unknown", "error": type(exc).__name__}


def can_reach_internet(timeout: float = 2.5) -> bool:
    """True if this API process can open a TCP connection to a public address."""
    try:
        with socket.create_connection(PROBE, timeout=timeout):
            return True
    except OSError:
        return False


def log_action(username: str, action: str) -> None:
    LOG_DIR.mkdir(exist_ok=True)
    with (LOG_DIR / "admin-actions.log").open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {username}  {action}\n")


def request_change(mode: str, username: str) -> None:
    """Start the elevated script. Returns at once; Windows shows the approval prompt on this PC."""
    ps_mode = MODES[mode]   # KeyError for anything not in the fixed list; never built from user text
    log_action(username, f"requested lockdown mode {ps_mode}")
    args = f"-NoProfile -ExecutionPolicy Bypass -File \"{SCRIPT}\" -Mode {ps_mode}"
    subprocess.Popen(
        ["powershell", "-NoProfile", "-Command",
         f"Start-Process powershell -Verb RunAs -WindowStyle Normal -ArgumentList '{args}'"],
        creationflags=NO_WINDOW,
    )
