import tempfile
from pathlib import Path
import unittest
import hashlib
import io
import json
from unittest.mock import patch

from app.writing_tips import writing_tips_context
from app.workflow import Workflow
from app.editor_agents import EditorAgents
from app.store import Store
from scripts.install_paper_writing_skill import install


class WritingTipsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.skill = self.root / 'skills/paper-writing'
        (self.skill / 'references').mkdir(parents=True)
        (self.skill / 'SKILL.md').write_text('Synthetic conditional community guidance.', encoding='utf-8')
        (self.skill / 'references/final-checklist.md').write_text('Synthetic final review checklist.', encoding='utf-8')
        (self.root / 'agents').mkdir()
        (self.root / 'agents/writer.md').write_text('Synthetic role.', encoding='utf-8')

    def tearDown(self):
        self.temp.cleanup()

    def test_role_routing_and_independent_review_checklist(self):
        writing = writing_tips_context(self.root, 'method')
        review = writing_tips_context(self.root, 'method', 'review')
        self.assertEqual(len(writing['sources']), 1)
        self.assertEqual(len(review['sources']), 2)
        self.assertIn('final review checklist', review['text'])
        for role in ('reviewer', 'calibration'):
            self.assertEqual(len(writing_tips_context(self.root, role)['sources']), 2)
        for role in ('translator', 'experience'):
            self.assertEqual(writing_tips_context(self.root, role)['text'], '')

    def test_optional_install_and_character_budget(self):
        self.assertEqual(writing_tips_context(self.root / 'empty', 'method')['sources'], [])
        (self.skill / 'SKILL.md').write_text('x' * 14000)
        context = writing_tips_context(self.root, 'method')
        self.assertEqual(context['sources'][0]['chars_read'], 12000)
        self.assertTrue(context['sources'][0]['truncated'])

    def test_workflow_and_legacy_editor_receive_guidance(self):
        role = {'id': 'method', 'prompt_path': 'agents/writer.md'}
        workflow = object.__new__(Workflow)
        workflow.root = self.root
        self.assertIn('Synthetic conditional', workflow._role_prompt(role))
        editor = object.__new__(EditorAgents)
        editor.root = self.root
        editor.store = Store(self.root)
        editor.revision_plan = editor.library = None
        pid = editor.store.create_project('Synthetic topic')['id']
        prompt = editor._context({'project_id': pid, 'action': 'review', 'message': 'Inspect',
                                  'changed_block_id': None, 'change_id': None}, {}, {}, set(), role, '')
        self.assertIn('Synthetic final review checklist', prompt)
        self.assertIn('Return ONLY the editor JSON', prompt)

    def installer_fixture(self):
        root = self.root / 'install-target'
        (root / 'configs').mkdir(parents=True)
        files = {'SKILL.md': b'---\nname: paper-writing\ndescription: Synthetic fixture\n---\nBody',
                 'references/final-checklist.md': b'Synthetic checklist'}
        manifest = {'repository': 'fixture/repo', 'commit': 'pinned', 'path': 'paper-writing',
                    'files': {k: hashlib.sha256(v).hexdigest() for k, v in files.items()}}
        (root / 'configs/paper_writing_upstream.json').write_text(json.dumps(manifest))
        (root / 'configs/paper_writing_policy.md').write_text('Local scope takes precedence.')
        def download(url, **kwargs):
            return io.BytesIO(files[url.split('/paper-writing/', 1)[1]])
        return root, files, download

    def test_install_is_repeatable_and_preserves_local_edits(self):
        root, files, download = self.installer_fixture()
        with patch('urllib.request.urlopen', side_effect=download):
            destination = install(root)
            first = (destination / 'SKILL.md').read_bytes()
            install(root)
            self.assertEqual((destination / 'SKILL.md').read_bytes(), first)
            (destination / 'SKILL.md').write_text('Author customization')
            with self.assertRaisesRegex(ValueError, 'Local skill edits preserved'):
                install(root)
            self.assertEqual((destination / 'SKILL.md').read_text(), 'Author customization')

    def test_hash_failure_leaves_previous_installation_untouched(self):
        root, files, download = self.installer_fixture()
        with patch('urllib.request.urlopen', side_effect=download):
            destination = install(root)
            first = (destination / 'SKILL.md').read_bytes()
            files['references/final-checklist.md'] = b'Unexpected upstream content'
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                install(root)
            self.assertEqual((destination / 'SKILL.md').read_bytes(), first)


if __name__ == '__main__':
    unittest.main()
