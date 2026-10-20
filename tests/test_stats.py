"""Tests for RF-64: Statistics API (GET /api/stats), historical relation type names, and usage peaks."""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.config import AgentSpec, PlacetConfig
from backend.app.database import Database
from backend.app.main import create_app
from tests.helpers import agent_client, human_client


@pytest.fixture
def env(tmp_path):
    db_path = str(tmp_path / "placet.db")
    db = Database(db_path)
    db.init_db()
    cfg = PlacetConfig(
        agents={
            "claude": AgentSpec(tmux_session="agent-claude", token="tok-claude"),
        }
    )
    app = create_app(db=db, config=cfg, bridge_token="tok-bridge")
    return app, db


@pytest.mark.asyncio
async def test_stats_requires_human(env):
    app, db = env
    async with agent_client(app, "tok-claude") as client:
        res = await client.get("/api/stats")
        assert res.status_code == 403

    async with human_client(app) as client:
        res = await client.get("/api/stats")
        assert res.status_code == 200
        data = res.json()
        assert "per_day" in data
        assert "total_requests" in data
        assert "generated_at" in data
        assert data["generated_at"].endswith("Z")


@pytest.mark.asyncio
async def test_stats_validation(env):
    app, db = env
    async with human_client(app) as client:
        # Invalid date format
        res = await client.get("/api/stats?from=not-a-date")
        assert res.status_code == 422

        res = await client.get("/api/stats?to=2026/10/20")
        assert res.status_code == 422

        # from > to
        res = await client.get("/api/stats?from=2026-10-20&to=2026-10-10")
        assert res.status_code == 422

        # Range > 400 days
        res = await client.get("/api/stats?from=2025-01-01&to=2026-10-20")
        assert res.status_code == 422

        # Invalid timezone
        res = await client.get("/api/stats?tz=Mars/Phobos")
        assert res.status_code == 422


@pytest.mark.asyncio
async def test_stats_localized_errors(env):
    app, db = env
    db.set_background_language("it")
    async with human_client(app) as client:
        res = await client.get("/api/stats?tz=Mars/Phobos")
        assert res.status_code == 422
        body = res.json()
        assert body["code"] == "stats.invalid_timezone"
        assert body["detail"] == "Fuso orario non valido: Mars/Phobos"

        res = await client.get("/api/stats?from=invalid")
        assert res.status_code == 422
        body = res.json()
        assert body["code"] == "stats.invalid_date"
        assert "Formato data non valido" in body["detail"]

        res = await client.get("/api/stats?from=2026-10-20&to=2026-10-01")
        assert res.status_code == 422
        body = res.json()
        assert body["code"] == "stats.invalid_range"
        assert body["detail"] == "La data di inizio non può essere successiva alla data di fine."

        res = await client.get("/api/stats?from=2024-01-01&to=2026-10-20")
        assert res.status_code == 422
        body = res.json()
        assert body["code"] == "stats.range_too_large"
        assert "supera il limite massimo di 400 giorni" in body["detail"]


@pytest.mark.asyncio
async def test_stats_aggregation_and_zero_filled_days(env):
    app, db = env
    # Populate projects, relation types, relations, nodes
    now = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)
    t_day1 = now - timedelta(days=2)
    t_day2 = now - timedelta(days=1)
    t_day3 = now

    with db._get_connection() as conn:
        # Create relation type and relation
        conn.execute("INSERT INTO relation_types (id, name, created_at) VALUES (1, 'Architettura', '2026-10-20T10:00:00Z')")
        conn.execute("INSERT INTO nodes (id, name, provider, cwd, created_at) VALUES (1, 'N1', 'claude-code', '/tmp', '2026-10-20T10:00:00Z')")
        conn.execute("INSERT INTO nodes (id, name, provider, cwd, created_at) VALUES (2, 'N2', 'gemini', '/tmp', '2026-10-20T10:00:00Z')")
        conn.execute("INSERT INTO relations (id, from_node, to_node, type_id, approval, created_at) VALUES (1, 1, 2, 1, 'manual', '2026-10-20T10:00:00Z')")

        # Create project
        conn.execute("INSERT INTO projects (id, name, created_at) VALUES (1, 'Placet Project', '2026-10-20T10:00:00Z')")

        # Request 1 (Day 1): manual approval, edited, architettura, project 1, claude -> gemini
        conn.execute(
            "INSERT INTO requests (id, sender_id, recipient_id, question_type, prompt, status, edited_prompt,"
            " created_at, decided_at, approval, relation_id, relation_type_name, project_id)"
            " VALUES (1, 'claude', 'gemini', 'prompt_review', 'prompt1', 'APPROVED', 'edited prompt',"
            " ?, ?, 'manual', 1, 'Architettura', 1)",
            (t_day1.isoformat(), (t_day1 + timedelta(seconds=120)).isoformat()),
        )

        # Request 2 (Day 2): automatic approval, rejected, no project, claude -> codex
        conn.execute(
            "INSERT INTO requests (id, sender_id, recipient_id, question_type, prompt, status,"
            " created_at, decided_at, approval, relation_id, relation_type_name, project_id)"
            " VALUES (2, 'claude', 'codex', 'prompt_review', 'prompt2', 'REJECTED',"
            " ?, ?, 'automatic', 1, 'Architettura', NULL)",
            (t_day2.isoformat(), (t_day2 + timedelta(seconds=60)).isoformat()),
        )

        # Request 3 (Day 3): ad_hoc user message, sender human -> claude, completed
        conn.execute(
            "INSERT INTO requests (id, sender_id, recipient_id, question_type, prompt, status,"
            " created_at, decided_at, approval, project_id)"
            " VALUES (3, 'human', 'claude', 'message', 'user prompt', 'COMPLETED',"
            " ?, NULL, NULL, 1)",
            (t_day3.isoformat(),),
        )

        # Request 4 (Day 3): question to human, sender claude -> human, decided with reply in 300s
        conn.execute(
            "INSERT INTO requests (id, sender_id, recipient_id, question_type, prompt, status,"
            " created_at, decided_at, approval, project_id)"
            " VALUES (4, 'claude', 'human', 'question', 'agent question', 'APPROVED',"
            " ?, ?, 'manual', 1)",
            (t_day3.isoformat(), (t_day3 + timedelta(seconds=300)).isoformat()),
        )

    from_str = (now - timedelta(days=3)).strftime("%Y-%m-%d")
    to_str = now.strftime("%Y-%m-%d")

    async with human_client(app) as client:
        res = await client.get(f"/api/stats?from={from_str}&to={to_str}&tz=UTC")
        assert res.status_code == 200
        data = res.json()

        assert data["total_requests"] == 4
        assert len(data["per_day"]) == 4  # 4 days in range
        # Check day 0 has count 0
        assert data["per_day"][0]["count"] == 0
        # Check day 1, 2, 3 counts
        assert data["per_day"][1]["count"] == 1
        assert data["per_day"][2]["count"] == 1
        assert data["per_day"][3]["count"] == 2
        # Human reply seconds on day 3: 300.0
        assert data["per_day"][3]["mean_reply_seconds"] == 300.0

        # by_relation_type
        assert data["by_relation_type"] == {"Architettura": 2}

        # by_project
        projects = {p["name"]: p["count"] for p in data["by_project"]}
        assert projects["Placet Project"] == 3
        assert projects["Senza progetto"] == 1

        # by_sender_provider and by_recipient_provider
        assert data["by_sender_provider"]["claude-code"] == 3
        assert data["by_sender_provider"]["human"] == 1
        assert data["by_recipient_provider"]["gemini"] == 1
        assert data["by_recipient_provider"]["codex"] == 1
        assert data["by_recipient_provider"]["human"] == 1
        assert data["by_recipient_provider"]["claude-code"] == 1

        # approval
        assert data["approval"]["manual"] == 2
        assert data["approval"]["automatic"] == 1
        assert data["approval"]["ad_hoc"] == 1

        # decisions
        assert data["decisions"]["approved"] == 2  # Request 3 (completed) + Request 4 (approved without edit)
        assert data["decisions"]["edited"] == 1    # Request 1 (approved with edited_prompt)
        assert data["decisions"]["rejected"] == 1  # Request 2

        # user messages
        assert data["user_messages"]["requests"] == 1
        assert data["user_messages"]["replies"] == 1
        assert data["mean_reply_seconds"] == 300.0


@pytest.mark.asyncio
async def test_stats_timezone_boundary(env):
    app, db = env
    # 2026-10-20 23:30:00 UTC is 2026-10-21 01:30:00 in Europe/Rome (CEST UTC+2)
    utc_time = datetime(2026, 10, 20, 23, 30, 0, tzinfo=timezone.utc)
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO requests (id, sender_id, recipient_id, question_type, prompt, status, created_at)"
            " VALUES (10, 'claude', 'gemini', 'prompt_review', 'prompt', 'COMPLETED', ?)",
            (utc_time.isoformat(),),
        )

    async with human_client(app) as client:
        # In UTC, request falls on 2026-10-20
        res_utc = await client.get("/api/stats?from=2026-10-20&to=2026-10-20&tz=UTC")
        assert res_utc.status_code == 200
        assert res_utc.json()["total_requests"] == 1

        # In Europe/Rome, request falls on 2026-10-21
        res_rome_20 = await client.get("/api/stats?from=2026-10-20&to=2026-10-20&tz=Europe/Rome")
        assert res_rome_20.status_code == 200
        assert res_rome_20.json()["total_requests"] == 0

        res_rome_21 = await client.get("/api/stats?from=2026-10-21&to=2026-10-21&tz=Europe/Rome")
        assert res_rome_21.status_code == 200
        assert res_rome_21.json()["total_requests"] == 1
        assert res_rome_21.json()["per_day"][0]["date"] == "2026-10-21"
        assert res_rome_21.json()["per_day"][0]["count"] == 1


@pytest.mark.asyncio
async def test_stats_relation_deletion_survival(env):
    app, db = env
    with db._get_connection() as conn:
        conn.execute("INSERT INTO relation_types (id, name, created_at) VALUES (1, 'Consulenza', '2026-10-20T10:00:00Z')")
        conn.execute("INSERT INTO nodes (id, name, provider, cwd, created_at) VALUES (1, 'N1', 'claude-code', '/tmp', '2026-10-20T10:00:00Z')")
        conn.execute("INSERT INTO nodes (id, name, provider, cwd, created_at) VALUES (2, 'N2', 'gemini', '/tmp', '2026-10-20T10:00:00Z')")
        conn.execute("INSERT INTO relations (id, from_node, to_node, type_id, approval, created_at) VALUES (1, 1, 2, 1, 'manual', '2026-10-20T10:00:00Z')")

    # Creating request over relation records relation_type_name
    with db._get_connection() as conn:
        now = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO requests (id, sender_id, recipient_id, question_type, prompt, status, created_at)"
            " VALUES (101, 'claude', 'gemini', 'prompt_review', 'prompt', 'COMPLETED', ?)",
            (now,),
        )
    db.set_request_relation(101, 1)

    with db._get_connection() as conn:
        row = conn.execute("SELECT relation_type_name FROM requests WHERE id = 101").fetchone()
        assert row["relation_type_name"] == "Consulenza"

    # Now delete the relation and relation type
    db.delete_relation(1)
    db.delete_relation_type(1)

    # Request still retains relation_type_name
    with db._get_connection() as conn:
        row = conn.execute("SELECT relation_type_name FROM requests WHERE id = 101").fetchone()
        assert row["relation_type_name"] == "Consulenza"

    # And /api/stats includes it in by_relation_type
    async with human_client(app) as client:
        res = await client.get("/api/stats?from=2026-10-20&to=2026-10-20&tz=UTC")
        assert res.status_code == 200
        assert res.json()["by_relation_type"] == {"Consulenza": 1}


def test_migration_backfills_relation_type_name(tmp_path):
    db_file = tmp_path / "legacy_rtn.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute("CREATE TABLE schema_migrations (name TEXT PRIMARY KEY)")
    conn.execute(
        "CREATE TABLE requests (id INTEGER PRIMARY KEY, relation_id INTEGER, prompt TEXT, status TEXT, created_at TEXT)"
    )
    conn.execute("CREATE TABLE relation_types (id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("CREATE TABLE relations (id INTEGER PRIMARY KEY, type_id INTEGER)")
    conn.execute("INSERT INTO relation_types (id, name) VALUES (1, 'Supervisione')")
    conn.execute("INSERT INTO relations (id, type_id) VALUES (10, 1)")
    conn.execute(
        "INSERT INTO requests (id, relation_id, prompt, status, created_at) VALUES (1, 10, 'p', 'COMPLETED', '2026-10-20T10:00:00Z')"
    )
    conn.commit()
    conn.close()

    db = Database(str(db_file))
    db.init_db()

    with db._get_connection() as conn:
        info = conn.execute("PRAGMA table_info(requests)").fetchall()
        cols = {r[1] for r in info}
        assert "relation_type_name" in cols

        row = conn.execute("SELECT relation_type_name FROM requests WHERE id = 1").fetchone()
        assert row["relation_type_name"] == "Supervisione"

        migrated = conn.execute("SELECT 1 FROM schema_migrations WHERE name = 'request_relation_type_name_v1'").fetchone()
        assert migrated is not None


@pytest.mark.asyncio
async def test_stats_usage_peaks_and_threshold_days(env):
    app, db = env
    # Configure usage guard: five_hour = 80, seven_day = 90
    db.set_setting("usage_guard_5h", "80")
    db.set_setting("usage_guard_7d", "90")

    # Insert usage samples across 3 days:
    # Day 1: peak 75% (< 80)
    # Day 2: peak 85% (>= 80 -> threshold exceeded)
    # Day 3: peak 92% (>= 80 -> threshold exceeded)
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO usage_samples (provider, observed_at, window, bucket_id, bucket_group, used_percent)"
            " VALUES ('claude-code', '2026-10-18T10:00:00Z', '5h', '5h', NULL, 75.0)"
        )
        conn.execute(
            "INSERT INTO usage_samples (provider, observed_at, window, bucket_id, bucket_group, used_percent)"
            " VALUES ('claude-code', '2026-10-19T10:00:00Z', '5h', '5h', NULL, 85.0)"
        )
        conn.execute(
            "INSERT INTO usage_samples (provider, observed_at, window, bucket_id, bucket_group, used_percent)"
            " VALUES ('claude-code', '2026-10-20T10:00:00Z', '5h', '5h', NULL, 92.0)"
        )
        conn.execute(
            "INSERT INTO usage_samples (provider, observed_at, window, bucket_id, bucket_group, used_percent)"
            " VALUES ('claude-code', '2026-10-20T14:00:00Z', '5h', '5h', NULL, 60.0)"
        )

    async with human_client(app) as client:
        res = await client.get("/api/stats?from=2026-10-18&to=2026-10-20&tz=UTC")
        assert res.status_code == 200
        data = res.json()
        assert "usage" in data
        assert len(data["usage"]) == 1
        u = data["usage"][0]
        assert u["provider"] == "claude-code"
        assert u["window"] == "5h"
        assert u["threshold"] == 80
        assert u["threshold_days"] == 2  # Days 19 and 20 exceeded 80%
        peaks_by_date = {p["date"]: p["peak_used_percent"] for p in u["peaks"]}
        assert peaks_by_date["2026-10-18"] == 75.0
        assert peaks_by_date["2026-10-19"] == 85.0
        assert peaks_by_date["2026-10-20"] == 92.0
