from pathlib import Path
import yaml

from deploy.enable_session_collaboration import enable


def test_registry_allows_every_provider_pair_with_same_provider_approval():
    text = Path('AGENTS.md').read_text()
    data = yaml.safe_load(text.split('---', 2)[1])
    agents = data['agents']
    for source, spec in agents.items():
        assert set(agents) <= set(spec['allowed_targets'])
        assert data['rules'][f'{source} -> {source}']['require_approval'] is True
    assert enable(text) == text


def test_migration_preserves_tokens_and_unrelated_content():
    original = '---\nagents:\n  a:\n    token: "${SECRET_A}"\n    allowed_targets: ["human"]\n  b:\n    token: "${SECRET_B}"\n    allowed_targets: ["a"]\nrules:\n  a -> b:\n    require_approval: true\n---\nDocumentation\n'
    result = enable(original)
    data = yaml.safe_load(result.split('---', 2)[1])
    assert data['agents']['a']['token'] == '${SECRET_A}'
    assert data['agents']['b']['allowed_targets'] == ['a', 'b', 'human']
    assert result.endswith('---\nDocumentation\n')
    assert enable(result) == result
