"""
AES-256-GCM token encryption (T-050).

OAuth tokens are AES-256 encrypted at rest in the `integrations` table.
They are decrypted only server-side in the integration worker — never
exposed to the widget or any client-facing response.

Key material:
  TOKEN_ENCRYPTION_KEY env var — 32 raw bytes, base64url-encoded.
  Generate: python -c "import os,base64; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"

Cipher: AES-256-GCM
  - 96-bit nonce prepended to ciphertext
  - 128-bit authentication tag appended by AESGCM (handled by cryptography lib)
  - Output: base64url(nonce + ciphertext+tag)
"""

from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class TokenCrypto:
    """Stateless AES-256-GCM encrypt/decrypt for OAuth token strings."""

    def __init__(self, key_b64: str) -> None:
        key_bytes = base64.urlsafe_b64decode(key_b64 + "==")  # tolerant padding
        if len(key_bytes) != 32:
            raise ValueError("TOKEN_ENCRYPTION_KEY must decode to exactly 32 bytes (AES-256)")
        self._aesgcm = AESGCM(key_bytes)

    def encrypt(self, plaintext: str) -> str:
        """Encrypt a token string. Returns base64url-encoded ciphertext."""
        nonce = os.urandom(12)  # 96-bit nonce — never reuse
        ciphertext = self._aesgcm.encrypt(nonce, plaintext.encode(), aad=None)
        return base64.urlsafe_b64encode(nonce + ciphertext).decode()

    def decrypt(self, ciphertext_b64: str) -> str:
        """Decrypt a base64url-encoded ciphertext. Raises ValueError on tamper."""
        raw = base64.urlsafe_b64decode(ciphertext_b64 + "==")
        nonce, ciphertext = raw[:12], raw[12:]
        return self._aesgcm.decrypt(nonce, ciphertext, aad=None).decode()


def get_token_crypto() -> TokenCrypto:
    """Build TokenCrypto from env. Call once at worker startup — not per-request."""
    key = os.environ.get("TOKEN_ENCRYPTION_KEY")
    if not key:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY env var is required")
    return TokenCrypto(key)
