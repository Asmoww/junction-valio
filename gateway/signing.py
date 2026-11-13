"""Ed25519-signed offers (compact JWS, alg EdDSA).

Every price/availability commitment the gateway makes is signed. Anyone can verify it with the
public key at /.well-known/jwks.json, so a buyer agent can trust the offer without a manual check
and neither side can dispute the price later.
"""
import base64
import hashlib
import json

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .config import STATE_DIR

_KEY_FILE = STATE_DIR / "signing_key.pem"


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _key():
    if not _KEY_FILE.exists():
        k = Ed25519PrivateKey.generate()
        _KEY_FILE.write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                              serialization.NoEncryption()))
        _KEY_FILE.chmod(0o600)
    return serialization.load_pem_private_key(_KEY_FILE.read_bytes(), None)


def _pub_raw():
    return _key().public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def kid():
    return hashlib.sha256(_pub_raw()).hexdigest()[:16]


def jwks():
    return {"keys": [{"kty": "OKP", "crv": "Ed25519", "x": _b64(_pub_raw()), "kid": kid(), "use": "sig", "alg": "EdDSA"}]}


def sign(payload: dict) -> str:
    header = {"alg": "EdDSA", "typ": "valio-offer+jws", "kid": kid()}
    signing_input = f"{_b64(json.dumps(header, separators=(',', ':')).encode())}." \
                    f"{_b64(json.dumps(payload, separators=(',', ':'), sort_keys=True).encode())}"
    return f"{signing_input}.{_b64(_key().sign(signing_input.encode()))}"


def verify(token: str):
    """Return (valid, payload or error)."""
    try:
        h, p, s = token.split(".")
        Ed25519PublicKey.from_public_bytes(_pub_raw()).verify(_unb64(s), f"{h}.{p}".encode())
        return True, json.loads(_unb64(p))
    except Exception as e:  # noqa: BLE001 - any failure means "not valid"
        return False, f"signature invalid: {type(e).__name__}"
