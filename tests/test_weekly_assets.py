# -*- coding: utf-8 -*-
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from weekly_assets import (
    WeeklyAssetStore, assert_result_write_target, initialize_weekly_assets,
    require_business_ready,
)


class TestWeeklyAssets(unittest.TestCase):
    def test_lock_rejects_concurrent_initializer(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = WeeklyAssetStore(Path(tmp))
            with store.lock('seq-1'):
                with self.assertRaisesRegex(RuntimeError, '已有初始化任务'):
                    with store.lock('seq-1'):
                        pass

    def test_write_guard_accepts_only_result(self):
        manifest = {
            'registry': {'spreadsheet_token': 'registry'},
            'source': {'spreadsheet_token': 'source'},
            'snapshot': {'spreadsheet_token': 'snapshot'},
            'result': {'spreadsheet_token': 'result'},
        }
        assert_result_write_target(manifest, 'result')
        for token in ('registry', 'source', 'snapshot', 'other'):
            with self.assertRaises(RuntimeError):
                assert_result_write_target(manifest, token)

    def test_daily_gate_rejects_missing_or_unmapped_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = WeeklyAssetStore(Path(tmp))
            with self.assertRaisesRegex(RuntimeError, '尚未初始化'):
                require_business_ready(store, 'seq-1')
            store.save('seq-1', {'status': 'ready', 'business_ready': False})
            with self.assertRaisesRegex(RuntimeError, 'R1.5'):
                require_business_ready(store, 'seq-1')
            store.save('seq-1', {'status': 'ready', 'business_ready': True})
            self.assertTrue(require_business_ready(store, 'seq-1')['business_ready'])

    def test_second_initialize_reuses_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = WeeklyAssetStore(Path(tmp))
            fc = Mock()
            fc.cfg = {'feishu_allowed_hosts': ['x']}
            fc.resolve_wiki_obj.return_value = ('source', 'sheet')
            structure = {'sheet_count': 1, 'sheets': [{'title': 'PD03'}], 'sha256': 'hash'}
            fc.spreadsheet_structure.return_value = structure
            fc.wait_spreadsheet_structure.return_value = structure
            fc.list_root_files.return_value = []
            fc.copy_file.return_value = {'token': 'snapshot', 'url': 'snapshot-url'}
            fc.create_spreadsheet.return_value = {
                'spreadsheet_token': 'result', 'url': 'result-url'}
            fc.query_sheets.return_value = [{'sheet_id': 'default'}]
            selection = SimpleNamespace(period_id='seq-1', source_url='https://x/wiki/y',
                                        row_number=2)
            registry = {'url': 'https://x/wiki/r', 'spreadsheet_token': 'registry',
                        'sheet_id': 's1'}
            fc.ensure_permission_member.side_effect = lambda token, typ, member: {
                'member_id': member, 'perm': 'full_access', 'reused': False}
            first, reused1 = initialize_weekly_assets(
                fc, store, selection, registry, manager_open_id='ou_admin')
            second, reused2 = initialize_weekly_assets(
                fc, store, selection, registry, manager_open_id='ou_admin')
            self.assertFalse(reused1)
            self.assertTrue(reused2)
            self.assertEqual(first['snapshot']['spreadsheet_token'],
                             second['snapshot']['spreadsheet_token'])
            self.assertEqual(fc.copy_file.call_count, 1)
            self.assertEqual(fc.create_spreadsheet.call_count, 1)
            self.assertEqual(fc.ensure_permission_member.call_count, 4)
            self.assertEqual(second['human_manager']['member_id'], 'ou_admin')

    def test_recreate_increments_generation_and_preserves_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = WeeklyAssetStore(Path(tmp))
            fc = Mock()
            fc.cfg = {'feishu_allowed_hosts': ['x']}
            fc.resolve_wiki_obj.return_value = ('source', 'sheet')
            structure = {'sheet_count': 1, 'sheets': [{'title': 'PD03'}], 'sha256': 'hash'}
            fc.spreadsheet_structure.return_value = structure
            fc.wait_spreadsheet_structure.return_value = structure
            fc.list_root_files.return_value = []
            fc.copy_file.side_effect = [
                {'token': 'snapshot-1', 'url': 's1'},
                {'token': 'snapshot-2', 'url': 's2'},
            ]
            fc.create_spreadsheet.side_effect = [
                {'spreadsheet_token': 'result-1', 'url': 'r1'},
                {'spreadsheet_token': 'result-2', 'url': 'r2'},
            ]
            fc.query_sheets.return_value = [{'sheet_id': 'default'}]
            selection = SimpleNamespace(period_id='seq-1', source_url='https://x/wiki/y',
                                        row_number=2)
            registry = {'url': 'https://x/wiki/r', 'spreadsheet_token': 'registry',
                        'sheet_id': 's1'}
            fc.ensure_permission_member.return_value = {
                'member_id': 'ou_admin', 'perm': 'full_access', 'reused': False}
            initialize_weekly_assets(
                fc, store, selection, registry, manager_open_id='ou_admin')
            rebuilt, reused = initialize_weekly_assets(
                fc, store, selection, registry, recreate=True,
                manager_open_id='ou_admin')
            self.assertFalse(reused)
            self.assertEqual(rebuilt['generation'], 2)
            self.assertEqual(len(rebuilt['history']), 1)
            self.assertEqual(rebuilt['history'][0]['snapshot']['spreadsheet_token'],
                             'snapshot-1')
            self.assertEqual(rebuilt['snapshot']['spreadsheet_token'], 'snapshot-2')

    def test_fixed_result_is_also_granted_manager_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = WeeklyAssetStore(Path(tmp))
            store.root.mkdir(parents=True, exist_ok=True)
            (store.root / 'fixed_result.json').write_text(
                '{"spreadsheet_token":"fixed-result","url":"https://x/sheets/fixed-result"}',
                encoding='utf-8')
            fc = Mock()
            fc.cfg = {'feishu_allowed_hosts': ['x']}
            fc.resolve_wiki_obj.return_value = ('source', 'sheet')
            structure = {'sheet_count': 1, 'sheets': [{'title': 'PD03'}], 'sha256': 'hash'}
            fc.spreadsheet_structure.return_value = structure
            fc.wait_spreadsheet_structure.return_value = structure
            fc.list_root_files.return_value = []
            fc.copy_file.return_value = {'token': 'snapshot', 'url': 'snapshot-url'}
            fc.query_sheets.return_value = [{'sheet_id': 'default'}]
            fc.ensure_permission_member.return_value = {
                'member_id': 'ou_admin', 'member_type': 'openid',
                'perm': 'full_access', 'verified': True, 'reused': False}
            selection = SimpleNamespace(period_id='seq-fixed', source_url='https://x/wiki/y',
                                        row_number=2)
            registry = {'url': 'https://x/wiki/r', 'spreadsheet_token': 'registry',
                        'sheet_id': 's1'}

            manifest, reused = initialize_weekly_assets(
                fc, store, selection, registry, manager_open_id='ou_admin')

            self.assertFalse(reused)
            self.assertEqual(manifest['result']['spreadsheet_token'], 'fixed-result')
            self.assertEqual(manifest['human_manager']['result']['perm'], 'full_access')
            self.assertEqual(manifest['human_manager']['result']['fixed_result'], True)
            self.assertEqual(fc.ensure_permission_member.call_count, 2)
            self.assertEqual(
                fc.ensure_permission_member.call_args_list[-1].args[:3],
                ('fixed-result', 'sheet', 'ou_admin'))

    def test_initialize_requires_human_manager(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = WeeklyAssetStore(Path(tmp))
            selection = SimpleNamespace(period_id='seq-1', source_url='https://x/wiki/y',
                                        row_number=2)
            with self.assertRaisesRegex(RuntimeError, 'feishu_manager_open_id'):
                initialize_weekly_assets(Mock(), store, selection, {})

    def test_direct_sheets_source_does_not_resolve_as_wiki(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = WeeklyAssetStore(Path(tmp))
            fc = Mock()
            fc.cfg = {'feishu_allowed_hosts': ['tenant.feishu.cn']}
            structure = {'sheet_count': 1, 'sheets': [{'title': 'CPD03'}],
                         'sha256': 'hash'}
            fc.spreadsheet_structure.return_value = structure
            fc.wait_spreadsheet_structure.return_value = structure
            fc.list_root_files.return_value = []
            fc.copy_file.return_value = {'token': 'snapshot', 'url': 'snapshot-url'}
            fc.create_spreadsheet.return_value = {
                'spreadsheet_token': 'result', 'url': 'result-url'}
            fc.query_sheets.return_value = [{'sheet_id': 'default'}]
            fc.ensure_permission_member.return_value = {
                'member_id': 'ou_admin', 'perm': 'full_access', 'reused': False}
            selection = SimpleNamespace(
                period_id='seq-2',
                source_url='https://tenant.feishu.cn/sheets/DirectToken?sheet=abc',
                row_number=3)
            manifest, reused = initialize_weekly_assets(
                fc, store, selection, {}, manager_open_id='ou_admin')
            self.assertFalse(reused)
            self.assertEqual(manifest['source']['spreadsheet_token'], 'DirectToken')
            fc.resolve_wiki_obj.assert_not_called()


if __name__ == '__main__':
    unittest.main()
