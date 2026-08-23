"""Scrypt password hashing shared by hub user store and client login."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    salt_bytes = salt if salt is not None else secrets.token_bytes(16)
    dig = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt_bytes,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
    )
    return "$".join(
        [
            "scrypt",
            str(_SCRYPT_N),
            str(_SCRYPT_R),
            str(_SCRYPT_P),
            base64.urlsafe_b64encode(salt_bytes).decode("ascii").rstrip("="),
            base64.urlsafe_b64encode(dig).decode("ascii").rstrip("="),
        ]
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        kind, n_s, r_s, p_s, salt_b64, hash_b64 = encoded.split("$", 5)
        if kind != "scrypt":
            return False
        n, r, p = int(n_s), int(r_s), int(p_s)
        salt = base64.urlsafe_b64decode(salt_b64 + "==")
        expected = base64.urlsafe_b64decode(hash_b64 + "==")
        got = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            dklen=len(expected),
        )
        return hmac.compare_digest(got, expected)
    except (ValueError, TypeError, OSError):
        return False
