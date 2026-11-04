"""Bounded optional community guidance, separate from author-accepted rules."""
import hashlib
from pathlib import Path


ROLES = {'introduction', 'method', 'related_work', 'experiments', 'figures',
         'innovation_teacher', 'global_teacher', 'reviewer', 'calibration'}


def writing_tips_context(root, role_id, action=''):
    if role_id not in ROLES:
        return {'text': '', 'sources': []}
    folder = Path(root).resolve() / 'skills/paper-writing'
    if not (folder / 'SKILL.md').is_file():
        return {'text': '', 'sources': []}
    names = ['SKILL.md']
    if role_id in {'reviewer', 'calibration'} or action == 'review':
        names.append('references/final-checklist.md')
    chunks, sources = [], []
    remaining = 12000
    for name in names:
        path = folder / name
        if not path.resolve().is_relative_to(folder.resolve()):
            raise ValueError('Writing skill reference escapes its directory')
        if path.stat().st_size > 100_000:
            raise ValueError('Writing skill reference is unexpectedly large')
        raw = path.read_bytes()
        text = raw.decode('utf-8-sig')
        shown = text[:remaining]
        remaining -= len(shown)
        sha = hashlib.sha256(raw).hexdigest()
        sid = 'skill:paper-writing/' + name
        sources.append(dict(id=sid, title='MLNLP Paper Writing Tips', kind='supplemental_skill',
                            sha256=sha, verification='local_snapshot_sha256', chars_total=len(text),
                            chars_read=len(shown), included_start=0, included_end=len(shown),
                            truncated=len(shown) < len(text), status='full' if shown == text else 'excerpt',
                            delivery='prepared', read_semantics='prompt inclusion, not proof of comprehension'))
        chunks.append(f'COMMUNITY WRITING REFERENCE (conditional, not author rules)\nSOURCE {sid} | SHA256 {sha}\n' + shown)
    return {'text': '\n\n'.join(chunks), 'sources': sources}
