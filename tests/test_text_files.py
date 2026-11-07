import tempfile
import unittest
from pathlib import Path

from agent_engine import (add_user_request, advance, approve_or_reject,
                          new_state, TOOLS)
from local_tools import LocalTools, ToolError


def call(name, args):
    return {'function': {'name': name, 'arguments': args}}


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)

    def chat(self, messages, tools):
        return self.responses.pop(0)


class TextFileToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.tools = LocalTools(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_creates_txt_when_extension_omitted(self):
        p = self.tools.preview_create_text_file('kk', '')
        self.assertEqual(p['path'], 'kk.txt')
        self.assertFalse((self.root / 'kk.txt').exists())
        result = self.tools.create_text_file('kk', '')
        self.assertEqual(result['status'], 'created')
        self.assertEqual((self.root / 'kk.txt').read_text(), '')

    def test_utf8_creation_and_read(self):
        self.tools.create_text_file('note.txt', 'سلام!\nHello')
        self.assertEqual(self.tools.read_text_file('note.txt')['content'], 'سلام!\nHello')

    def test_no_overwrite_on_create(self):
        self.tools.create_text_file('kk.txt', 'اول')
        with self.assertRaises(ToolError):
            self.tools.create_text_file('kk.txt', 'دوم')
        self.assertEqual((self.root / 'kk.txt').read_text(), 'اول')

    def test_append_and_replace(self):
        self.tools.create_text_file('kk.txt', 'سطر اول\n')
        preview = self.tools.preview_edit_text_file('kk.txt', 'سطر دوم\n', 'append')
        self.assertEqual(preview['new_preview'], 'سطر اول\nسطر دوم\n')
        result = self.tools.edit_text_file('kk.txt', 'سطر دوم\n', 'append', preview['identity'])
        self.assertEqual(result['status'], 'updated')
        self.assertEqual((self.root / 'kk.txt').read_text(), 'سطر اول\nسطر دوم\n')
        preview2 = self.tools.preview_edit_text_file('kk.txt', 'جایگزین', 'replace')
        self.tools.edit_text_file('kk.txt', 'جایگزین', 'replace', preview2['identity'])
        self.assertEqual((self.root / 'kk.txt').read_text(), 'جایگزین')

    def test_reject_invalid_paths_and_extensions(self):
        for bad in ['../out.txt', '/tmp/foo.txt', '.secret.txt', 'other.md', 'folder/../../escape.txt']:
            with self.subTest(bad=bad), self.assertRaises(ToolError):
                self.tools.create_text_file(bad, 'x')
        self.assertFalse((self.root.parent / 'out.txt').exists())

    def test_no_implicit_parent_creation(self):
        with self.assertRaises(ToolError):
            self.tools.create_text_file('missing/child.txt', 'x')
        self.assertFalse((self.root / 'missing').exists())

    def test_symlink_is_not_modified(self):
        (self.root / 'outside.txt').write_text('secret', encoding='utf-8')
        try:
            (self.root / 'link.txt').symlink_to(self.root / 'outside.txt')
        except OSError:
            self.skipTest('No symlink support')
        with self.assertRaises(ToolError):
            self.tools.preview_edit_text_file('link.txt', 'changed', 'replace')
        self.assertEqual((self.root / 'outside.txt').read_text(), 'secret')

    def test_mutation_after_preview_cancels_edit(self):
        (self.root / 'note.txt').write_text('before', encoding='utf-8')
        preview = self.tools.preview_edit_text_file('note.txt', 'after', 'replace')
        (self.root / 'note.txt').write_text('externally changed', encoding='utf-8')
        with self.assertRaises(ToolError):
            self.tools.edit_text_file('note.txt', 'after', 'replace', preview['identity'])
        self.assertEqual((self.root / 'note.txt').read_text(), 'externally changed')

    def test_missing_edit_target(self):
        with self.assertRaises(ToolError):
            self.tools.preview_edit_text_file('missing.txt', 'x', 'append')

    def test_invalid_mode_and_oversized(self):
        self.tools.create_text_file('note.txt', 'ok')
        with self.assertRaises(ToolError):
            self.tools.preview_edit_text_file('note.txt', 'a', 'erase')
        with self.assertRaises(ToolError):
            self.tools.create_text_file('huge.txt', 'A'*20001)
        self.assertFalse((self.root / 'huge.txt').exists())

    def test_preview_never_writes(self):
        self.tools.create_text_file('existing.txt', 'a')
        self.tools.preview_edit_text_file('existing.txt', 'new', 'replace')
        self.assertEqual((self.root / 'existing.txt').read_text(), 'a')


class TextFileAgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_tools_advertised_to_model(self):
        names = {t['function']['name'] for t in TOOLS}
        self.assertIn('create_text_file', names)
        self.assertIn('edit_text_file', names)

    def test_create_needs_explicit_approval_and_logs(self):
        client = FakeClient([
            {'role': 'assistant', 'content': '',
             'tool_calls': [call('create_text_file', {'path': 'kk.txt', 'content': 'سلام'})]},
            {'role': 'assistant', 'content': 'فایل ساخته شد.'},
        ])
        state = new_state()
        add_user_request(state, 'یه فایل txt به اسم kk بساز')
        advance(state, self.root, client)
        self.assertEqual(state['pending']['name'], 'create_text_file')
        self.assertFalse((self.root / 'kk.txt').exists())
        result = approve_or_reject(state, self.root, True)
        self.assertEqual(result['status'], 'created')
        self.assertTrue(state['events'][0]['result']['actual_change'])
        self.assertEqual((self.root / 'kk.txt').read_text(), 'سلام')
        advance(state, self.root, client)
        self.assertEqual(state['ui_messages'][-1]['content'], 'فایل ساخته شد.')

    def test_reject_create_does_not_write(self):
        client = FakeClient([
            {'role': 'assistant', 'content': '',
             'tool_calls': [call('create_text_file', {'path': 'kk', 'content': ''})]},
        ])
        state = new_state()
        add_user_request(state, 'یه فایل خالی به اسم kk بساز')
        advance(state, self.root, client)
        result = approve_or_reject(state, self.root, False)
        self.assertEqual(result['status'], 'rejected_by_user')
        self.assertFalse((self.root / 'kk.txt').exists())

    def test_edit_needs_explicit_approval_and_reject_preserves_content(self):
        (self.root / 'kk.txt').write_text('before', encoding='utf-8')
        client = FakeClient([
            {'role': 'assistant', 'content': '',
             'tool_calls': [call('edit_text_file', {'path': 'kk.txt', 'content': 'new', 'mode': 'append'})]},
        ])
        state = new_state()
        add_user_request(state, 'به kk.txt متن new رو اضافه کن')
        advance(state, self.root, client)
        self.assertEqual(state['pending']['name'], 'edit_text_file')
        self.assertEqual((self.root / 'kk.txt').read_text(), 'before')
        approve_or_reject(state, self.root, False)
        self.assertEqual((self.root / 'kk.txt').read_text(), 'before')

    def test_edit_blocked_when_modified_after_approval_preview(self):
        (self.root / 'kk.txt').write_text('before', encoding='utf-8')
        client = FakeClient([
            {'role': 'assistant', 'content': '',
             'tool_calls': [call('edit_text_file', {'path': 'kk.txt', 'content': 'new', 'mode': 'replace'})]},
        ])
        state = new_state()
        add_user_request(state, 'جایگزین کن')
        advance(state, self.root, client)
        (self.root / 'kk.txt').write_text('change between preview and approval', encoding='utf-8')
        result = approve_or_reject(state, self.root, True)
        self.assertEqual(result['status'], 'error')
        self.assertEqual((self.root / 'kk.txt').read_text(), 'change between preview and approval')

    def test_queue_folder_then_text_file_with_two_approvals(self):
        client = FakeClient([
            {'role': 'assistant', 'content': '', 'tool_calls': [
                call('create_folder', {'path': 'notes'}),
                call('create_text_file', {'path': 'notes/kk.txt', 'content': 'Hello'}),
            ]},
            {'role': 'assistant', 'content': 'هر دو انجام شدند.'},
        ])
        state = new_state()
        add_user_request(state, 'پوشه notes بساز، داخلش یه فایل kk.txt با Hello بساز')
        advance(state, self.root, client)
        self.assertEqual(state['pending']['name'], 'create_folder')
        approve_or_reject(state, self.root, True)
        advance(state, self.root, client)
        self.assertEqual(state['pending']['name'], 'create_text_file')
        self.assertFalse((self.root / 'notes/kk.txt').exists())
        approve_or_reject(state, self.root, True)
        advance(state, self.root, client)
        self.assertEqual((self.root / 'notes/kk.txt').read_text(), 'Hello')


if __name__ == '__main__':
    unittest.main()
