import tempfile
import unittest
from pathlib import Path

from agent_engine import add_user_request, advance, approve_or_reject, new_state
from local_tools import LocalTools, ToolError


def call(name, args):
    return {'function': {'name': name, 'arguments': args}}


class SingleToolClient:
    def __init__(self, name, args):
        self.name, self.args = name, args

    def chat(self, *_):
        return {'role': 'assistant', 'content': '', 'tool_calls': [call(self.name, self.args)]}


class FolderTrashTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'workspace'
        self.tools = LocalTools(self.root)
        self.staging = Path(self.tmp.name) / 'fake_trash'
        self.staging.mkdir()
        self.moved = []

    def tearDown(self):
        self.tmp.cleanup()

    def fake_trash(self, path):
        path = Path(path)
        self.moved.append(path.name)
        path.rename(self.staging / path.name)

    def test_empty_folder_preview_and_trash(self):
        (self.root / 'Reports2').mkdir()
        prev = self.tools.preview_delete(['Reports2'])
        self.assertEqual(prev['items'][0]['status'], 'eligible')
        self.assertEqual(prev['items'][0]['kind'], 'folder')
        self.assertEqual(prev['items'][0]['files_count'], 0)
        self.assertEqual(prev['items'][0]['folders_count'], 0)
        result = self.tools.trash_selected(['Reports2'], ['Reports2'], prev['identities'], trash_fn=self.fake_trash)
        self.assertEqual(result['status'], 'moved_to_trash')
        self.assertEqual(result['trashed_folders'], 1)
        self.assertEqual(result['trashed_files'], 0)
        self.assertFalse((self.root / 'Reports2').exists())
        self.assertTrue((self.staging / 'Reports2').is_dir())

    def test_nested_folder_informed_preview_and_trash_everything(self):
        (self.root / 'Reports2' / 'nested').mkdir(parents=True)
        (self.root / 'Reports2' / 'first.txt').write_text('abc')
        (self.root / 'Reports2' / 'nested' / 'second.txt').write_text('hi')
        (self.root / 'Reports2' / '.DS_Store').write_text('hidden')
        prev = self.tools.preview_delete(['Reports2'])
        item = prev['items'][0]
        self.assertEqual(item['files_count'], 3)
        self.assertEqual(item['folders_count'], 1)
        self.assertEqual(item['size_bytes'], 3+2+6)
        result = self.tools.trash_selected(['Reports2'], ['Reports2'], prev['identities'], trash_fn=self.fake_trash)
        self.assertEqual(result['status'], 'moved_to_trash')
        self.assertTrue((self.staging / 'Reports2' / 'nested' / 'second.txt').is_file())

    def test_rejected_folder_has_no_change(self):
        (self.root / 'Reports2').mkdir()
        state = new_state()
        add_user_request(state, 'پوشه Reports2 رو پاک کن')
        advance(state, self.root, SingleToolClient('delete_files', {'paths': ['Reports2']}))
        self.assertEqual(state['pending']['preview']['items'][0]['kind'], 'folder')
        result = approve_or_reject(state, self.root, approved=False, selected_paths=['Reports2'], delete_mode='trash')
        self.assertEqual(result['status'], 'rejected_by_user')
        self.assertTrue((self.root / 'Reports2').exists())
        self.assertFalse(state['events'][0]['result']['actual_change'])

    def test_dry_run_folder_no_effect(self):
        (self.root / 'Reports2').mkdir()
        result = self.tools.simulate_delete(['Reports2'], ['Reports2'])
        self.assertEqual(result['status'], 'dry_run_only')
        self.assertEqual(result['items'][0]['status'], 'simulated_trash_folder')
        self.assertTrue((self.root / 'Reports2').is_dir())

    def test_deselected_folder_kept_and_selected_file_trashed(self):
        (self.root / 'Reports2').mkdir()
        (self.root / 'one.txt').write_text('ok')
        prev = self.tools.preview_delete(['Reports2', 'one.txt'])
        result = self.tools.trash_selected(['Reports2', 'one.txt'], ['one.txt'], prev['identities'], trash_fn=self.fake_trash)
        self.assertEqual(result['trashed_folders'], 0)
        self.assertEqual(result['trashed_files'], 1)
        self.assertTrue((self.root / 'Reports2').exists())
        self.assertTrue((self.staging / 'one.txt').is_file())

    def test_changed_descendant_blocks_whole_folder(self):
        (self.root / 'Reports2').mkdir()
        (self.root / 'Reports2' / 'one.txt').write_text('old')
        prev = self.tools.preview_delete(['Reports2'])
        (self.root / 'Reports2' / 'one.txt').write_text('new content')
        with self.assertRaises(ToolError):
            self.tools.trash_selected(['Reports2'], ['Reports2'], prev['identities'], trash_fn=self.fake_trash)
        self.assertEqual(self.moved, [])
        self.assertTrue((self.root / 'Reports2' / 'one.txt').exists())

    def test_new_nested_file_blocks_after_preview(self):
        (self.root / 'Reports2').mkdir()
        prev = self.tools.preview_delete(['Reports2'])
        (self.root / 'Reports2' / 'unexpected.txt').write_text('new')
        with self.assertRaises(ToolError):
            self.tools.trash_selected(['Reports2'], ['Reports2'], prev['identities'], trash_fn=self.fake_trash)
        self.assertEqual(self.moved, [])

    def test_symlink_in_folder_disables_trash(self):
        (self.root / 'Reports2').mkdir()
        outside = Path(self.tmp.name) / 'outside.txt'
        outside.write_text('outside')
        (self.root / 'Reports2' / 'link.txt').symlink_to(outside)
        prev = self.tools.preview_delete(['Reports2'])
        self.assertEqual(prev['items'][0]['status'], 'blocked')
        with self.assertRaises(ToolError):
            self.tools.trash_selected(['Reports2'], ['Reports2'], prev['identities'], trash_fn=self.fake_trash)
        self.assertTrue(outside.exists())
        self.assertEqual(self.moved, [])

    def test_root_outside_and_parent_child_proposals_blocked(self):
        (self.root / 'Reports2').mkdir()
        for path in ['.', '..', '/tmp', '../outside']:
            preview = self.tools.preview_delete([path])
            self.assertEqual(preview['items'][0]['status'], 'blocked')
        with self.assertRaises(ToolError):
            self.tools.preview_delete(['Reports2', 'Reports2/one.txt'])
        with self.assertRaises(ToolError):
            self.tools.preview_delete(['Reports2', 'Reports2\\one.txt'])
        with self.assertRaises(ToolError):
            self.tools.preview_delete(['Reports2', './Reports2'])

    def test_large_folder_is_blocked(self):
        self.tools.MAX_TREE_ENTRIES = 2
        (self.root / 'Reports2').mkdir()
        (self.root / 'Reports2' / 'one.txt').write_text('one')
        (self.root / 'Reports2' / 'two.txt').write_text('two')
        prev = self.tools.preview_delete(['Reports2'])
        self.assertEqual(prev['items'][0]['status'], 'blocked')

    def test_unapproved_invalid_folder_preflight_prevents_partial(self):
        (self.root / 'okay.txt').write_text('ok')
        (self.root / 'Reports2').mkdir()
        (self.root / 'Reports2' / 'nested.txt').write_text('old')
        proposed = ['okay.txt', 'Reports2']
        prev = self.tools.preview_delete(proposed)
        (self.root / 'Reports2' / 'nested.txt').write_text('new value')
        with self.assertRaises(ToolError):
            self.tools.trash_selected(proposed, proposed, prev['identities'], trash_fn=self.fake_trash)
        self.assertTrue((self.root / 'okay.txt').exists())
        self.assertEqual(self.moved, [])

    def test_engine_routes_folder_with_human_click_real(self):
        (self.root / 'Reports2').mkdir()
        state = new_state()
        add_user_request(state, 'پوشه Reports2 رو پاک کن')
        advance(state, self.root, SingleToolClient('delete_files', {'paths': ['Reports2']}))
        self.assertIsNotNone(state['pending'])
        self.assertTrue((self.root / 'Reports2').exists())
        # Patch the tool method; no real OS trash is invoked by automated tests.
        from unittest.mock import patch
        original = LocalTools.trash_selected
        def redirected(instance, proposed_paths, approved_paths, identities):
            return original(instance, proposed_paths, approved_paths, identities,
                            trash_fn=self.fake_trash)
        with patch.object(LocalTools, 'trash_selected', redirected):
            result = approve_or_reject(state, self.root, approved=True,
                                       selected_paths=['Reports2'], delete_mode='trash')
        self.assertEqual(result['trashed_folders'], 1)
        self.assertFalse((self.root / 'Reports2').exists())
        self.assertTrue(state['events'][0]['result']['actual_change'])


if __name__ == '__main__':
    unittest.main()
