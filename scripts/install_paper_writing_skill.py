"""Fetch the pinned MLNLP writing skill without executing upstream code."""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import urllib.request


def install(root):
    root = Path(root).resolve()
    manifest = json.loads((root / 'configs/paper_writing_upstream.json').read_text(encoding='utf-8'))
    policy = (root / 'configs/paper_writing_policy.md').read_text(encoding='utf-8')
    destination = root / 'skills/paper-writing'
    payloads = {}
    for name, expected in manifest['files'].items():
        if name not in {'SKILL.md', 'references/writing-rules.md', 'references/final-checklist.md', 'references/resources.md'}:
            raise ValueError('Unexpected skill file')
        url = f"https://raw.githubusercontent.com/{manifest['repository']}/{manifest['commit']}/{manifest['path']}/{name}"
        with urllib.request.urlopen(url, timeout=30) as response:
            raw = response.read(100_001)
        if len(raw) > 100_000 or hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError('Upstream file hash mismatch: ' + name)
        raw.decode('utf-8')
        payloads[name] = raw
    # Verify every download before replacing anything. Local customizations are
    # preserved unless they match the managed installation's previous hashes.
    record_path = destination / '.installation.json'
    previous = json.loads(record_path.read_text(encoding='utf-8')) if record_path.exists() else {}
    for name, raw in payloads.items():
        path = destination / name
        if path.exists():
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual not in {manifest['files'][name], previous.get('installed_sha256', {}).get(name)}:
                raise ValueError('Local skill edits preserved; reconcile before reinstalling: ' + name)
    text = payloads['SKILL.md'].decode('utf-8')
    front, body = text.split('\n---\n', 1)
    payloads['SKILL.md'] = (front + '\n---\n\n' + policy + '\n\n' + body.lstrip()).encode('utf-8')
    destination.mkdir(parents=True, exist_ok=True)
    for name, raw in payloads.items():
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temp:
            temp.write(raw)
            pending = Path(temp.name)
        pending.replace(path)
    record_path.write_text(json.dumps({**manifest, 'installed_sha256': {
        name: hashlib.sha256(raw).hexdigest() for name, raw in payloads.items()
    }}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    print('Installed:', install(args.root))
