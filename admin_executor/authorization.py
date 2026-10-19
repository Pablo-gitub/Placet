"""Independent WebAuthn verification and atomic, durable job authorization.

No HTTP routes or privileged dispatcher. All configuration/database paths must
be protected from the backend, bridge and desktop user in production. Callers
must recompute the confirmation from the protected catalogue and current state.
"""
import hashlib
import json
import os
import secrets
import sqlite3
import time
import uuid
from urllib.parse import urlsplit

from .jobs import canonical_proposal, PROPOSAL_TTL_SECONDS


def confirmation(action, parameters, *, installed_commit, artifact_sha256, summary):
    """Canonical operation shown by the protected page, never a PWA description."""
    import re
    if installed_commit is not None and not re.fullmatch(r"[0-9a-f]{40}", installed_commit):
        raise ValueError("Invalid installed commit")
    if action == "deploy" and (not isinstance(artifact_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", artifact_sha256)):
        raise ValueError("A frozen build artifact digest is required")
    if action != "deploy" and artifact_sha256 is not None:
        raise ValueError("Unexpected artifact digest")
    if not isinstance(summary, str) or not summary or len(summary) > 8192:
        raise ValueError("Invalid confirmation summary")
    return json.dumps({"proposal": json.loads(canonical_proposal(action, parameters)),
                       "handler_revision": 1, "installed_commit": installed_commit,
                       "artifact_sha256": artifact_sha256, "summary": summary},
                      sort_keys=True, separators=(",", ":"))


class AuthorizationStore:
    def __init__(self, path, *, rp_id, origin):
        parsed = urlsplit(origin)
        if (parsed.scheme != "https" or parsed.hostname != rp_id or parsed.username or parsed.password
                or parsed.path or parsed.query or parsed.fragment or parsed.port not in (None, 443)
                or origin != "https://" + rp_id or "." not in rp_id or rp_id == "ts.net"):
            raise ValueError("Use an exact, dedicated HTTPS origin and RP hostname")
        self.path, self.rp_id, self.origin = path, rp_id, origin
        with sqlite3.connect(path) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS credentials (
                    credential_id BLOB PRIMARY KEY, public_key BLOB NOT NULL,
                    sign_count INTEGER NOT NULL, revoked INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS challenges (
                    id TEXT PRIMARY KEY, nonce BLOB NOT NULL, operation TEXT NOT NULL,
                    expires REAL NOT NULL, consumed INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS authorized_jobs (
                    id TEXT PRIMARY KEY, challenge_id TEXT UNIQUE NOT NULL,
                    operation TEXT NOT NULL, digest TEXT NOT NULL,
                    credential_id BLOB NOT NULL, assertion TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state = 'authorized'));
                CREATE TABLE IF NOT EXISTS identity (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    rp_id TEXT NOT NULL, origin TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS enrollments (
                    id TEXT PRIMARY KEY, nonce BLOB NOT NULL,
                    expires REAL NOT NULL, consumed INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS confirmation_proposals (
                    id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL,
                    canonical TEXT NOT NULL, created_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS challenge_proposals (
                    challenge_id TEXT PRIMARY KEY, proposal_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS proposal_jobs (
                    proposal_id TEXT PRIMARY KEY, job_id TEXT UNIQUE NOT NULL);
            """)
            db.execute("INSERT OR IGNORE INTO identity VALUES (1, ?, ?)", (rp_id, origin))
            identity = db.execute("SELECT rp_id, origin FROM identity WHERE singleton = 1").fetchone()
            if identity != (rp_id, origin):
                raise ValueError("Administrative origin changed; explicit credential migration required")

    def begin_enrollment(self):
        """Terminal-only administrative ceremony, never a PWA/agent endpoint."""
        from webauthn import generate_registration_options, options_to_json
        from webauthn.helpers.structs import AuthenticatorSelectionCriteria, UserVerificationRequirement

        if os.geteuid() != 0:
            raise ValueError("Enrollment requires a root-authorized terminal operation")
        identifier, nonce = uuid.uuid4().hex, secrets.token_bytes(32)
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO enrollments VALUES (?, ?, ?, 0)",
                       (identifier, nonce, time.time() + 300))
        options = generate_registration_options(
            rp_id=self.rp_id, rp_name="Placet Admin", user_name="supervisor",
            user_id=b"placet-human-supervisor", challenge=nonce, timeout=300000,
            authenticator_selection=AuthenticatorSelectionCriteria(
                user_verification=UserVerificationRequirement.REQUIRED))
        return {"id": identifier, "publicKey": json.loads(options_to_json(options))}

    def finish_enrollment(self, identifier, response):
        from webauthn import base64url_to_bytes, verify_registration_response

        if os.geteuid() != 0:
            raise ValueError("Enrollment requires a root-authorized terminal operation")
        if not isinstance(response, dict) or len(json.dumps(response)) > 65536:
            raise ValueError("Invalid WebAuthn registration")
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            ceremony = db.execute("SELECT nonce, expires, consumed FROM enrollments WHERE id = ?", (identifier,)).fetchone()
            if not ceremony or ceremony[2] or time.time() >= ceremony[1]:
                raise ValueError("Enrollment is unknown, expired or consumed")
            client = json.loads(base64url_to_bytes(response["response"]["clientDataJSON"]))
            if client.get("crossOrigin", False) or client.get("topOrigin"):
                raise ValueError("Cross-origin enrollment is forbidden")
            verified = verify_registration_response(
                credential=response, expected_challenge=ceremony[0],
                expected_rp_id=self.rp_id, expected_origin=self.origin,
                require_user_verification=True, require_user_presence=True)
            if time.time() >= ceremony[1]:
                raise ValueError("Enrollment expired during verification")
            db.execute("INSERT INTO credentials VALUES (?, ?, ?, 0)",
                       (verified.credential_id, verified.credential_public_key, verified.sign_count))
            db.execute("UPDATE enrollments SET consumed = 1 WHERE id = ?", (identifier,))
        return {"enrolled": True}

    def revoke(self, credential_id):
        if os.geteuid() != 0:
            raise ValueError("Revocation requires a root-authorized terminal operation")
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE credentials SET revoked = 1 WHERE credential_id = ?", (credential_id,))

    def challenge(self, operation: str, *, proposal_id=None):
        from webauthn import generate_authentication_options, options_to_json
        from webauthn.helpers.structs import PublicKeyCredentialDescriptor, UserVerificationRequirement

        # Only canonical operations produced by the protected handler are
        # accepted. Revalidating structure is not a substitute for recomputing
        # the actual installed version and display summary before each call.
        parsed = json.loads(operation)
        proposal = parsed["proposal"]
        expected = confirmation(proposal["action"], proposal["parameters"],
                                installed_commit=parsed["installed_commit"],
                                artifact_sha256=parsed["artifact_sha256"], summary=parsed["summary"])
        if expected != operation:
            raise ValueError("Noncanonical administrative operation")
        with sqlite3.connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            if proposal_id is not None:
                bound = db.execute('SELECT canonical,created_at FROM confirmation_proposals WHERE id=?', (proposal_id,)).fetchone()
                if not bound or bound[0] != canonical_proposal(proposal['action'], proposal['parameters']):
                    raise ValueError('Proposal binding changed')
                existing = db.execute('SELECT job_id FROM proposal_jobs WHERE proposal_id=?', (proposal_id,)).fetchone()
                if existing:
                    return {'job_id': existing[0], 'state': 'authorized'}
                if time.time() >= bound[1] + PROPOSAL_TTL_SECONDS:
                    raise ValueError('Proposal expired')
            credentials = db.execute("SELECT credential_id FROM credentials WHERE revoked = 0").fetchall()
            if not credentials:
                raise ValueError("No administratively enrolled passkey")
            challenge_id, nonce = uuid.uuid4().hex, secrets.token_bytes(32)
            db.execute("INSERT INTO challenges VALUES (?, ?, ?, ?, 0)",
                       (challenge_id, nonce, operation, time.time() + 60))
            if proposal_id is not None:
                db.execute('INSERT INTO challenge_proposals VALUES (?,?)', (challenge_id, proposal_id))
        options = generate_authentication_options(rp_id=self.rp_id, challenge=nonce, timeout=60000,
                   allow_credentials=[PublicKeyCredentialDescriptor(id=row[0]) for row in credentials],
                   user_verification=UserVerificationRequirement.REQUIRED)
        return {"id": challenge_id, "operation": json.loads(operation),
                "publicKey": json.loads(options_to_json(options))}

    def authorize(self, challenge_id, assertion: dict, *, current_operation: str, proposal_id=None):
        from webauthn import base64url_to_bytes, verify_authentication_response

        if not isinstance(assertion, dict) or len(json.dumps(assertion)) > 32768:
            raise ValueError("Invalid WebAuthn assertion")
        credential_id = base64url_to_bytes(assertion["id"])
        with sqlite3.connect(self.path) as db:
            # Serialize the credential counter, one-use challenge and job
            # creation in a single persistent transaction, including retries.
            db.execute("BEGIN IMMEDIATE")
            challenge = db.execute("SELECT nonce, operation, expires, consumed FROM challenges WHERE id = ?",
                                   (challenge_id,)).fetchone()
            if not challenge or challenge[1] != current_operation:
                raise ValueError("Operation changed or challenge unknown")
            bound = db.execute('SELECT proposal_id FROM challenge_proposals WHERE challenge_id=?', (challenge_id,)).fetchone()
            if (bound[0] if bound else None) != proposal_id:
                raise ValueError('Challenge belongs to another proposal')
            if challenge[3]:
                if proposal_id is not None:
                    previous = db.execute('''SELECT a.id,a.assertion FROM authorized_jobs a
                        JOIN proposal_jobs p ON p.job_id=a.id
                        WHERE p.proposal_id=? AND a.challenge_id=?''', (proposal_id, challenge_id)).fetchone()
                    if previous and previous[1] == json.dumps(assertion, sort_keys=True):
                        return {'job_id': previous[0], 'state': 'authorized'}
                raise ValueError("Authorization challenge already consumed")
            if time.time() >= challenge[2]:
                raise ValueError("Authorization challenge expired")
            proposal_deadline = None
            if proposal_id is not None:
                intent = db.execute('SELECT created_at FROM confirmation_proposals WHERE id=?', (proposal_id,)).fetchone()
                if intent is None:
                    raise ValueError('Unknown proposal')
                proposal_deadline = intent[0] + PROPOSAL_TTL_SECONDS
                if time.time() >= proposal_deadline:
                    raise ValueError('Proposal expired')
            credential = db.execute("SELECT public_key, sign_count FROM credentials WHERE credential_id = ? AND revoked = 0",
                                    (credential_id,)).fetchone()
            if not credential:
                raise ValueError("Passkey is unknown or revoked")
            client = json.loads(base64url_to_bytes(assertion["response"]["clientDataJSON"]))
            if client.get("crossOrigin", False) or client.get("topOrigin"):
                raise ValueError("Cross-origin authentication is forbidden")
            verified = verify_authentication_response(
                credential=assertion, expected_challenge=challenge[0], expected_rp_id=self.rp_id,
                expected_origin=self.origin, credential_public_key=credential[0],
                credential_current_sign_count=credential[1], require_user_verification=True)
            if verified.credential_id != credential_id:
                raise ValueError("Credential identity mismatch")
            if time.time() >= challenge[2]:
                raise ValueError("Authorization challenge expired during verification")
            if proposal_deadline is not None and time.time() >= proposal_deadline:
                raise ValueError('Proposal expired during verification')
            job_id = uuid.uuid4().hex
            db.execute("UPDATE credentials SET sign_count = ? WHERE credential_id = ?",
                       (verified.new_sign_count, credential_id))
            db.execute("UPDATE challenges SET consumed = 1 WHERE id = ?", (challenge_id,))
            if proposal_id is not None:
                previous = db.execute('SELECT job_id FROM proposal_jobs WHERE proposal_id=?', (proposal_id,)).fetchone()
                if previous:
                    return {'job_id': previous[0], 'state': 'authorized'}
            db.execute("INSERT INTO authorized_jobs VALUES (?, ?, ?, ?, ?, ?, 'authorized')",
                       (job_id, challenge_id, current_operation,
                        hashlib.sha256(current_operation.encode()).hexdigest(), credential_id,
                        json.dumps(assertion, sort_keys=True)))
            if proposal_id is not None:
                db.execute('INSERT INTO proposal_jobs VALUES (?,?)', (proposal_id, job_id))
        return {"job_id": job_id, "state": "authorized"}
