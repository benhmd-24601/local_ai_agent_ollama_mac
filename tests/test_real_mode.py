import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_engine import add_user_request, advance, approve_or_reject, new_state
from local_tools import LocalTools, ToolError, make_demo_files


def tool_call(name, args):
    return {"function": {"name": name, "arguments": args}}


class OneReplyClient:
    def chat(self, *_):
        return {"role": "assistant", "content": "", "tool_calls": [
            tool_call("delete_files", {"paths": ["first.txt", "second.txt", "third.txt"]})
        ]}


class RealModeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.tools = LocalTools(self.root)
        self.names = ["first.txt", "second.txt", "third.txt"]
        for name in self.names:
            (self.root / name).write_text(name, encoding="utf8")
        self.preview = self.tools.preview_delete(self.names)

    def tearDown(self):
        self.tmp.cleanup()

    def test_selective_real_mode_trashes_only_selected(self):
        # The fake trashes via rename into another temp directory. No OS Trash is used by tests.
        with tempfile.TemporaryDirectory() as destination:
            trash_dir = Path(destination)
            def fake_trash(p):
                Path(p).rename(trash_dir / Path(p).name)
            result = self.tools.trash_selected(self.names, self.names[:2],
                                               self.preview["identities"], trash_fn=fake_trash)
            self.assertEqual(result["status"], "moved_to_trash")
            self.assertEqual(result["trashed_count"], 2)
            self.assertEqual(result["permanently_deleted"], 0)
            self.assertEqual([item["status"] for item in result["items"]],
                             ["moved_to_trash", "moved_to_trash", "kept"])
            self.assertTrue((self.root / self.names[2]).is_file())
            self.assertFalse((self.root / self.names[0]).exists())
            self.assertTrue((trash_dir / self.names[1]).is_file())

    def test_no_trash_without_explicit_selection(self):
        with self.assertRaises(ToolError):
            self.tools.trash_selected(self.names, [], self.preview["identities"],
                                      trash_fn=lambda _: self.fail("should not run"))

    def test_non_proposed_path_rejected_before_any_trash(self):
        with self.assertRaises(ToolError):
            self.tools.trash_selected(self.names, ["../outside.txt"],
                                      self.preview["identities"],
                                      trash_fn=lambda _: self.fail("should not run"))

    def test_file_changed_since_preview_is_blocked(self):
        (self.root / self.names[0]).write_text("different content here", encoding="utf8")
        with self.assertRaises(ToolError):
            self.tools.trash_selected(self.names, self.names[:2],
                                      self.preview["identities"],
                                      trash_fn=lambda _: self.fail("should not run"))
        self.assertTrue(all((self.root / n).is_file() for n in self.names))

    def test_empty_directory_eligible_but_symlink_blocked(self):
        (self.root / 'directory').mkdir()
        outside = self.root.parent / (self.root.name + '_outside.txt')
        outside.write_text('should not touch', encoding='utf8')
        self.addCleanup(outside.unlink)
        (self.root / 'link.txt').symlink_to(outside)
        preview = self.tools.preview_delete(['directory', 'link.txt'])
        self.assertEqual(set(preview['identities']), {'directory'})
        self.assertEqual(preview['items'][0]['kind'], 'folder')
        self.assertEqual(preview['items'][0]['files_count'], 0)
        self.assertEqual(preview['items'][0]['folders_count'], 0)
        self.assertEqual(preview['items'][1]['status'], 'blocked')
        with self.assertRaises(ToolError):
            self.tools.trash_selected(['link.txt'], ['link.txt'], preview['identities'],
                                      trash_fn=lambda _: self.fail('should not run'))

    def test_mixed_batch_partial_failure_stops(self):
        called = []
        def fake_trash(p):
            called.append(Path(p).name)
            if len(called) == 2:
                raise OSError('demo failure')
        result = self.tools.trash_selected(self.names, self.names,
                                           self.preview['identities'], trash_fn=fake_trash)
        self.assertEqual(result['status'], 'partial_failure')
        self.assertEqual(result['trashed_count'], 1)
        self.assertEqual(called, self.names[:2])
        self.assertEqual(result['items'][-1]['status'], 'not_attempted')

    def test_dry_run_does_not_invoke_real_trash(self):
        self.assertEqual(self.tools.simulate_delete(self.names, self.names[:2])['real_deletions'], 0)
        self.assertTrue(all((self.root / n).exists() for n in self.names))

    def test_engine_blocks_on_pending_then_rejects(self):
        state = new_state()
        add_user_request(state, 'دو فایل رو حذف کن')
        advance(state, self.root, OneReplyClient())
        self.assertIsNotNone(state['pending'])
        self.assertTrue(all((self.root / n).exists() for n in self.names))
        result = approve_or_reject(state, self.root, approved=False,
                                   selected_paths=self.names[:2], delete_mode='trash')
        self.assertEqual(result['status'], 'rejected_by_user')
        self.assertFalse(state['events'][0]['result']['actual_change'])
        self.assertTrue(all((self.root / n).exists() for n in self.names))

    def test_engine_default_approval_is_dry_run(self):
        state = new_state()
        add_user_request(state, 'دو فایل رو حذف کن')
        advance(state, self.root, OneReplyClient())
        result = approve_or_reject(state, self.root, approved=True,
                                   selected_paths=self.names[:2])
        self.assertEqual(result['status'], 'dry_run_only')
        self.assertFalse(state['events'][0]['result']['actual_change'])

    def test_engine_real_mode_uses_only_approved_paths(self):
        state = new_state()
        add_user_request(state, 'دو فایل رو حذف کن')
        advance(state, self.root, OneReplyClient())
        def fake_trash(self_arg, proposed_paths, approved_paths, identities):
            self.assertEqual(approved_paths, self.names[:2])
            self.assertEqual(set(identities), set(self.names))
            return {"status": "moved_to_trash", "trashed_count": 2,
                    "mode": "trash", "permanently_deleted": 0,
                    "items": [{"path": n, "status": "moved_to_trash"} for n in approved_paths]}
        with patch.object(LocalTools, "trash_selected", new=fake_trash):
            result = approve_or_reject(state, self.root, approved=True,
                                       selected_paths=self.names[:2], delete_mode='trash')
        self.assertEqual(result['trashed_count'], 2)
        self.assertEqual(state['events'][0]['result']['delete_mode'], 'trash')
        self.assertTrue(state['events'][0]['result']['actual_change'])
        self.assertIn('Trash', state['ui_messages'][-1]['content'])

    def test_invalid_mode_fails_closed(self):
        state = new_state()
        add_user_request(state, 'دو فایل رو حذف کن')
        advance(state, self.root, OneReplyClient())
        result = approve_or_reject(state, self.root, approved=True,
                                   selected_paths=self.names[:2], delete_mode='unlink')
        self.assertEqual(result['status'], 'error')
        self.assertFalse(state['events'][0]['result']['actual_change'])
        self.assertTrue(all((self.root / n).exists() for n in self.names))


if __name__ == '__main__':
    unittest.main()
