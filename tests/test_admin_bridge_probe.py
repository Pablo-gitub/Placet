"""The operator-only worker probe never runs from a test."""
import ast
from pathlib import Path

import pytest


def test_worker_probe_preview_is_read_only(monkeypatch, capsys):
    from admin_executor import bridge_probe
    monkeypatch.setattr(bridge_probe.subprocess, 'run', lambda *a, **k: pytest.fail('Probe executed'))
    bridge_probe.main([])
    assert 'preview' in capsys.readouterr().out


def test_probe_apply_refuses_checkout_and_missing_pin(monkeypatch):
    from admin_executor import bridge_probe
    monkeypatch.setattr(bridge_probe.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(bridge_probe.subprocess, 'run', lambda *a, **k: pytest.fail('Probe executed'))
    with pytest.raises(SystemExit):
        bridge_probe.main(['--apply'])


def test_probe_contains_only_disposable_targets_and_parseable_inline_program():
    from admin_executor import bridge_probe
    ast.parse(bridge_probe.HELPER)
    ast.parse(bridge_probe.RUNNER)
    source = Path(bridge_probe.__file__).read_text()
    assert 'placet-bridge.service' not in source
    assert 'placet-backend.service' not in source
    assert 'NoNewPrivileges=yes' in source
    assert 'CapabilityBoundingSet=' in source
    assert 'InvocationID' in bridge_probe.RUNNER
    assert 'StatusText' in bridge_probe.RUNNER


def test_probe_cleanup_tolerates_collected_runner_and_checks_inactive_before_removal(monkeypatch,tmp_path):
    from types import SimpleNamespace
    from admin_executor import bridge_probe as p
    stage=tmp_path/'stage';stage.mkdir();(stage/'helper.py').write_text('fixed')
    unit=tmp_path/'worker.service';unit.write_text('fixed')
    monkeypatch.setattr(p,'STAGE',stage);monkeypatch.setattr(p,'UNIT',unit)
    calls=[]
    def run(argv,**options):
        calls.append(argv)
        return SimpleNamespace(returncode=5 if 'stop' in argv else 0,stdout='inactive\n')
    monkeypatch.setattr(p.subprocess,'run',run)
    p.cleanup()
    assert not stage.exists() and not unit.exists()
    assert all('placet-probe.service' not in argv for argv in calls)


def test_probe_uses_exact_worker_barrier_and_consistent_timeouts():
    from admin_executor import bridge_probe,bridge_restart
    module=ast.parse(Path(bridge_restart.__file__).read_text())
    probe=ast.parse(bridge_probe.HELPER)
    function=lambda tree:next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='notification_barrier')
    assert ast.dump(function(module))==ast.dump(function(probe))
    source=Path(bridge_probe.__file__).read_text()
    assert 'TimeoutStartSec=60' in source
    assert 'RuntimeMaxSec=100' in source
    assert 'timeout=75' in bridge_probe.RUNNER
    assert 'timeout=120' in source
