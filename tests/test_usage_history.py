"""Tests for RF-64b: usage history, sample recording conditions, retention, and migration."""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.config import PlacetConfig
from backend.app.database import Database
from backend.app.main import create_app
from tests.helpers import agent_client


@pytest.fixture
def env(tmp_path):
    db_path = str(tmp_path / "placet.db")
    db = Database(db_path)
    db.init_db()
    app = create_app(db=db, config=PlacetConfig(), bridge_token="tok-bridge")
    return app, db


def get_samples(db, provider="codex"):
    with db._get_connection() as conn:
        return conn.execute(
            "SELECT provider, observed_at, window, bucket_id, bucket_group, used_percent, resets_at"
            " FROM usage_samples WHERE provider = ? ORDER BY id ASC",
            (provider,),
        ).fetchall()


@pytest.mark.asyncio
async def test_usage_history_recording_conditions(env):
    app, db = env
    base_time = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)

    def report(observed_at, used5, used7, resets5=None, resets7=None):
        return {
            "providers": [
                {
                    "provider": "codex",
                    "plan": "plus",
                    "observed_at": observed_at.isoformat(),
                    "buckets": [
                        {
                            "id": "5h",
                            "window": "5h",
                            "used_percent": used5,
                            "resets_at": resets5 or "2026-10-20T17:00:00Z",
                        },
                        {
                            "id": "7d",
                            "window": "7d",
                            "used_percent": used7,
                            "resets_at": resets7 or "2026-10-27T12:00:00Z",
                        },
                    ],
                }
            ]
        }

    async with agent_client(app, "tok-bridge") as bridge:
        # 1. First reading is recorded
        r1 = report(base_time, 20.0, 10.0)
        res = await bridge.post("/api/bridge/usage", json=r1)
        assert res.status_code == 200
        samples = get_samples(db, "codex")
        assert len(samples) == 2
        assert samples[0]["bucket_id"] == "5h" and samples[0]["used_percent"] == 20.0
        assert samples[1]["bucket_id"] == "7d" and samples[1]["used_percent"] == 10.0

        # 2. Identical reading 2 minutes later is NOT recorded
        t2 = base_time + timedelta(minutes=2)
        r2 = report(t2, 20.0, 10.0)
        res = await bridge.post("/api/bridge/usage", json=r2)
        assert res.status_code == 200
        assert len(get_samples(db, "codex")) == 2

        # 2b. Minor change (< 1 point) 4 minutes later is NOT recorded
        t2b = base_time + timedelta(minutes=4)
        r2b = report(t2b, 20.5, 10.4)
        res = await bridge.post("/api/bridge/usage", json=r2b)
        assert res.status_code == 200
        assert len(get_samples(db, "codex")) == 2

        # 3. +1 point change 5 minutes later is recorded
        t3 = base_time + timedelta(minutes=5)
        r3 = report(t3, 21.0, 10.0)  # +1.0 point on 5h bucket
        res = await bridge.post("/api/bridge/usage", json=r3)
        assert res.status_code == 200
        samples = get_samples(db, "codex")
        assert len(samples) == 4
        assert samples[2]["used_percent"] == 21.0

        # 4. 11 minutes later with NO change is recorded (>= 10 minutes threshold)
        t4 = t3 + timedelta(minutes=11)
        r4 = report(t4, 21.0, 10.0)
        res = await bridge.post("/api/bridge/usage", json=r4)
        assert res.status_code == 200
        samples = get_samples(db, "codex")
        assert len(samples) == 6
        assert samples[4]["observed_at"] == t4.strftime("%Y-%m-%dT%H:%M:%SZ")

        # 5. Reset change (2 minutes after t4) is recorded
        t5 = t4 + timedelta(minutes=2)
        r5 = report(t5, 21.0, 10.0, resets5="2026-10-20T18:00:00Z")
        res = await bridge.post("/api/bridge/usage", json=r5)
        assert res.status_code == 200
        samples = get_samples(db, "codex")
        assert len(samples) == 8
        assert samples[6]["resets_at"] == "2026-10-20T18:00:00Z"


@pytest.mark.asyncio
async def test_usage_history_retention_purges_old_samples(env):
    app, db = env
    now = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)
    old_time = (now - timedelta(days=405)).isoformat()
    recent_time = (now - timedelta(days=10)).isoformat()

    # Pre-populate with old samples and recent samples
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO usage_samples (provider, observed_at, window, bucket_id, bucket_group, used_percent, resets_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("codex", old_time, "5h", "5h", None, 50.0, None),
        )
        conn.execute(
            "INSERT INTO usage_samples (provider, observed_at, window, bucket_id, bucket_group, used_percent, resets_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("codex", recent_time, "5h", "5h", None, 60.0, None),
        )

    # Now post a reading at `now`
    report = {
        "providers": [
            {
                "provider": "codex",
                "observed_at": now.isoformat(),
                "buckets": [{"id": "5h", "window": "5h", "used_percent": 70.0}],
            }
        ]
    }
    async with agent_client(app, "tok-bridge") as bridge:
        res = await bridge.post("/api/bridge/usage", json=report)
        assert res.status_code == 200

    # The sample older than 400 days must be purged
    with db._get_connection() as conn:
        rows = conn.execute("SELECT observed_at FROM usage_samples WHERE provider = 'codex' ORDER BY observed_at ASC").fetchall()
        observed = [r["observed_at"] for r in rows]
        assert old_time not in observed
        assert recent_time in observed
        assert now.strftime("%Y-%m-%dT%H:%M:%SZ") in observed


def test_migration_applies_on_existing_database(tmp_path):
    db_file = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db_file))
    # Create minimal legacy tables without usage_samples and without usage_samples migration
    conn.execute("CREATE TABLE schema_migrations (name TEXT PRIMARY KEY)")
    conn.execute("INSERT INTO schema_migrations (name) VALUES ('claude_desktop_end_is_suspension_v1')")
    conn.commit()
    conn.close()

    # Initializing Database should run the new migration
    db = Database(str(db_file))
    db.init_db()

    with db._get_connection() as conn:
        # Check table exists
        info = conn.execute("PRAGMA table_info(usage_samples)").fetchall()
        cols = {r[1] for r in info}
        assert {"id", "provider", "observed_at", "window", "bucket_id", "bucket_group", "used_percent", "resets_at"}.issubset(cols)

        # Check index exists
        indices = conn.execute("PRAGMA index_list(usage_samples)").fetchall()
        idx_names = [i[1] for i in indices]
        assert any("provider_observed" in name for name in idx_names)

        # Check schema_migrations contains the migration
        migrated = conn.execute("SELECT 1 FROM schema_migrations WHERE name = 'usage_samples_v1'").fetchone()
        assert migrated is not None


@pytest.mark.asyncio
async def test_usage_history_mixed_timestamp_formats_normalized(env):
    app, db = env
    # First report with +00:00 format
    t1 = "2026-10-20T12:00:00+00:00"
    buckets = [
        {"id": "5h", "window": "5h", "used_percent": 50.0, "resets_at": "2026-10-20T17:00:00+00:00"}
    ]
    recorded = db.record_usage_samples("codex", buckets, observed_at=t1)
    assert recorded is True

    # Verify normalized in SQLite table to canonical format
    with db._get_connection() as conn:
        row = conn.execute("SELECT observed_at, resets_at FROM usage_samples WHERE provider = 'codex'").fetchone()
        assert row["observed_at"] == "2026-10-20T12:00:00Z"
        assert row["resets_at"] == "2026-10-20T17:00:00Z"

    # Second report 5 minutes later with Z format and same usage: must NOT be recorded (elapsed 300s < 600s)
    t2 = "2026-10-20T12:05:00Z"
    recorded2 = db.record_usage_samples("codex", buckets, observed_at=t2)
    assert recorded2 is False

    # Third report 11 minutes after t1 with +00:00 format and microseconds: must be recorded (elapsed 660s >= 600s)
    t3 = "2026-10-20T12:11:00.123456+00:00"
    recorded3 = db.record_usage_samples("codex", buckets, observed_at=t3)
    assert recorded3 is True
    with db._get_connection() as conn:
        rows = conn.execute("SELECT observed_at FROM usage_samples WHERE provider = 'codex' ORDER BY observed_at ASC").fetchall()
        assert len(rows) == 2
        assert rows[0]["observed_at"] == "2026-10-20T12:00:00Z"
        assert rows[1]["observed_at"] == "2026-10-20T12:11:00Z"

