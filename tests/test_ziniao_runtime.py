import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

from ziniao_runtime import (  # noqa: E402
    CONTEXT_STATUS_PATH,
    GLOBAL_LOCK_PATH,
    load_global_runtime,
    global_sellercentral_lock,
    resolve_feedback_stores,
)


class ZiniaoRuntimeTest(unittest.TestCase):
    def test_current_project_resolves_central_store_identities(self):
        info = load_global_runtime(Path(__file__).resolve().parents[1])
        self.assertEqual(info['project_id'], 'amazon_daily')
        self.assertEqual(info['store_ids'], ['26782671389969', '26686718929338'])
        self.assertEqual(Path(info['lock_path']), GLOBAL_LOCK_PATH)
        self.assertEqual(Path(info['status_path']), CONTEXT_STATUS_PATH)

    def test_project_selectors_are_merged_without_accepting_duplicate_identity(self):
        resolved = resolve_feedback_stores({
            'stores': [
                {'key': 'store_a', 'feedback_manager_url': 'https://sellercentral.amazon.com/feedback-manager',
                 'selectors': {'row_selector': '.row'}},
                {'key': 'store_b', 'feedback_manager_url': 'https://sellercentral.amazon.com/feedback-manager',
                 'selectors': {'row_selector': '.row'}},
            ]
        }, project_root=Path(__file__).resolve().parents[1])
        self.assertEqual(resolved['stores'][0]['store_id'], '26782671389969')
        self.assertEqual(resolved['stores'][1]['display_name'], '北蓉')
        with self.assertRaisesRegex(RuntimeError, '不得重复保存'):
            resolve_feedback_stores({
                'stores': [
                    {'key': 'store_a', 'store_id': 'local-copy'},
                    {'key': 'store_b'},
                ]
            }, project_root=Path(__file__).resolve().parents[1])

    def test_shared_lock_publishes_and_releases_redacted_context(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            lock = Path(temp) / 'sellercentral.lock'
            status = Path(temp) / 'context' / 'status.json'
            with global_sellercentral_lock(
                    phase='test', store_ids=['26782671389969'], project_root=root,
                    lock_path=lock, status_path=status):
                active = json.loads(status.read_text(encoding='utf-8'))
                self.assertEqual(active['status'], 'active')
                self.assertEqual(active['project_id'], 'amazon_daily')
                self.assertEqual(active['store_ids'], ['26782671389969'])
                self.assertNotIn('apiKey', json.dumps(active))
            released = json.loads(status.read_text(encoding='utf-8'))
            self.assertEqual(released['status'], 'released')


if __name__ == '__main__':
    unittest.main()
