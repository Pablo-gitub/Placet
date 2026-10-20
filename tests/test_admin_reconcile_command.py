"""The displayed human command must never import from its working directory."""
import json
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parent.parent
PROTECTED_PREFIX = '/usr/local/lib/placet-admin'
PROTECTED_PYTHON = '/usr/local/lib/placet-admin-venv/bin/python'


def displayed_prefix():
    source = (ROOT / 'frontend/src/Commands.jsx').read_text()
    return source.split('const TERMINAL = "', 1)[1].split('";', 1)[0]


def documented_command(outcome):
    lines = (ROOT / 'docs/installation.md').read_text().splitlines()
    return next(line for line in lines if line.startswith('sudo /usr/bin/env -i ')
                and ' reconcile --id <job_id> --outcome ' + outcome in line)


def test_displayed_and_documented_commands_exclude_the_working_directory():
    prefix = displayed_prefix()
    arguments = shlex.split(prefix)
    assert '-P' in arguments
    assert '-s' in arguments
    assert arguments[:3] == ['sudo', '/usr/bin/env', '-i']
    assert [item for item in arguments if item.startswith('PYTHONPATH=')] == ['PYTHONPATH=' + PROTECTED_PREFIX]
    assert PROTECTED_PYTHON in arguments
    for outcome in ('succeeded', 'failed'):
        assert documented_command(outcome) == prefix + ' reconcile --id <job_id> --outcome ' + outcome


@pytest.mark.parametrize('outcome', ['succeeded', 'failed'])
@pytest.mark.parametrize('working_directory', ['checkout', 'arbitrary', 'root'])
def test_documented_command_imports_only_the_protected_package(tmp_path, outcome, working_directory):
    protected = tmp_path / 'protected'
    package = protected / 'admin_executor'
    package.mkdir(parents=True)
    (package / '__init__.py').write_text('')
    (package / 'terminal.py').write_text(
        'import json, sys\nprint(json.dumps({"origin": "protected", "args": sys.argv[1:]}))\n')
    checkout = tmp_path / 'checkout'
    malicious = checkout / 'admin_executor'
    malicious.mkdir(parents=True)
    marker = tmp_path / 'checkout-imported'
    (malicious / '__init__.py').write_text(
        'from pathlib import Path\nPath(' + repr(str(marker)) + ').write_text("unexpected import")\n')
    (malicious / 'terminal.py').write_text('print("checkout terminal must not run")\n')
    arbitrary = tmp_path / 'elsewhere'
    arbitrary.mkdir()
    cwd = {'checkout': checkout, 'arbitrary': arbitrary, 'root': Path('/')}[working_directory]
    arguments = shlex.split(documented_command(outcome))
    assert arguments.pop(0) == 'sudo'  # Tests never invoke sudo or reconciliation.
    arguments = [sys.executable if a == PROTECTED_PYTHON else
                 'PYTHONPATH=' + str(protected) if a == 'PYTHONPATH=' + PROTECTED_PREFIX else
                 'a' * 32 if a == '<job_id>' else a for a in arguments]
    result = subprocess.run(arguments, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True,
                            text=True, timeout=10, env={'PYTHONPATH': str(checkout)})
    assert not marker.exists(), 'The documented command imported an agent-writable checkout'
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'origin': 'protected', 'args': [
        'reconcile', '--id', 'a' * 32, '--outcome', outcome]}
