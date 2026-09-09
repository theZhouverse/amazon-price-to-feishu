"""Per-run live-source snapshots and full-row publication; all Feishu calls are fakes."""
import json
import re
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import main
import weekly_execution as execution
from weekly_assets import WeeklyAssetStore
from weekly_result import RESULT_HEADERS
from test_weekly_result import FakeFeishu, source, row, CFG
import test_weekly_result as base_fixture
from test_weekly_result_write import result
from models import PageStatus


class MemoryTable(FakeFeishu):
    def __init__(self, rows=None, fail_second=False):
        super().__init__({'s1': source(*(rows or [])), 's2': []}, with_result=True)
        self.result_sheets['EMPTY'] = 'r2'
        self.grids = {'r1': [['title'], RESULT_HEADERS,
                            ['B000000001'] + ['old'] * 21,
                            ['B000000009'] + ['old'] * 21,
                            ['B000000008'] + ['old'] * 21],
                      'r2': [['title'], RESULT_HEADERS]}
        self.fail_second = fail_second

    def read_values(self, token, sid, rng):
        if token == 'snapshot':
            return super().read_values(token, sid, rng)
        a, r1, b, r2 = re.fullmatch(r'([A-Z])(\d+):([A-Z])(\d+)', rng).groups()
        return [r[ord(a)-65:ord(b)-64] for r in self.grids[sid][int(r1)-1:int(r2)]]

    def write_values(self, token, sid, rng, values):
        self.writes.append((sid, rng, deepcopy(values)))
        a, r1, b, r2 = re.fullmatch(r'([A-Z])(\d+):([A-Z])(\d+)', rng).groups()
        grid = self.grids[sid]
        while len(grid) < int(r2):
            grid.append([])
        for i, vals in enumerate(values, int(r1)-1):
            grid[i] += [''] * max(0, ord(b)-64-len(grid[i]))
            grid[i][ord(a)-65:ord(b)-64] = vals

    def backup_target_sheet(self, token, title, sid, run_id):
        if self.fail_second and sid == 'r2':
            raise RuntimeError('second sheet backup failure')
        self.backups.append(title)
        return Path('fake-backup')


class PriceRefreshTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = WeeklyAssetStore(self.root)
        execution.atomic_json(self.root / 'fixed_result.json', {
            'spreadsheet_token': 'result', 'url': 'fixed-url', 'period_id': 'seq-3'})
        self.selection = SimpleNamespace(period_id='seq-3', row_number=2,
            source_url='https://tenant.feishu.cn/sheets/SourceToken')
        self.cfg = {**CFG, 'html_archive_required': False,
                    'weekly_registry_url': 'registry', 'feishu_allowed_hosts': ['tenant.feishu.cn'],
                    'feishu_manager_open_id': 'manager'}

    def test_new_run_copies_latest_even_same_week_but_resume_reuses(self):
        fc = Mock()
        fc.cfg = self.cfg
        structure = {'sheets': [{'title': 'PD03'}], 'sha256': 'hash'}
        fc.spreadsheet_structure.return_value = structure
        fc.wait_spreadsheet_structure.return_value = structure
        fc.list_root_files.return_value = []
        fc.copy_file.side_effect = [{'token': 'morning', 'url': 'am'},
                                    {'token': 'afternoon', 'url': 'pm'}]
        fc.query_sheets.return_value = []
        fc.ensure_permission_member.return_value = {'verified': True}
        discovery = {'sheets': [{'status': 'mapped', 'result_sheet': 'PD03'}]}
        with patch.object(execution, 'build_discovery', return_value=discovery) as discover, \
             patch.object(execution, 'validate_discovery'), \
             patch.object(execution, 'audit_manifest_links', return_value={'invalid_count': 0}):
            am = execution.ensure_price_week(fc, self.store, self.selection, {}, self.cfg,
                                            allow_create=True, run_id='am')
            pm = execution.ensure_price_week(fc, self.store, self.selection, {}, self.cfg,
                                            allow_create=True, run_id='pm')
            recovered = execution.ensure_price_week(fc, self.store, self.selection, {}, self.cfg,
                                            allow_create=True, run_id='pm', resume=True)
        self.assertEqual(am['snapshot']['spreadsheet_token'], 'morning')
        self.assertEqual(pm['snapshot']['spreadsheet_token'], 'afternoon')
        self.assertEqual(recovered['snapshot']['spreadsheet_token'], 'afternoon')
        self.assertEqual(fc.copy_file.call_count, 2)
        self.assertEqual(discover.call_count, 2)
        fc.create_spreadsheet.assert_not_called()
        self.assertTrue((self.root / 'seq-3/runs/am.json').exists())
        with self.assertRaisesRegex(RuntimeError, '只能恢复'):
            execution.ensure_price_week(fc, self.store, self.selection, {}, self.cfg,
                                        allow_create=True, run_id='am', resume=True)

    def test_fixed_identity_required(self):
        (self.root / 'fixed_result.json').unlink()
        with self.assertRaisesRegex(RuntimeError, '固定结果表'):
            execution.ensure_price_week(Mock(), self.store, self.selection, {}, self.cfg,
                                        allow_create=True, run_id='am')

    def test_dry_reads_current_source_without_cloud_copy_or_manifest_change(self):
        fc = Mock()
        discovery = {'sheets': [{'status': 'mapped', 'result_sheet': 'CPD03'}]}
        with patch.object(execution, 'build_discovery', return_value=discovery) as discover, \
             patch.object(execution, 'validate_discovery'), \
             patch.object(execution, 'audit_manifest_links', return_value={'invalid_count': 0}):
            m = execution.ensure_price_week(fc, self.store, self.selection, {}, self.cfg,
                                           allow_create=False, run_id='dry')
        discover.assert_called_once_with(fc, 'SourceToken')
        self.assertTrue(m['readonly_preview'])
        self.assertIsNone(self.store.load('seq-3'))
        fc.copy_file.assert_not_called()
        fc.create_spreadsheet.assert_not_called()

    def publish(self, fc, results):
        m = base_fixture.WeeklyResultTest().manifest()
        m.update(period_id='seq-3', snapshot_run_id='run1', base_sync_pending=True)
        m['result'].update(name='fixed', url='url')
        self.store.save('seq-3', m)
        execution.claim_latest_run(self.store, m, 'run1')
        with patch.object(main, '_rename_run_result'):
            return main._deliver_weekly_results(fc, self.store, m, 'run1',
                                                results, self.cfg, self.root)

    def test_new_base_values_add_delete_reorder_and_price_alignment(self):
        fc = MemoryTable([row('B000000002', sku='new', normal=40, target=36),
                          row('B000000001', sku='changed', normal=30, target=27)])
        report = self.publish(fc, {'PD03': [result('B000000001'), result('B000000002')], 'EMPTY': []})
        self.assertEqual(report['written_rows'], 2)
        self.assertEqual(report['status'], 'complete')
        self.assertEqual(fc.grids['r1'][2][:2], ['B000000002', 'new'])
        self.assertEqual(fc.grids['r1'][3][:2], ['B000000001', 'changed'])
        self.assertEqual(fc.grids['r1'][3][6], 27)
        self.assertEqual(fc.grids['r1'][4], [''] * 22)
        self.assertEqual(fc.grids['r1'][2][21], result('B000000002').product_url)
        self.assertTrue(all(values[0] != [''] * 22 for _, _, values in fc.writes))
        self.assertEqual(fc.result_sheets['PD03'], 'r1')

    def test_missing_asin_blocks_before_first_write(self):
        fc = MemoryTable([row('B000000001'), row('B000000002')])
        report = self.publish(fc, {'PD03': [result('B000000001')], 'EMPTY': []})
        self.assertEqual(report['status'], 'partial')
        self.assertIn('ASIN集合', report['failures'][0]['error'])
        self.assertEqual(fc.writes, [])
        self.assertFalse((self.root / 'active_result.json').exists())

    def test_partial_failure_retains_real_count(self):
        fc = MemoryTable([row('B000000001')], fail_second=True)
        report = self.publish(fc, {'PD03': [result('B000000001')], 'EMPTY': []})
        self.assertEqual(report['status'], 'partial')
        self.assertEqual(report['written_rows'], 1)
        self.assertEqual(report['failures'][0]['stage'], 'base_publish')
        self.assertTrue(self.store.load('seq-3')['base_sync_pending'])

    def test_failed_first_sheet_does_not_stop_next_sheet(self):
        fc = MemoryTable([row('B000000001')])
        original = fc.backup_target_sheet
        def fail_first(token, title, sid, run_id):
            if title == 'PD03':
                raise RuntimeError('first backup failed')
            return original(token, title, sid, run_id)
        fc.backup_target_sheet = fail_first
        report = self.publish(fc, {'PD03': [result('B000000001')], 'EMPTY': []})
        self.assertEqual(report['status'], 'partial')
        self.assertTrue(any(sid == 'r2' for sid, _, _ in fc.writes))

    def test_technical_error_keeps_new_base_not_old_price(self):
        fc = MemoryTable([row('B000000001', sku='updated', target=15)])
        cr = result('B000000001')
        cr.status = PageStatus.CRAWL_ERROR
        cr.error = 'captcha'
        report = self.publish(fc, {'PD03': [cr], 'EMPTY': []})
        self.assertEqual(fc.grids['r1'][2][1], 'updated')
        self.assertEqual(fc.grids['r1'][2][7], '')
        self.assertEqual(report['written_rows'], 0)
        self.assertEqual(len(report['blocked']), 1)
        self.assertEqual(report['base_rows_written'], 1)

    def test_all_empty_snapshot_clears_old_rows(self):
        fc = MemoryTable([])
        report = self.publish(fc, {'PD03': [], 'EMPTY': []})
        self.assertEqual(report['status'], 'complete')
        self.assertEqual(fc.grids['r1'][2], [''] * 22)

    def test_unknown_layout_blocks_before_writes(self):
        fc = MemoryTable([row('B000000001')])
        fc.grids['r1'][1] = ['user data']
        report = self.publish(fc, {'PD03': [result('B000000001')], 'EMPTY': []})
        self.assertEqual(report['status'], 'partial')
        self.assertEqual(fc.writes, [])

    def test_source_url_change_rejected_even_same_week(self):
        self.store.save('seq-3', {'source': {'url': 'different'}, 'result': {}})
        fc = Mock()
        with self.assertRaisesRegex(RuntimeError, '新增序号'):
            execution.ensure_price_week(fc, self.store, self.selection, {}, self.cfg,
                                        allow_create=True, run_id='new')
        fc.copy_file.assert_not_called()

    def test_stale_running_batch_cannot_publish_over_new_manifest(self):
        fc = MemoryTable([row('B000000001')])
        m = base_fixture.WeeklyResultTest().manifest()
        m.update(period_id='seq-3', snapshot_run_id='newer', base_sync_pending=True)
        self.store.save('seq-3', m)
        from weekly_result import sync_weekly_result_base
        with self.assertRaisesRegex(RuntimeError, '新批次替换'):
            sync_weekly_result_base(fc, self.store, 'seq-3', self.cfg, 'run1',
                                   staged_results={'PD03': [result('B000000001')], 'EMPTY': []})
        self.assertEqual(fc.writes, [])

    def test_dry_resume_does_not_initialize_cloud(self):
        self.store.save('seq-3', {'snapshot_run_id': 'run1', 'status': 'initializing',
            'source': {'url': self.selection.source_url}, 'result': {}})
        fc = Mock()
        with self.assertRaisesRegex(RuntimeError, '只读模式'):
            execution.ensure_price_week(fc, self.store, self.selection, {}, self.cfg,
                                        allow_create=False, run_id='run1', resume=True)
        fc.copy_file.assert_not_called()


if __name__ == '__main__':
    unittest.main()
