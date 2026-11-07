import tempfile
import unittest
from pathlib import Path

from local_tools import LocalTools, ToolError, make_demo_files


class LocalToolsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.tools = LocalTools(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_demo_selective_delete_does_not_change_files(self):
        paths = make_demo_files(self.root)
        before = {p: (self.root / p).read_bytes() for p in paths}
        result = self.tools.simulate_delete(paths, paths[:2])
        self.assertEqual(result['real_deletions'], 0)
        self.assertEqual([i['status'] for i in result['items']],
                         ['simulated_delete', 'simulated_delete', 'kept'])
        after = {p: (self.root / p).read_bytes() for p in paths}
        self.assertEqual(before, after)

    def test_parent_traversal_and_absolute_path_rejected(self):
        for p in ['../secret.txt', '/etc/passwd', r'C:\\Windows\\system.ini', '.env']:
            with self.subTest(path=p), self.assertRaises(ToolError):
                self.tools.safe_path(p)

    def test_symlink_rejected(self):
        secret = self.root / 'outside.txt'
        secret.write_text('secret')
        link = self.root / 'link.txt'
        try:
            link.symlink_to(secret)
        except OSError:
            self.skipTest('Current user cannot create symlinks')
        with self.assertRaises(ToolError):
            self.tools.read_text_file('link.txt')

    def test_real_move_requires_no_overwrite(self):
        self.tools.create_folder('archive')
        (self.root / 'one.txt').write_text('alpha', encoding='utf-8')
        self.tools.move_file('one.txt', 'archive/one.txt')
        self.assertFalse((self.root / 'one.txt').exists())
        self.assertEqual((self.root / 'archive/one.txt').read_text(), 'alpha')
        (self.root / 'two.txt').write_text('beta', encoding='utf-8')
        with self.assertRaises(ToolError):
            self.tools.move_file('two.txt', 'archive/one.txt')

    def test_find_and_read(self):
        (self.root / 'notes.txt').write_text('hello', encoding='utf-8')
        self.assertEqual(self.tools.find_files('.', '', 'txt')['matches'], ['notes.txt'])
        self.assertEqual(self.tools.read_text_file('notes.txt')['content'], 'hello')


if __name__ == '__main__':
    unittest.main()
