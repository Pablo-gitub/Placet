"""Dedicated confirmation UI, with no shell or privileged execution endpoint.

The protected service supplies operation IDs and recomputes their contents.
Enrollment responses are staged only: a root terminal must verify and finalize
them. Never run this network-facing process as root.
"""
import hashlib
import json
import secrets
import sqlite3
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field


STATIC = Path(__file__).with_name("web_static")
SECURITY_HEADERS = {
    "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
    "Permissions-Policy": "publickey-credentials-create=(self), publickey-credentials-get=(self)",
}


class EnrollmentMailbox:
    """Root provisions one-use invitations; the web process only stages replies.

    This database contains no credentials or authority to enable commands.
    Its parent and ownership are provisioned administratively. Invitation URLs
    belong only to the human; tokens must not enter access logs or Placet chat.
    """

    def __init__(self, path):
        self.path = path
        with sqlite3.connect(path) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS invitations (
                id TEXT PRIMARY KEY, token_hash TEXT NOT NULL,
                options TEXT NOT NULL, expires REAL NOT NULL, response TEXT)""")

    def provision(self, ceremony):
        import os
        if os.geteuid() != 0:
            raise ValueError("Enrollment invitation requires a root terminal")
        return self._write_invitation(ceremony)

    def _write_invitation(self, ceremony):
        # Unprivileged transport helper may write options, but cannot create
        # the corresponding ceremony in the root-private authorization store.
        token = secrets.token_urlsafe(32)
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO invitations VALUES (?, ?, ?, ?, NULL)",
                       (ceremony["id"], hashlib.sha256(token.encode()).hexdigest(),
                        json.dumps(ceremony["publicKey"]), time.time() + 300))
        return token

    def _invitation(self, db, identifier, token):
        row = db.execute("SELECT token_hash, options, expires, response FROM invitations WHERE id = ?",
                         (identifier,)).fetchone()
        if (not row or not secrets.compare_digest(row[0], hashlib.sha256(token.encode()).hexdigest())
                or time.time() >= row[2]):
            raise ValueError("Enrollment invitation is invalid or expired")
        return row

    def options(self, identifier, token):
        with sqlite3.connect(self.path) as db:
            row = self._invitation(db, identifier, token)
            if row[3] is not None:
                raise ValueError("Enrollment response already staged")
            return json.loads(row[1])

    def stage(self, identifier, token, response):
        encoded = json.dumps(response)
        if len(encoded) > 65536:
            raise ValueError("Registration response exceeds limit")
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._invitation(db, identifier, token)
            if row[3] is not None:
                raise ValueError("Enrollment response already staged")
            db.execute("UPDATE invitations SET response = ? WHERE id = ?", (encoded, identifier))

    def pending(self, identifier):
        import os
        if os.geteuid() != 0:
            raise ValueError("Enrollment completion requires a root terminal")
        return self._read_pending(identifier)

    def _read_pending(self, identifier):
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT response FROM invitations WHERE id = ? AND expires > ?",
                             (identifier, time.time())).fetchone()
        if not row or row[0] is None:
            raise ValueError("No unexpired registration response")
        return json.loads(row[0])


class Invitation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-f0-9]{32}$")
    token: str = Field(min_length=32, max_length=128)


class Registration(Invitation):
    credential: dict


def create_app(*, origin: str, mailbox: EnrollmentMailbox, trusted_unix_proxy: bool = False):
    from urllib.parse import urlsplit
    parsed = urlsplit(origin)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.path or parsed.query
            or parsed.fragment or parsed.username or parsed.password or parsed.port not in (None, 443)
            or origin != "https://" + parsed.hostname):
        raise ValueError("An exact dedicated HTTPS origin is required")
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        # Only the protected UDS deployment may trust Tailscale's overwritten
        # forwarded headers. Never enable this for a publicly reachable listener.
        # Reject fetches embedded by
        # the PWA and all cross-origin mutation attempts, with no CORS fallback.
        navigation = (request.method == "GET" and request.headers.get("sec-fetch-mode") == "navigate"
                      and request.headers.get("sec-fetch-dest") == "document")
        hosts = (parsed.hostname, parsed.hostname + ":443")
        valid_host = request.headers.get("host") in hosts
        if trusted_unix_proxy:
            valid_host = (request.headers.getlist("host") == ["localhost"]
                          and request.headers.getlist("x-forwarded-host") in ([hosts[0]], [hosts[1]])
                          and request.headers.getlist("x-forwarded-proto") == ["https"])
        if not valid_host:
            response = JSONResponse({"code": "wrong_host"}, status_code=403)
        elif request.headers.get("sec-fetch-site") in ("cross-site", "same-site") and not navigation:
            response = JSONResponse({"code": "wrong_origin"}, status_code=403)
        elif request.method not in ("GET", "HEAD") and request.headers.get("origin") != origin:
            response = JSONResponse({"code": "wrong_origin"}, status_code=403)
        else:
            # Limit actual body bytes, not just a potentially false header.
            if request.method == "POST":
                chunks, size = [], 0
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > 100000:
                        return JSONResponse({"code": "body_too_large"}, status_code=413, headers=SECURITY_HEADERS)
                    chunks.append(chunk)
                request._body = b"".join(chunks)
            response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        return response

    @app.exception_handler(ValueError)
    async def unavailable(_request, _error):
        return JSONResponse({"code": "invitation_unavailable"}, status_code=409)

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/assets/{name}")
    async def asset(name: str):
        if name not in ("app.js", "style.css", "en.json", "it.json"):
            return JSONResponse({"code": "not_found"}, status_code=404)
        return FileResponse(STATIC / name)

    @app.get("/api/status")
    async def status():
        return {"service": "placet-admin", "execution_enabled": False,
                "enrollment": "terminal_authorized", "origin": origin}

    @app.post("/api/enrollment/options")
    async def enrollment_options(body: Invitation):
        return {"publicKey": mailbox.options(body.id, body.token)}

    @app.post("/api/enrollment/stage")
    async def stage(body: Registration):
        mailbox.stage(body.id, body.token, body.credential)
        return {"state": "awaiting_terminal_verification", "execution_enabled": False}

    return app
