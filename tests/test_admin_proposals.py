"""Intent persistence must never turn retries or arbitrary text into commands."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from admin_executor.jobs import ProposalStore, canonical_proposal


@pytest.mark.parametrize("action,parameters", [
    ("shell", {"command": "id"}), ("deploy", {"commit": "main"}),
    ("deploy", {"commit": "a" * 40, "args": "--force"}),
    ("deploy", {"commit": "A" * 40}), ("deploy", {"commit": None}),
    ("restart-backend", {"unit": "other.service"}),
    ("restart-bridge", {"cwd": "/tmp"}),
])
def test_rejects_free_commands_and_parameters(action, parameters):
    with pytest.raises(ValueError):
        canonical_proposal(action, parameters)


def test_restart_and_concurrent_retries_preserve_one_intent(tmp_path):
    path = tmp_path / "admin.sqlite"
    store = ProposalStore(path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: store.propose("phone-1", "deploy", {"commit": "a" * 40}), range(8)))
    assert all(result == results[0] for result in results)
    assert ProposalStore(path).propose("phone-1", "deploy", {"commit": "a" * 40}) == results[0]
    assert results[0]["state"] == "awaiting_confirmation"
    with pytest.raises(ValueError, match="different proposal"):
        store.propose("phone-1", "restart-backend", {})
    assert store.propose("phone-2", "restart-backend", {})["state"] == "awaiting_confirmation"
