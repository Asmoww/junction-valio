"""AP2 alignment: each signed offer is also an AP2-style merchant-signed checkout.

AP2 (Agent Payments Protocol, v0.2, ap2-protocol.org) has the merchant sign a JWT of the checkout, and mandates
reference it by `checkout_hash` (base64url SHA-256 of the checkout JWT). The spec requires a non-deterministic
signature scheme for the checkout JWT (ECDSA, e.g. ES256), not Ed25519, so this uses its own P-256 key, published in
/.well-known/jwks.json next to the Ed25519 offer key. Amounts are in minor units (cents), as AP2's payment amounts.

Scope: this produces the merchant's side (checkout JWT + hash). It does not issue or verify SD-JWT checkout or
payment mandates: the challenge has no payments, and the buyer's authority is the signed mandate credential.
"""
import base64
import hashlib
import json
import time

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature, encode_dss_signature

from .config import STATE_DIR

_KEY_FILE = STATE_DIR / "ap2_es256_key.pem"
MERCHANT = {"id": "valio-aimo", "name": "Valio Aimo", "website": "https://www.valioaimo.fi"}


def _b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _key():
    if not _KEY_FILE.exists():
        k = ec.generate_private_key(ec.SECP256R1())
        _KEY_FILE.write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                              serialization.NoEncryption()))
        _KEY_FILE.chmod(0o600)
    return serialization.load_pem_private_key(_KEY_FILE.read_bytes(), None)


def kid():
    n = _key().public_key().public_numbers()
    return "ap2-" + hashlib.sha256(n.x.to_bytes(32, "big") + n.y.to_bytes(32, "big")).hexdigest()[:12]


def jwk():
    n = _key().public_key().public_numbers()
    return {"kty": "EC", "crv": "P-256", "x": _b64(n.x.to_bytes(32, "big")), "y": _b64(n.y.to_bytes(32, "big")),
            "kid": kid(), "use": "sig", "alg": "ES256"}


def checkout(offer):
    """The offer as an AP2-style checkout object (amounts in minor units)."""
    cents = lambda eur: int(round(eur * 100))
    return {"id": offer["offer_id"], "merchant": MERCHANT, "currency": offer.get("currency", "EUR"),
            "line_items": [{"id": l["productId"], "name": l["name"], "quantity": l["units"], "unit": l.get("salesUnit"),
                            "price": {"amount": cents(l["unitPrice"]), "currency": "EUR"},
                            "total": {"amount": cents(l["lineTotal"]), "currency": "EUR"}} for l in offer["lines"]],
            "total": {"amount": cents(offer["total_eur"]), "currency": "EUR"},
            "deliver_by": offer.get("deliver_by"), "expires_at": offer.get("expires_at"),
            "buyer_mandate": offer.get("mandate_id"), "price_basis": offer.get("price_basis")}


def checkout_jwt(offer):
    """Merchant-signed checkout JWT (ES256) and its checkout_hash."""
    header = {"alg": "ES256", "typ": "JWT", "kid": kid()}
    claims = {"iss": MERCHANT["id"], "iat": int(time.time()), "checkout": checkout(offer)}
    signing_input = f"{_b64(json.dumps(header, separators=(',', ':')).encode())}.{_b64(json.dumps(claims, separators=(',', ':')).encode())}"
    r, s = decode_dss_signature(_key().sign(signing_input.encode(), ec.ECDSA(hashes.SHA256())))
    token = f"{signing_input}.{_b64(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"
    return {"checkout_jwt": token, "checkout_hash": _b64(hashlib.sha256(token.encode()).digest()), "kid": kid()}


def verify(token):
    """(ok, claims or reason) for a checkout JWT signed with this gateway's ES256 key."""
    try:
        h, p, sig = token.split(".")
        raw = _unb64(sig)
        der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
        _key().public_key().verify(der, f"{h}.{p}".encode(), ec.ECDSA(hashes.SHA256()))
        return True, json.loads(_unb64(p))
    except Exception as e:  # noqa: BLE001
        return False, f"signature invalid: {type(e).__name__}"
