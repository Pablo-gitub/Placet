import json
import os
import sqlite3

import httpx
import pytest

from admin_executor.web import EnrollmentMailbox, STATIC, create_app


ORIGIN = "https://admin.example.test"


@pytest.fixture
def setup(tmp_path, monkeypatch):
    mailbox = EnrollmentMailbox(tmp_path / "mailbox.sqlite")
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    ceremony = {"id": "a" * 32, "publicKey": {"challenge": "challenge", "user": {"id": "human"}}}
    token = mailbox.provision(ceremony)
    app = create_app(origin=ORIGIN, mailbox=mailbox)
    return app, mailbox, {"id": ceremony["id"], "token": token}


@pytest.mark.asyncio
async def test_status_page_and_security_headers(setup):
    app, _, _ = setup
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=ORIGIN) as client:
        page = await client.get("/")
        assert page.status_code == 200
        assert page.headers["cache-control"] == "no-store"
        assert page.headers["referrer-policy"] == "no-referrer"
        assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
        assert (await client.get("/api/status")).json()["execution_enabled"] is False
        assert (await client.get("/docs")).status_code == 404
        assert (await client.get("/assets/unknown")).status_code == 404
        assert (await client.post("/api/execute", headers={"origin": ORIGIN})).status_code == 404


@pytest.mark.asyncio
async def test_invitation_can_stage_once_but_never_enrolls_or_executes(setup):
    app, mailbox, invitation = setup
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=ORIGIN,
                                headers={"origin": ORIGIN}) as client:
        assert (await client.post("/api/enrollment/options", json=invitation)).status_code == 200
        body = {**invitation, "credential": {"id": "response is not yet trusted"}}
        result = await client.post("/api/enrollment/stage", json=body)
        assert result.json() == {"state": "awaiting_terminal_verification", "execution_enabled": False}
        assert mailbox.pending(invitation["id"]) == body["credential"]
        assert (await client.post("/api/enrollment/stage", json=body)).status_code == 409
        assert (await client.post("/api/enrollment/options", json=invitation)).status_code == 409
    with sqlite3.connect(mailbox.path) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE name = 'credentials'").fetchone() is None


@pytest.mark.asyncio
async def test_wrong_origin_host_and_embedded_access_refused(setup):
    app, _, invitation = setup
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=ORIGIN) as client:
        for headers in ({}, {"origin": "https://pwa.example.test"},
                        {"origin": ORIGIN, "sec-fetch-site": "same-site"},
                        {"origin": ORIGIN, "host": "pwa.example.test"}):
            assert (await client.post("/api/enrollment/options", json=invitation, headers=headers)).status_code == 403
        assert (await client.get("/", headers={"sec-fetch-site": "same-site", "sec-fetch-mode": "navigate",
                                               "sec-fetch-dest": "document"})).status_code == 200
        assert (await client.get("/", headers={"sec-fetch-site": "same-site", "sec-fetch-mode": "navigate",
                                               "sec-fetch-dest": "iframe"})).status_code == 403


@pytest.mark.asyncio
async def test_protected_unix_proxy_validates_original_https_host(setup):
    _, mailbox, invitation = setup
    app = create_app(origin=ORIGIN, mailbox=mailbox, trusted_unix_proxy=True)
    headers = {"host": "localhost", "x-forwarded-host": "admin.example.test",
               "x-forwarded-proto": "https", "origin": ORIGIN}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=ORIGIN) as client:
        assert (await client.get("/api/status", headers=headers)).status_code == 200
        assert (await client.post("/api/enrollment/options", headers=headers, json=invitation)).status_code == 200
        for change in ({"x-forwarded-host": "pwa.example.test"},
                       {"x-forwarded-host": "admin.example.test, pwa.example.test"},
                       {"x-forwarded-proto": "http"}, {"host": "admin.example.test"},
                       {"x-forwarded-host": ""}):
            assert (await client.get("/api/status", headers={**headers, **change})).status_code == 403
        assert (await client.post("/api/enrollment/options", headers={**headers, "origin": "https://pwa.example.test"},
                                  json=invitation)).status_code == 403
        duplicate = list(headers.items()) + [("x-forwarded-host", "pwa.example.test")]
        assert (await client.get("/api/status", headers=duplicate)).status_code == 403
    direct = create_app(origin=ORIGIN, mailbox=mailbox)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(direct), base_url=ORIGIN) as client:
        assert (await client.get("/api/status", headers=headers)).status_code == 403


@pytest.mark.asyncio
async def test_expired_invalid_and_oversized_requests_are_refused(setup):
    app, mailbox, invitation = setup
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=ORIGIN,
                                headers={"origin": ORIGIN}) as client:
        invalid = {**invitation, "token": "x" * 43}
        assert (await client.post("/api/enrollment/options", json=invalid)).status_code == 409
        assert (await client.post("/api/enrollment/options", content="x" * 100001)).status_code == 413
        assert (await client.post("/api/enrollment/options", json={**invitation, "enable": True})).status_code == 422
        with sqlite3.connect(mailbox.path) as db:
            db.execute("UPDATE invitations SET expires = 0")
        assert (await client.post("/api/enrollment/options", json=invitation)).status_code == 409


def test_page_catalogues_have_identical_keys_and_no_inline_code():
    english = json.loads((STATIC / "en.json").read_text())
    italian = json.loads((STATIC / "it.json").read_text())
    assert english.keys() == italian.keys()
    assert all(isinstance(text, str) and text for text in (*english.values(), *italian.values()))
    assert "<script>" not in (STATIC / "index.html").read_text()
