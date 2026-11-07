import tempfile
import unittest
from pathlib import Path

from agent_engine import add_user_request, advance, approve_or_reject, new_state
from local_tools import make_demo_files


def call(name, args):
    return {'function': {'name': name, 'arguments': args}}


class FakeClient:
    def __init__(self, messages):
        self.replies = list(messages)
        self.inputs = []

    def chat(self, messages, tools):
        self.inputs.append(list(messages))
        return self.replies.pop(0)


class ApprovalTests(unittest.TestCase):
    def test_folder_creation_pauses_until_click(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            client = FakeClient([
                {'role': 'assistant', 'content': '', 'tool_calls': [call('create_folder', {'path': 'archive'})]},
                {'role': 'assistant', 'content': 'پوشه ایجاد شد.'},
            ])
            state = new_state()
            add_user_request(state, 'یه پوشه به اسم archive بساز')
            advance(state, root, client)
            self.assertIsNotNone(state['pending'])
            self.assertFalse((root / 'archive').exists())
            result = approve_or_reject(state, root, approved=True)
            self.assertEqual(result['status'], 'created')
            self.assertTrue((root / 'archive').is_dir())
            advance(state, root, client)
            self.assertEqual(state['ui_messages'][-1]['content'], 'پوشه ایجاد شد.')
            self.assertEqual(client.inputs[-1][-1]['role'], 'tool')

    def test_rejected_folder_is_not_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            client = FakeClient([
                {'role': 'assistant', 'content': '', 'tool_calls': [call('create_folder', {'path': 'private'})]},
                {'role': 'assistant', 'content': 'انجام نشد.'},
            ])
            state = new_state()
            add_user_request(state, 'پوشه بساز')
            advance(state, root, client)
            result = approve_or_reject(state, root, approved=False)
            self.assertEqual(result['status'], 'rejected_by_user')
            self.assertFalse((root / 'private').exists())
            advance(state, root, client)
            self.assertEqual(state['ui_messages'][-1]['content'], 'انجام نشد.')

    def test_selective_delete_is_always_simulated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = make_demo_files(root)
            client = FakeClient([
                {'role': 'assistant', 'content': '', 'tool_calls': [call('delete_files', {'paths': files})]},
                {'role': 'assistant', 'content': 'فقط شبیه‌سازی شد.'},
            ])
            original = {f: (root / f).read_bytes() for f in files}
            state = new_state()
            add_user_request(state, 'حذف پیشنهادی')
            advance(state, root, client)
            self.assertIsNotNone(state['pending'])
            result = approve_or_reject(state, root, approved=True, selected_paths=files[:2])
            self.assertEqual(result['status'], 'dry_run_only')
            self.assertEqual(result['real_deletions'], 0)
            self.assertEqual([x['status'] for x in result['items']],
                             ['simulated_delete', 'simulated_delete', 'kept'])
            self.assertEqual(original, {f: (root / f).read_bytes() for f in files})
            advance(state, root, client)

    def test_tools_in_one_reply_processed_in_order_and_pause(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'example.txt').write_text('abc', encoding='utf-8')
            client = FakeClient([
                {'role': 'assistant', 'content': '', 'tool_calls': [
                    call('list_files', {'folder': '.'}),
                    call('create_folder', {'path': 'archive'}),
                    call('move_file', {'source': 'example.txt', 'destination': 'archive/example.txt'}),
                ]},
                {'role': 'assistant', 'content': 'تمام.'},
            ])
            state = new_state()
            add_user_request(state, 'لیست کن و بعد منتقل کن')
            advance(state, root, client)
            self.assertEqual(state['pending']['name'], 'create_folder')
            self.assertTrue((root / 'example.txt').exists())
            self.assertEqual(state['events'][0]['tool'], 'list_files')
            approve_or_reject(state, root, approved=True)
            advance(state, root, client)
            self.assertEqual(state['pending']['name'], 'move_file')
            self.assertTrue((root / 'example.txt').exists())
            approve_or_reject(state, root, approved=True)
            advance(state, root, client)
            self.assertFalse((root / 'example.txt').exists())
            self.assertEqual((root / 'archive/example.txt').read_text(), 'abc')

    def test_reject_cancels_remaining_queued_write_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = new_state()
            client = FakeClient([
                {'role': 'assistant', 'content': '', 'tool_calls': [
                    call('create_folder', {'path': 'one'}),
                    call('create_folder', {'path': 'two'}),
                ]},
                {'role': 'assistant', 'content': 'انجام ندادم.'},
            ])
            add_user_request(state, 'دو پوشه بساز')
            advance(state, root, client)
            approve_or_reject(state, root, approved=False)
            self.assertEqual(state['queue'], [])
            advance(state, root, client)
            self.assertFalse((root / 'one').exists())
            self.assertFalse((root / 'two').exists())

    def test_unknown_tool_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            client = FakeClient([
                {'role': 'assistant', 'content': '', 'tool_calls': [call('run_shell', {'command': 'whoami'})]},
                {'role': 'assistant', 'content': 'نمی‌شود.'},
            ])
            state = new_state()
            add_user_request(state, 'run command')
            advance(state, root, client)
            self.assertEqual(state['events'][0]['result']['status'], 'blocked')
            self.assertIsNone(state['pending'])

    def test_invalid_args_do_not_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            client = FakeClient([
                {'role': 'assistant', 'content': '', 'tool_calls': [call('create_folder', '{broken')]},
                {'role': 'assistant', 'content': 'آرگومان اشتباه بود.'},
            ])
            state = new_state()
            add_user_request(state, 'folder')
            advance(state, root, client)
            self.assertEqual(state['events'][0]['result']['status'], 'error')
            self.assertIsNone(state['pending'])


if __name__ == '__main__':
    unittest.main()
