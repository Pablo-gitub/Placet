"""user_config_unreadable blocks a delivery like missing_user_config does, with the general words."""
import asyncio

import pytest

from tests.test_delivery_block import block, env, subscribe, waiting_session  # noqa: F401
from tests.test_delivery_block import SID


@pytest.mark.asyncio
async def test_the_unreadable_configuration_is_a_block_with_the_general_notification(env):
    app, db, sent = env
    await subscribe(app)
    await waiting_session(app)
    result = await block(app, code="user_config_unreadable", deterministic=True)
    assert result["failed_attempts"] == 1
    assert db.get_delivery_block(SID)["code"] == "user_config_unreadable"
    alerts = [p for p in sent if p["tag"].startswith("delivery-blocked")]
    assert len(alerts) == 1
    assert "configurazione di questa conversazione" not in alerts[0]["body"]       # not the "missing" words: it is there, unreadable
    assert "non riesce" in alerts[0]["body"]
