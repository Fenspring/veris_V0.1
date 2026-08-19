"""Credential storage.

Credentials go to the operating system's own secret store — Keychain on macOS,
Credential Manager/DPAPI on Windows, Secret Service on Linux — and nowhere else.

There is deliberately **no encrypted-file fallback**. A file Veris can decrypt
unattended is a file an attacker with the host can decrypt, and shipping one
would let a hospital believe its LMS credentials are protected by something
stronger than filesystem permissions. When no OS store is available the honest
answers are an operator-supplied environment variable or an explicit refusal —
not a reassuring-sounding file.

Nothing here is ever logged. Callers receive values; the module never emits them.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

SERVICE = "veris"
ENV_PREFIX = "VERIS_CRED_"
_SAFE_KEY = re.compile(r"^[A-Za-z0-9_.:-]{1,120}$")


class CredentialError(RuntimeError):
    """Raised with a message safe to display. Never contains a secret."""


@dataclass(frozen=True)
class Backend:
    name: str
    available: bool
    writable: bool
    detail: str


def _keyring():
    try:
        import keyring
        from keyring.backends import fail
        backend = keyring.get_keyring()
        if isinstance(backend, fail.Keyring):
            return None
        return keyring
    except Exception:
        return None


def backend() -> Backend:
    """What is actually protecting credentials on this machine.

    Surfaced in the UI and in /api/v1/health: an operator should never have to
    guess whether the secret store is real.
    """
    kr = _keyring()
    if kr:
        name = type(kr.get_keyring()).__name__
        return Backend(f"OS keychain ({name})", True, True,
                       "Credentials are stored by the operating system.")
    if any(k.startswith(ENV_PREFIX) for k in os.environ):
        return Backend("environment", True, False,
                       "Credentials supplied by the environment. Veris cannot "
                       "store new ones on this machine.")
    return Backend("none", False, False,
                   "No OS credential store is available. Veris will not write "
                   "credentials to disk; supply them via environment variables "
                   f"named {ENV_PREFIX}<CONNECTION>_<FIELD>.")


def _env_name(connection_id: str, field: str) -> str:
    return f"{ENV_PREFIX}{connection_id}_{field}".upper().replace("-", "_")


def _validate(connection_id: str, field: str) -> None:
    for value, label in ((connection_id, "connection id"), (field, "field name")):
        if not _SAFE_KEY.match(value):
            raise CredentialError(f"Invalid {label}")


def store_credential(connection_id: str, field: str, value: str) -> None:
    _validate(connection_id, field)
    if not value:
        raise CredentialError("Refusing to store an empty credential")
    kr = _keyring()
    if not kr:
        raise CredentialError(
            "No OS credential store is available on this machine. Veris does not "
            "write credentials to disk. Supply this value as the environment "
            f"variable {_env_name(connection_id, field)} instead.")
    kr.set_password(SERVICE, f"{connection_id}:{field}", value)


def get_credential(connection_id: str, field: str) -> str | None:
    _validate(connection_id, field)
    env = os.environ.get(_env_name(connection_id, field))
    if env:
        return env
    kr = _keyring()
    if not kr:
        return None
    try:
        return kr.get_password(SERVICE, f"{connection_id}:{field}")
    except Exception:
        return None


def delete_credential(connection_id: str, field: str) -> None:
    _validate(connection_id, field)
    kr = _keyring()
    if not kr:
        return
    try:
        kr.delete_password(SERVICE, f"{connection_id}:{field}")
    except Exception:
        pass  # already absent is the desired end state


def load_credentials(connection_id: str, fields: list[str]) -> dict[str, str]:
    out = {}
    for f in fields:
        v = get_credential(connection_id, f)
        if v:
            out[f] = v
    return out


def forget_connection(connection_id: str, fields: list[str]) -> None:
    """Called on disconnect. Removing the connection must remove its secrets."""
    for f in fields:
        delete_credential(connection_id, f)
