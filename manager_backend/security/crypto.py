"""
manager_backend/security/crypto.py
Funzioni di utilità crittografica e hashing per token e audit.
"""
from __future__ import annotations

import base64
import hashlib
import secrets


def generate_session_token() -> str:
    """Genera un token di sessione opaco ad alta entropia (256 bit)."""
    return secrets.token_urlsafe(32)


def generate_state_token() -> str:
    """Genera un token CSRF state per flussi OAuth2."""
    return secrets.token_urlsafe(16)


def hash_identifier(value: str) -> str:
    """Calcola un hash SHA-256 troncato per mascherare IP o identificatori sensibili nei log."""
    if not value:
        return "unknown"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def verify_pkce_verifier(verifier: str, challenge: str, method: str = "S256") -> bool:
    """Verifica un PKCE code_verifier a fronte del code_challenge registrato."""
    if method == "plain":
        return secrets.compare_digest(verifier, challenge)
    if method == "S256":
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        computed = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
        return secrets.compare_digest(computed, challenge)
    return False
