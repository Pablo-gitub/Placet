"""Real ES256 WebAuthn signatures; no mocked cryptographic verification."""
import base64
import hashlib
import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

pytest.importorskip("webauthn", reason="Install admin_executor/requirements.txt for passkey tests")
import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from webauthn.helpers.exceptions import InvalidAuthenticationResponse

from admin_executor.authorization import AuthorizationStore, confirmation


RP = "admin.example.test"
ORIGIN = "https://" + RP


def b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


@pytest.fixture
def setup(tmp_path):
    path = tmp_path / "authorization.sqlite"
    store = AuthorizationStore(path, rp_id=RP, origin=ORIGIN)
    private = ec.generate_private_key(ec.SECP256R1())
    public = private.public_key().public_numbers()
    cose = cbor2.dumps({1: 2, 3: -7, -1: 1,
                       -2: public.x.to_bytes(32, "big"), -3: public.y.to_bytes(32, "big")})
    # Test provisioning only. Production enrollment must verify a separate
    # registration ceremony authorized from a root-only terminal command.
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO credentials VALUES (?, ?, 0, 0)", (b"phone", cose))
    operation = confirmation("deploy", {"commit": "a" * 40}, installed_commit=None,
                             artifact_sha256="b" * 64, summary="Deploy reviewed commit aaaa")
    return path, store, private, operation


def assertion(private, challenge, *, origin=ORIGIN, rp=RP, flags=5, cross_origin=False):
    client = json.dumps({"type": "webauthn.get", "challenge": challenge["publicKey"]["challenge"],
                         "origin": origin, "crossOrigin": cross_origin}).encode()
    auth = hashlib.sha256(rp.encode()).digest() + bytes([flags]) + (1).to_bytes(4, "big")
    signature = private.sign(auth + hashlib.sha256(client).digest(), ec.ECDSA(hashes.SHA256()))
    return {"id": b64(b"phone"), "rawId": b64(b"phone"), "type": "public-key",
            "response": {"clientDataJSON": b64(client), "authenticatorData": b64(auth),
                         "signature": b64(signature), "userHandle": None}}


def test_valid_signature_creates_one_durable_job_and_replay_fails(setup):
    path, store, private, operation = setup
    challenge = store.challenge(operation)
    assert challenge["publicKey"]["userVerification"] == "required"
    response = assertion(private, challenge)
    result = store.authorize(challenge["id"], response, current_operation=operation)
    assert result["state"] == "authorized"
    restored = AuthorizationStore(path, rp_id=RP, origin=ORIGIN)
    with pytest.raises(ValueError, match="consumed"):
        restored.authorize(challenge["id"], response, current_operation=operation)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM authorized_jobs").fetchone()[0] == 1


@pytest.mark.parametrize("changes", [
    {"origin": "https://pwa.example.test"}, {"rp": "pwa.example.test"},
    {"flags": 1}, {"cross_origin": True},
])
def test_wrong_origin_rp_missing_verification_and_embedded_context_fail(setup, changes):
    path, store, private, operation = setup
    challenge = store.challenge(operation)
    with pytest.raises((ValueError, InvalidAuthenticationResponse)):
        store.authorize(challenge["id"], assertion(private, challenge, **changes), current_operation=operation)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM authorized_jobs").fetchone()[0] == 0


def test_operation_change_and_expiration_refuse_valid_signature(setup):
    path, store, private, operation = setup
    challenge = store.challenge(operation)
    response = assertion(private, challenge)
    with pytest.raises(ValueError, match="Operation changed"):
        store.authorize(challenge["id"], response, current_operation=operation.replace("aaaa", "cccc"))
    with sqlite3.connect(path) as db:
        db.execute("UPDATE challenges SET expires = 0")
    with pytest.raises(ValueError, match="expired"):
        store.authorize(challenge["id"], response, current_operation=operation)


def test_wrong_signature_and_revoked_key_do_not_authorize(setup):
    path, store, private, operation = setup
    challenge = store.challenge(operation)
    other = ec.generate_private_key(ec.SECP256R1())
    with pytest.raises(InvalidAuthenticationResponse):
        store.authorize(challenge["id"], assertion(other, challenge), current_operation=operation)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE credentials SET revoked = 1")
    with pytest.raises(ValueError, match="revoked"):
        store.authorize(challenge["id"], assertion(private, challenge), current_operation=operation)


def test_concurrent_assertion_consumption_never_duplicates_job(setup):
    path, store, private, operation = setup
    challenge = store.challenge(operation)
    response = assertion(private, challenge)

    def attempt(_):
        try:
            return store.authorize(challenge["id"], response, current_operation=operation)["state"]
        except ValueError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, range(2))) == ["authorized", "rejected"]
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM authorized_jobs").fetchone()[0] == 1


@pytest.mark.parametrize("rp,origin", [
    (RP, "http://" + RP), (RP, "https://pwa.example.test"),
    (RP, ORIGIN + ":8443"), ("ts.net", "https://ts.net"), (RP, ORIGIN + "/"),
])
def test_unsafe_origins_refused(tmp_path, rp, origin):
    with pytest.raises(ValueError):
        AuthorizationStore(tmp_path / "auth.sqlite", rp_id=rp, origin=origin)


def test_origin_cannot_change_while_retaining_credentials(setup):
    path, _, _, _ = setup
    with pytest.raises(ValueError, match="credential migration"):
        AuthorizationStore(path, rp_id="other.example.test", origin="https://other.example.test")


def test_enrollment_and_revocation_require_root_terminal(setup, monkeypatch):
    _, store, _, _ = setup
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    for operation in (store.begin_enrollment, lambda: store.finish_enrollment("id", {}),
                      lambda: store.revoke(b"phone")):
        with pytest.raises(ValueError, match="root-authorized"):
            operation()


def test_real_registration_and_enrollment_replay(tmp_path, monkeypatch):
    path = tmp_path / "enrollment.sqlite"
    store = AuthorizationStore(path, rp_id=RP, origin=ORIGIN)
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    registration = store.begin_enrollment()
    private = ec.generate_private_key(ec.SECP256R1())
    public = private.public_key().public_numbers()
    cose = cbor2.dumps({1: 2, 3: -7, -1: 1,
                       -2: public.x.to_bytes(32, "big"), -3: public.y.to_bytes(32, "big")})
    credential_id = b"phone"
    auth = (hashlib.sha256(RP.encode()).digest() + bytes([0x45]) + bytes(4) + bytes(16)
            + len(credential_id).to_bytes(2, "big") + credential_id + cose)
    client = json.dumps({"type": "webauthn.create", "challenge": registration["publicKey"]["challenge"],
                         "origin": ORIGIN, "crossOrigin": False}).encode()
    response = {"id": b64(credential_id), "rawId": b64(credential_id), "type": "public-key",
                "response": {"clientDataJSON": b64(client),
                             "attestationObject": b64(cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth}))}}
    assert store.finish_enrollment(registration["id"], response) == {"enrolled": True}
    with pytest.raises(ValueError, match="consumed"):
        store.finish_enrollment(registration["id"], response)
    operation = confirmation("restart-backend", {}, installed_commit=None,
                             artifact_sha256=None, summary="Restart backend")
    challenge = store.challenge(operation)
    assert store.authorize(challenge["id"], assertion(private, challenge), current_operation=operation)["state"] == "authorized"
    store.revoke(credential_id)
    with pytest.raises(ValueError, match="No administratively enrolled"):
        store.challenge(operation)
