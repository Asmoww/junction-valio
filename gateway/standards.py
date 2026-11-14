"""Open procurement standards: Peppol BIS Ordering 3 orders and the mandate as a W3C Verifiable Credential.

Peppol: every placed order can be exported as a UBL 2.1 Order following Peppol BIS Ordering 3, so it can go
into any Peppol-capable ERP or access point unchanged. Checked against the official UBL 2.1 XSD in tests;
not checked against the Peppol schematron, and the party identifiers are demo values.

Mandate credential: the buyer signs its mandate as a W3C Verifiable Credential (VC Data Model 2.0, secured
as a JWT with EdDSA, typ "vc+jwt"). The buyer's public key is published as a did:web document. The gateway
checks the signature and that the credential matches the mandate it enforces, so a mandate nobody signed,
or one edited after signing, never authorises an order. In this demo the gateway holds the buyers' demo
keys; in production the buyer signs with its own key and the gateway only ever sees the public key.
"""
import base64
import hashlib
import json
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .config import STATE_DIR

DID_HOST = "valio.psavela.com"

# ---------------------------------------------------------------------------------------------- Peppol

UBL = "urn:oasis:names:specification:ubl:schema:xsd:Order-2"
CAC = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
CBC = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
ET.register_namespace("", UBL)
ET.register_namespace("cac", CAC)
ET.register_namespace("cbc", CBC)

# Valio sales units -> UN/ECE Recommendation 20/21 codes (what Peppol expects in unitCode)
UNIT_CODES = {"ST": "H87", "KG": "KGM", "KI": "XCR", "PAK": "XPA", "BAG": "XBG", "RAS": "XBX", "PRK": "XJR",
              "BOT": "XBO", "SK": "XSA", "CS": "XCS", "KLE": "XPK", "KRT": "XCT", "KAN": "XJC", "PAL": "XPX",
              "NIP": "XBE", "DZ": "DZN"}

SELLER = {"name": "Valio Aimo (demo)", "endpoint": ("0216", "003700000000"), "id": "VALIO-AIMO"}


def _el(parent, ns, tag, text=None, **attrs):
    e = ET.SubElement(parent, f"{{{ns}}}{tag}", attrs)
    if text is not None:
        e.text = str(text)
    return e


def _amount(parent, tag, value, currency="EUR"):
    _el(parent, CBC, tag, f"{value:.2f}", currencyID=currency)


def _party(parent, wrapper, name, endpoint, party_id, contact=None):
    w = _el(parent, CAC, wrapper)
    p = _el(w, CAC, "Party")
    _el(p, CBC, "EndpointID", endpoint[1], schemeID=endpoint[0])
    _el(_el(p, CAC, "PartyIdentification"), CBC, "ID", party_id)
    _el(_el(p, CAC, "PartyName"), CBC, "Name", name)
    _el(_el(_el(p, CAC, "PostalAddress"), CAC, "Country"), CBC, "IdentificationCode", "FI")
    _el(_el(p, CAC, "PartyLegalEntity"), CBC, "RegistrationName", name)
    if contact:
        _el(_el(p, CAC, "Contact"), CBC, "Name", contact)


def peppol_order(order, mandate, base_url=""):
    """UBL 2.1 Order (Peppol BIS Ordering 3) for a placed order, as UTF-8 XML bytes."""
    offer = order["record"]["offer"]
    created = datetime.fromisoformat(order["created"]).astimezone(timezone.utc)
    approvals = order["record"].get("approvals") or []
    approved_by = next((a.get("approver") for a in approvals if a.get("decision") == "approve"), None)

    root = ET.Element(f"{{{UBL}}}Order")
    _el(root, CBC, "CustomizationID", "urn:fdc:peppol.eu:poacc:trns:order:3")
    _el(root, CBC, "ProfileID", "urn:fdc:peppol.eu:poacc:bis:ordering:3")
    _el(root, CBC, "ID", order["id"])
    _el(root, CBC, "IssueDate", created.date().isoformat())
    _el(root, CBC, "IssueTime", created.strftime("%H:%M:%S"))
    _el(root, CBC, "OrderTypeCode", "220")
    _el(root, CBC, "Note", "Placed by a purchasing agent through the Trusted Trade Gateway at the catalog price "
                           "in a signed offer. " + ("Approved by " + approved_by + "." if approved_by else
                                                   "Within the agent's mandate.") + " Demo identifiers.")
    _el(root, CBC, "DocumentCurrencyCode", offer.get("currency", "EUR"))
    _el(root, CBC, "CustomerReference", mandate["mandate_id"])
    _el(_el(root, CAC, "ValidityPeriod"), CBC, "EndDate", offer["expires_at"][:10])
    _el(_el(root, CAC, "QuotationDocumentReference"), CBC, "ID", offer["offer_id"])
    ref = _el(root, CAC, "AdditionalDocumentReference")
    _el(ref, CBC, "ID", offer["offer_id"])
    _el(ref, CBC, "DocumentDescription", "Ed25519-signed fixed-price offer (JWS); key at /.well-known/jwks.json")
    if base_url:
        _el(_el(_el(ref, CAC, "Attachment"), CAC, "ExternalReference"), CBC, "URI",
            f"{base_url}/v1/orders/{order['id']}/decision")

    buyer_endpoint = ("0216", "0037" + str(mandate.get("customer", "0")).zfill(8))
    _party(root, "BuyerCustomerParty", mandate["buyer"], buyer_endpoint, str(mandate.get("customer", "")),
           contact=approved_by)
    _party(root, "SellerSupplierParty", SELLER["name"], SELLER["endpoint"], SELLER["id"])

    delivery = _el(root, CAC, "Delivery")
    period = _el(delivery, CAC, "RequestedDeliveryPeriod")
    _el(period, CBC, "StartDate", offer["deliver_by"])
    _el(period, CBC, "EndDate", offer["deliver_by"])

    lines_total = sum(line["lineTotal"] for line in offer["lines"])
    totals = _el(root, CAC, "AnticipatedMonetaryTotal")
    _amount(totals, "LineExtensionAmount", lines_total)
    _amount(totals, "PayableAmount", lines_total)

    for i, line in enumerate(offer["lines"], 1):
        item_line = _el(_el(root, CAC, "OrderLine"), CAC, "LineItem")
        _el(item_line, CBC, "ID", i)
        unit = UNIT_CODES.get(line.get("salesUnit", "ST"), "H87")
        _el(item_line, CBC, "Quantity", f"{line['units']:g}", unitCode=unit)
        _amount(item_line, "LineExtensionAmount", line["lineTotal"])
        _el(item_line, CBC, "PartialDeliveryIndicator", "false")
        price = _el(item_line, CAC, "Price")
        _amount(price, "PriceAmount", line["unitPrice"])
        _el(price, CBC, "BaseQuantity", "1", unitCode=unit)
        item = _el(item_line, CAC, "Item")
        _el(item, CBC, "Name", line["name"])
        _el(_el(item, CAC, "SellersItemIdentification"), CBC, "ID", line["productId"])

    ET.indent(root)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


# ---------------------------------------------------------------------------------------- Credentials

_KEY_DIR = STATE_DIR / "buyer_keys"
_VC_DIR = STATE_DIR / "mandate_credentials"


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def buyer_slug(mandate_id):
    return mandate_id.removeprefix("mnd_")


def buyer_did(mandate_id):
    return f"did:web:{DID_HOST}:buyers:{buyer_slug(mandate_id)}"


def _buyer_key(mandate_id):
    """The buyer's demo signing key. In production this never leaves the buyer."""
    _KEY_DIR.mkdir(parents=True, exist_ok=True)
    f = _KEY_DIR / f"{buyer_slug(mandate_id)}.pem"
    if not f.exists():
        k = Ed25519PrivateKey.generate()
        f.write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                      serialization.NoEncryption()))
        f.chmod(0o600)
    return serialization.load_pem_private_key(f.read_bytes(), None)


def _pub_raw(mandate_id):
    return _buyer_key(mandate_id).public_key().public_bytes(serialization.Encoding.Raw,
                                                            serialization.PublicFormat.Raw)


def did_document(mandate_id):
    did = buyer_did(mandate_id)
    vm = {"id": f"{did}#key-1", "type": "JsonWebKey", "controller": did,
          "publicKeyJwk": {"kty": "OKP", "crv": "Ed25519", "x": _b64(_pub_raw(mandate_id))}}
    return {"@context": ["https://www.w3.org/ns/did/v1", "https://w3id.org/security/jwk/v1"], "id": did,
            "verificationMethod": [vm], "assertionMethod": [vm["id"]]}


def _digest(mandate):
    return hashlib.sha256(json.dumps(mandate, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def issue(mandate):
    """Sign the mandate as a VC (vc+jwt) with the buyer's key."""
    did = buyer_did(mandate["mandate_id"])
    vc = {
        "@context": ["https://www.w3.org/ns/credentials/v2"],
        "id": f"urn:mandate:{mandate['mandate_id']}:{_digest(mandate)[:12]}",
        "type": ["VerifiableCredential", "PurchasingMandateCredential"],
        "issuer": {"id": did, "name": mandate["buyer"]},
        "validFrom": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "validUntil": f"{mandate['valid_until']}T23:59:59Z",
        "credentialSubject": {"id": f"urn:agent:{mandate.get('agent', 'purchasing-agent')}",
                              "authorisedBy": did, "supplier": SELLER["name"], "mandate": mandate},
    }
    header = {"alg": "EdDSA", "typ": "vc+jwt", "cty": "vc", "kid": f"{did}#key-1"}
    signing_input = f"{_b64(json.dumps(header, separators=(',', ':')).encode())}." \
                    f"{_b64(json.dumps(vc, separators=(',', ':')).encode())}"
    return f"{signing_input}.{_b64(_buyer_key(mandate['mandate_id']).sign(signing_input.encode()))}"


def credential(mandate):
    """The stored credential for this mandate (issued if there is none). Not re-issued when the mandate
    changes: an edited mandate then fails verification until the buyer signs it again (`sign_all`)."""
    _VC_DIR.mkdir(parents=True, exist_ok=True)
    f = _VC_DIR / f"{mandate['mandate_id']}.jwt"
    if not f.exists():
        f.write_text(issue(mandate))
    return f.read_text().strip()


def sign_all(mandates):
    """Demo stand-in for each buyer signing its current mandate (run at startup). Returns re-signed ids."""
    resigned = []
    for m in mandates:
        f = _VC_DIR / f"{m['mandate_id']}.jwt"
        if not f.exists() or not verify(f.read_text().strip(), m)[0]:
            _VC_DIR.mkdir(parents=True, exist_ok=True)
            f.write_text(issue(m))
            resigned.append(m["mandate_id"])
    return resigned


def _resolve(did):
    """did:web resolution for the buyers this gateway hosts. Other DIDs are not trusted."""
    prefix = f"did:web:{DID_HOST}:buyers:"
    if not did.startswith(prefix):
        return None
    slug = did.removeprefix(prefix)
    if not (_KEY_DIR / f"{slug}.pem").exists():
        return None
    return did_document(f"mnd_{slug}")


def verify(token, mandate=None):
    """Check a mandate credential. Returns (ok, credential payload or reason).

    With `mandate`, also checks that the credential covers exactly that mandate."""
    try:
        h, p, s = token.split(".")
        header, vc = json.loads(_unb64(h)), json.loads(_unb64(p))
    except Exception:  # noqa: BLE001
        return False, "not a credential (malformed JWT)"
    if header.get("alg") != "EdDSA" or header.get("typ") != "vc+jwt":
        return False, "unsupported credential format"
    issuer = (vc.get("issuer") or {}).get("id") if isinstance(vc.get("issuer"), dict) else vc.get("issuer")
    doc = _resolve(issuer or "")
    if not doc:
        return False, f"unknown issuer {issuer!r}"
    vm = next((m for m in doc["verificationMethod"] if m["id"] == header.get("kid")), None)
    if not vm:
        return False, "signing key not in the issuer's DID document"
    try:
        Ed25519PublicKey.from_public_bytes(_unb64(vm["publicKeyJwk"]["x"])).verify(_unb64(s), f"{h}.{p}".encode())
    except Exception:  # noqa: BLE001
        return False, "signature does not match the buyer's key"
    if "VerifiableCredential" not in vc.get("type", []):
        return False, "not a Verifiable Credential"
    if vc.get("validUntil") and datetime.fromisoformat(vc["validUntil"]) < datetime.now(timezone.utc):
        return False, f"credential expired {vc['validUntil']}"
    signed = vc.get("credentialSubject", {}).get("mandate")
    if mandate is not None and (not signed or _digest(signed) != _digest(mandate)):
        return False, "mandate differs from what the buyer signed"
    if signed and buyer_did(signed.get("mandate_id", "")) != issuer:
        return False, "credential issued by a different buyer"
    return True, vc
