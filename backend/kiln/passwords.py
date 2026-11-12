"""Password hashes for local accounts.

Only the hash is stored. Callers must not log or return the plaintext.
"""

from pwdlib import PasswordHash

_HASHER = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return _HASHER.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _HASHER.verify(password, password_hash)
    except Exception:
        return False
