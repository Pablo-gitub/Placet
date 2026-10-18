#!/usr/bin/env python3
"""Check repository Markdown links and heading anchors without network access."""
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
FENCES = re.compile(r"^\s*(`{3,}|~{3,}).*$", re.MULTILINE)
LINKS = re.compile(r'\[[^\]\n]*\]\((<[^>\n]+>|[^\s)]+)(?:\s+"[^"\n]*")?\)')
HTML_LINKS = re.compile(r'(?:href|src)=["\']([^"\']+)["\']')


def prose(source):
    """Exclude fenced examples: their paths and links may be illustrative."""
    lines, fence = [], None
    for line in source.splitlines():
        match = FENCES.match(line)
        if match:
            marker = match[1]
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = None
            continue
        if fence is None:
            lines.append(line)
    return "\n".join(lines)


def anchors(path):
    found, counts = set(), {}
    for line in prose(path.read_text()).splitlines():
        match = re.match(r"^#{1,6}\s+(.+?)(?:\s+#+)?$", line)
        if not match:
            continue
        heading = match[1].lower()
        heading = re.sub(r"[^\w\s-]", "", heading)
        slug = re.sub(r"\s", "-", heading)
        count = counts.get(slug, 0)
        counts[slug] = count + 1
        found.add(f"{slug}-{count}" if count else slug)
    found.update(re.findall(r'(?:id|name)=["\']([^"\']+)["\']', path.read_text()))
    return found


def main():
    tracked = subprocess.check_output(["git", "ls-files", "-z", "--", "*.md"], cwd=ROOT)
    files = [ROOT / name.decode() for name in tracked.split(b"\0") if name]
    errors, checked = [], 0
    for path in files:
        if not path.is_file():
            continue
        source = prose(path.read_text())
        targets = [m[1].strip("<>") for m in LINKS.finditer(source)] + HTML_LINKS.findall(source)
        for target in targets:
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc:
                continue
            # Resolve local URLs relative to their document, not the shell cwd.
            dest = (path.parent / unquote(parsed.path)).resolve() if parsed.path else path
            checked += 1
            if not dest.exists():
                errors.append(f"{path.relative_to(ROOT)}: missing target {target}")
            elif parsed.fragment and dest.suffix == ".md" and unquote(parsed.fragment) not in anchors(dest):
                errors.append(f"{path.relative_to(ROOT)}: missing anchor {target}")
    for error in errors:
        print(error, file=sys.stderr)
    print(f"Checked {checked} local links across {len(files)} Markdown files; {len(errors)} errors.")
    return bool(errors)


if __name__ == "__main__":
    sys.exit(main())
