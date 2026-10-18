"""Enable every registered agent pair, preserving approval, with a backup.
Run with the deployment Python: ... enable_session_collaboration.py AGENTS.md
"""
import json
from pathlib import Path
import re
import shutil
import sys
from datetime import datetime

import yaml


def enable(text):
    match = re.match(r'\A---\s*\n(.*?)\n---', text, re.S)
    if not match:
        raise ValueError('Missing YAML frontmatter')
    header = match.group(1)
    data = yaml.safe_load(header)
    agents = list(data['agents'])
    targets = agents + ['human']
    for agent in agents:
        pattern = r'(?m)(^  ' + re.escape(agent) + r':\n)(.*?)(?=^  \w[^\n]*:\n|^rules:|\Z)'
        def replace(m):
            block = m.group(2)
            line = '    allowed_targets: ' + json.dumps(targets)
            if re.search(r'(?m)^    allowed_targets:.*$', block):
                block = re.sub(r'(?m)^    allowed_targets:.*$', lambda _: line, block)
            else:
                block = line + '\n' + block
            return m.group(1) + block
        header, count = re.subn(pattern, replace, header, flags=re.S)
        if count != 1:
            raise ValueError('Unsupported agent block: ' + agent)
    rules = data.get('rules', {})
    additions = []
    for agent in agents:
        key = f'{agent} -> {agent}'
        if key not in rules:
            additions.append(f'  {key}:\n    require_approval: true')
        elif not rules[key].get('require_approval', True):
            raise ValueError('Same-provider route has automatic approval; review explicitly: ' + key)
    if additions:
        if not re.search(r'(?m)^rules:', header):
            header += '\nrules:'
        header = header.rstrip() + '\n' + '\n'.join(additions) + '\n'
    return text[:match.start(1)] + header + text[match.end(1):]


if __name__ == '__main__':
    path = Path(sys.argv[1])
    original = path.read_text()
    updated = enable(original)
    if updated != original:
        backup = Path(str(path) + '.bak-collaboration-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
        shutil.copy2(path, backup)
        path.write_text(updated)
        print('Updated routing; backup:', backup)
    else:
        print('Routing already enabled')
