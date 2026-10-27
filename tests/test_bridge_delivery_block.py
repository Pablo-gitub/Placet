"""The bridge stops hammering a session its channel can not deliver to: one confirming re-read, then a block."""
import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.config import AgentSpec, PlacetConfig
from backend.app.database import Database
from bridge.bridge_daemon import BridgeDaemon
from bridge.channels import AntigravityChannel, ChannelError, ChannelUncertain
from bridge.stores import LocalStore

CONFIG = PlacetConfig(agents={
    "gemini": AgentSpec(tmux_session="agent-gemini", token="tok-gemini", allowed_targets=["human"]),
})
SID = "ag1"
SECRET = "SECRET-MODEL-CONFIGURATION"


class FakeChannel:
    """Stands in for AntigravityChannel: each send raises what `plan` says (an exception or None for success)."""

    def __init__(self, plan):
        self.plan, self.calls = list(plan), 0

    def send(self, session_id, text, now=False):
        self.calls += 1
        outcome = self.plan.pop(0) if self.plan else None
        if outcome is not None:
            raise outcome


def missing_config():
    return ChannelError("No previous user turn to copy the model configuration from.", code="missing_user_config",
                        phase="pre_dispatch")


def unavailable():
    return ChannelError("Antigravity is not running.", code="conversation_unavailable", phase="pre_dispatch")


@pytest.fixture
def env():
    db = Database(os.path.join(tempfile.mkdtemp(), "placet.db"))
    db.init_db()
    db.upsert_session(SID, "gemini", provider="antigravity")
    return db


def daemon(db, channel):
    return BridgeDaemon(store=LocalStore(db, CONFIG), tmux=None, channels={"antigravity": channel}, claude_waker=None)


def tick(d, db):
    return d.process_session_mail([{"id": s.id, "agent_id": s.agent_id, "provider": s.provider,
                                    **({"probing": True} if s.id in db.delivery_block_states()[1] else {})}
                                   for s in db.bridge_sessions_with_mail()])


def make_due(db):
    with db._get_connection() as conn:
        conn.execute("UPDATE delivery_blocks SET next_retry_at = ?", ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),))


def write(db, text="ciao"):
    from backend.app.models import RequestCreate, RequestStatus
    req = db.create_request(RequestCreate(sender_id="human", recipient_id="gemini", prompt=text, question_type="message",
                                          recipient_session_id=SID))
    db.update_request_decision(req.id, RequestStatus.APPROVED, actor="human:test")
    return req.id


# --- the channel says what failed -----------------------------------------------------------------


def test_the_antigravity_channel_names_the_failure_with_a_closed_code_and_phase():
    with pytest.raises(ChannelError) as missing:
        AntigravityChannel._config({"steps": []})
    assert (missing.value.code, missing.value.phase) == ("missing_user_config", "pre_dispatch")
    channel = AntigravityChannel(discover=lambda: [], ports_of=lambda pid: [])
    with pytest.raises(ChannelError) as gone:
        channel.send("nope", "x")
    assert (gone.value.code, gone.value.phase) == ("conversation_unavailable", "pre_dispatch")
    assert ChannelError("plain").code is None and ChannelUncertain("late").code is None       # untyped stays untyped


# --- the loop --------------------------------------------------------------------------------------


def test_one_confirming_re_read_and_then_the_session_is_blocked(env):
    db = env
    rid = write(db)
    channel = FakeChannel([missing_config(), missing_config()])
    d = daemon(db, channel)
    assert tick(d, db) == 0
    assert channel.calls == 2                                                                  # the try and one confirmation
    block = db.get_delivery_block(SID)
    assert (block["code"], block["phase"], block["failed_attempts"]) == ("missing_user_config", "pre_dispatch", 2)
    request = db.get_request(rid)
    assert request.status.value == "APPROVED" and request.delivery_attempts == 0              # nothing was delivered
    for _ in range(5):                                                                         # five more ticks: no polling
        assert tick(d, db) == 0
    assert channel.calls == 2


def test_the_confirming_re_read_that_succeeds_just_delivers(env):
    db = env
    rid = write(db)
    channel = FakeChannel([missing_config(), None])
    assert tick(daemon(db, channel), db) == 1
    assert db.get_delivery_block(SID) is None and db.get_request(rid).status.value == "EXECUTING"


def test_a_block_survives_a_new_bridge_and_a_new_message_does_not_clear_it(env):
    db = env
    write(db)
    channel = FakeChannel([missing_config(), missing_config()])
    tick(daemon(db, channel), db)
    write(db, "un altro")
    restarted = daemon(db, channel)                                                            # the bridge restarted
    for _ in range(3):
        tick(restarted, db)
    assert channel.calls == 2 and db.get_delivery_block(SID) is not None


def test_the_users_retry_probes_once_and_a_second_failure_blocks_again(env):
    db = env
    write(db)
    channel = FakeChannel([missing_config(), missing_config(), missing_config()])
    d = daemon(db, channel)
    tick(d, db)
    assert db.retry_delivery_block(SID)
    tick(d, db)
    assert channel.calls == 3                                                                  # one probe: no new re-read
    assert db.get_delivery_block(SID)["failed_attempts"] == 3
    tick(d, db)
    assert channel.calls == 3


def test_a_probe_that_succeeds_ends_the_block(env):
    db = env
    rid = write(db)
    channel = FakeChannel([missing_config(), missing_config(), None])
    d = daemon(db, channel)
    tick(d, db)
    make_due(db)
    assert tick(d, db) == 1
    assert db.get_delivery_block(SID) is None and db.get_request(rid).status.value == "EXECUTING"


def test_a_transient_failure_backs_off_instead_of_retrying_every_tick(env):
    db = env
    write(db)
    channel = FakeChannel([unavailable(), unavailable(), None])
    d = daemon(db, channel)
    tick(d, db)
    assert channel.calls == 1                                                                   # transient: no confirming re-read
    block = db.get_delivery_block(SID)
    assert block["code"] == "conversation_unavailable" and block["deterministic"] == 0
    for _ in range(4):
        tick(d, db)
    assert channel.calls == 1                                                                   # waiting out the backoff
    make_due(db)
    assert tick(d, db) == 0 and channel.calls == 2 and db.get_delivery_block(SID)["level"] == 2
    make_due(db)
    assert tick(d, db) == 1 and db.get_delivery_block(SID) is None


def test_a_failure_after_a_possible_dispatch_stays_uncertain_and_is_never_retried(env):
    db = env
    rid = write(db)
    channel = FakeChannel([ChannelUncertain("Antigravity did not answer")])
    d = daemon(db, channel)
    tick(d, db)
    tick(d, db)
    assert channel.calls == 1 and db.get_delivery_block(SID) is None
    assert db.get_request(rid).delivery == "uncertain"


def test_an_untyped_channel_error_behaves_as_before(env):
    db = env
    rid = write(db)
    channel = FakeChannel([ChannelError("down"), ChannelError("down"), None])
    d = daemon(db, channel)
    assert tick(d, db) == 0 and db.get_delivery_block(SID) is None
    assert db.get_request(rid).status.value == "APPROVED"
    assert tick(d, db) == 0 and tick(d, db) == 1 and channel.calls == 3                       # retried on every tick, as today


def test_no_configuration_content_reaches_the_log_or_the_backend(env, caplog):
    db = env
    write(db)
    error = ChannelError(f"No previous user turn to copy the model configuration from. {SECRET}", code="missing_user_config",
                         phase="pre_dispatch")
    caplog.set_level(logging.DEBUG)
    tick(daemon(db, FakeChannel([error, error])), db)
    assert SECRET not in caplog.text
    assert SECRET not in str(db.get_delivery_block(SID))
