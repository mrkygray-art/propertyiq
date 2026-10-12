"""Demo sign-in: scrypt password hashes and HMAC-signed bearer tokens.

Good enough to demonstrate backend authorization on synthetic data. Not production
identity management (no MFA, no revocation list, no SSO).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

TOKEN_TTL_SECONDS = 8 * 3600
_SCRYPT = {"n": 2**14, "r": 8, "p": 1}


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **_SCRYPT)
    return f"scrypt${_SCRYPT['n']}${_SCRYPT['r']}${_SCRYPT['p']}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        actual = hashlib.scrypt(password.encode(), salt=_unb64(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(actual, _unb64(digest))
    except (ValueError, TypeError):
        return False


def issue_token(username: str, secret: str, now: float | None = None) -> str:
    body = _b64(json.dumps({"sub": username, "exp": int((now or time.time()) + TOKEN_TTL_SECONDS)}).encode())
    sig = _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def read_token(token: str, secret: str, now: float | None = None) -> str | None:
    """Return the username if the token is authentic and unexpired, else None."""
    try:
        body, sig = token.split(".")
        expected = _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        claims = json.loads(_unb64(body))
        if claims["exp"] < (now or time.time()):
            return None
        return str(claims["sub"])
    except (ValueError, KeyError, TypeError):
        return None
