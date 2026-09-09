import json
import tempfile
import unittest
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import main
import weekly_execution as execution
from weekly_assets import WeeklyAssetStore, initialize_weekly_assets
from models import ReportRow
from test_weekly_result_write import FakeFeishu, manifest, result


class WeeklyExecutionTest(unittest.TestCase):
    def test_fixed_week_publish_replaces_complete_rows_without_clearing_first(self):
        import re
        from test_weekly_result import FakeFeishu as BaseFake, source, row, CFG, WeeklyResultTest
        from weekly_result import sync_weekly_result_base, RESULT_HEADERS
        class FixedTable(BaseFake):
            def __init__(self):
                super().__init__({'s1': source(row('B000000001'))}, with_result=True)
                self.grid = [['title'], RESULT_HEADERS,
                             ['B000000001'] + ['old'] * 20, ['B000000002'] + ['old'] * 20]
            def read_values(self, token, sid, rng):
                if token == 'snapshot':
                    return super().read_values(token, sid, rng)
                a, r1, b, r2 = re.fullmatch(r'([A-Z])(\d+):([A-Z])(\d+)', rng).groups()
                return [r[ord(a)-65:ord(b)-64] for r in self.grid[int(r1)-1:int(r2)]]
            def write_values(self, token, sid, rng, values):
                self.writes.append((sid, rng, values))
                start = int(re.match(r'A(\d+)', rng).group(1)) - 1
                for i, values_row in enumerate(values, start):
                    self.grid[i] = list(values_row)
            def add_sheet(self, *args):
                raise AssertionError('existing Sheet ID must be reused')
        with tempfile.TemporaryDirectory() as root:
            store = WeeklyAssetStore(Path(root))
            data = WeeklyResultTest().manifest()
            data['sheet_mappings'] = data['sheet_mappings'][:1]
            data['base_sync_pending'] = True
            data.update(period_id='seq-3', snapshot_run_id='run1')
            store.save('seq-3', data)
            execution.claim_latest_run(store, data, 'run1')
            execution.atomic_json(store.root / 'fixed_result.json', {
                'spreadsheet_token': 'result', 'url': 'fixed-url', 'period_id': 'seq-3'})
            fc = FixedTable()
            sync_weekly_result_base(fc, store, 'seq-3', CFG, 'run1',
                                   staged_results={'PD03': [result('B000000001')]})
            self.assertEqual(len(fc.writes), 1)
            self.assertEqual(fc.writes[0][1], 'A2:V4')
            self.assertEqual(fc.grid[2][0], 'B000000001')
            self.assertEqual(fc.grid[2][12], 'USD')
            self.assertEqual(fc.grid[3], [''] * 22)
            self.assertFalse(store.load('seq-3')['base_sync_pending'])

    def test_old_lock_marker_does_not_block_restart(self):
        with tempfile.TemporaryDirectory() as root:
            store = WeeklyAssetStore(Path(root))
            marker = Path(root) / '.locks' / 'seq-3.lock'
            marker.parent.mkdir()
            marker.write_text('pid=dead-process')
            with store.lock('seq-3'):
                with self.assertRaisesRegex(RuntimeError, '已有初始化任务'):
                    with store.lock('seq-3'):
                        pass
            with store.lock('seq-3'):
                pass

    def test_first_backup_preserved_on_retry(self):
        import config
        from feishu import FeishuClient
        with tempfile.TemporaryDirectory() as root, patch.object(config, 'OUTPUT_DIR', Path(root)):
            fc = FeishuClient({})
            self.addCleanup(fc.close)
            fc.read_values = Mock(return_value=[['original']])
            fc.query_sheets = Mock(return_value=[{'sheet_id': 'sid', 'grid_properties': {'row_count': 100}}])
            path = fc.backup_target_sheet('result', 'PD03', 'sid', 'run1')
            fc.read_values.return_value = [['changed']]
            fc.backup_target_sheet('result', 'PD03', 'sid', 'run1')
            self.assertEqual(fc.read_values.call_count, 1)
            self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['values'], [['original']])

    def test_existing_week_readonly_and_changed_source_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            store = WeeklyAssetStore(Path(root))
            data = {'status': 'ready', 'business_ready': True, 'source': {'url': 'url'}}
            store.save('seq-2', data)
            selection = SimpleNamespace(period_id='seq-2', source_url='url')
            fc = Mock()
            self.assertEqual(execution.ensure_price_week(fc, store, selection, {}, {}, allow_create=True), data)
            self.assertEqual(fc.mock_calls, [])
            selection.source_url = 'changed'
            with self.assertRaisesRegex(RuntimeError, '新增序号'):
                execution.ensure_price_week(fc, store, selection, {}, {}, allow_create=True)

    def test_dry_run_never_initializes_new_week(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.object(execution, 'initialize_weekly_assets') as init:
                with self.assertRaisesRegex(RuntimeError, '尚未初始化'):
                    execution.ensure_price_week(Mock(), WeeklyAssetStore(Path(root)),
                        SimpleNamespace(period_id='seq-3', source_url='url'), {}, {}, allow_create=False)
                init.assert_not_called()

    def test_frontend_dry_run_uses_ready_snapshot_copy(self):
        with tempfile.TemporaryDirectory() as root:
            store = WeeklyAssetStore(Path(root))
            manifest = {
                'period_id': 'seq-4', 'status': 'ready',
                'mapping_ready': True, 'link_rules_ready': True,
                'source': {'url': 'registry-source'},
                'snapshot': {'spreadsheet_token': 'frozen-copy'},
                'result': {'spreadsheet_token': 'fixed-result'},
            }
            store.save('seq-4', manifest)
            execution.atomic_json(store.root / 'fixed_result.json', {
                'spreadsheet_token': 'fixed-result', 'url': 'fixed-url'})
            selection = SimpleNamespace(period_id='seq-4', source_url='registry-source')
            preview = execution._prepare_price_run(
                Mock(), store, selection, {}, {}, 'frontend-run', False, False)
            self.assertEqual(preview['snapshot']['spreadsheet_token'], 'frozen-copy')
            self.assertEqual(preview['snapshot_run_id'], 'frontend-run')
            self.assertTrue(preview['readonly_preview'])

    def test_resume_same_manifest_can_reclaim_pointer_after_preclaim_failure(self):
        with tempfile.TemporaryDirectory() as root:
            store = WeeklyAssetStore(Path(root))
            data = {
                'period_id': 'seq-3', 'status': 'ready', 'mapping_ready': True,
                'snapshot_run_id': 'run-new', 'source': {'url': 'url'},
                'snapshot': {'spreadsheet_token': 'snapshot'},
                'result': {'spreadsheet_token': 'result'},
            }
            store.save('seq-3', data)
            execution.atomic_json(store.root / 'fixed_result.json', {
                'spreadsheet_token': 'result', 'url': 'fixed-url', 'period_id': 'seq-3'})
            execution.atomic_json(store.root / 'latest_run.json', {
                'period_id': 'seq-3', 'run_id': 'run-old',
                'snapshot_token': 'old-snapshot', 'result_token': 'result'})
            selection = SimpleNamespace(period_id='seq-3', source_url='url')
            recovered = execution._prepare_price_run(
                Mock(), store, selection, {}, {}, 'run-new', True, True)
            self.assertEqual(recovered['snapshot_run_id'], 'run-new')
            self.assertEqual(json.loads((store.root / 'latest_run.json').read_text())['run_id'], 'run-new')

    def test_automatic_new_week_staging_preserves_previous(self):
        with tempfile.TemporaryDirectory() as root:
            store = WeeklyAssetStore(Path(root))
            store.save('seq-2', {'result': {'spreadsheet_token': 'old-do-not-touch'}})
            execution.atomic_json(store.root / 'fixed_result.json', {
                'spreadsheet_token': 'old-do-not-touch', 'url': 'fixed-url', 'period_id': 'seq-2'})
            original = store.manifest_path('seq-2').read_bytes()
            selection = SimpleNamespace(period_id='seq-3', source_url='url')
            data = {'period_id': 'seq-3', 'status': 'ready', 'business_ready': False,
                    'source': {'url': 'url'}, 'snapshot': {'spreadsheet_token': 'snap-new'}}
            def init(*a, **kw):
                store.save('seq-3', data)
                return data, False
            def sync(*a, **kw):
                updated = store.load('seq-3'); updated['business_ready'] = True
                store.save('seq-3', updated)
            discovery = {'sheets': [{'status': 'mapped', 'result_sheet': 'CPD99', 'marketplace': 'CA'}]}
            with patch.object(execution, 'initialize_weekly_assets', side_effect=init) as initialize, \
                 patch.object(execution, 'build_discovery', return_value=discovery), \
                 patch.object(execution, 'validate_discovery'), \
                 patch.object(execution, 'audit_manifest_links', return_value={'invalid_count': 0}), \
                 patch.object(execution, 'sync_weekly_result_base', side_effect=sync):
                current = execution.ensure_price_week(Mock(), store, selection, {},
                    {'weekly_registry_url': 'registry'}, allow_create=True)
                self.assertFalse(current['business_ready'])
                self.assertTrue(current['base_sync_pending'])
                execution.ensure_price_week(Mock(), store, selection, {}, {}, allow_create=True)
                self.assertEqual(initialize.call_count, 1)
            self.assertEqual(store.manifest_path('seq-2').read_bytes(), original)
            self.assertEqual(current['sheet_mappings'][0]['result_sheet'], 'CPD99')

    def test_initialization_resumes_same_generation_after_failure(self):
        for failure_stage in ('snapshot', 'result', 'fixed_result'):
            with self.subTest(stage=failure_stage), tempfile.TemporaryDirectory() as root:
                store = WeeklyAssetStore(Path(root)); fc = Mock()
                if failure_stage == 'fixed_result':
                    execution.atomic_json(store.root / 'fixed_result.json', {
                        'spreadsheet_token': 'fixed-token', 'url': 'fixed-url', 'name': 'Fixed result'})
                fc.cfg = {'feishu_allowed_hosts': ['tenant.feishu.cn']}
                structure = {'sheets': [{'title': 'PD03'}], 'sha256': 'hash'}
                fc.spreadsheet_structure.return_value = structure
                fc.list_root_files.return_value = []
                fc.copy_file.return_value = {'token': 'snap', 'url': 'snap-url'}
                fc.create_spreadsheet.return_value = {'spreadsheet_token': 'result', 'url': 'result-url'}
                fc.ensure_permission_member.return_value = {}
                fc.wait_spreadsheet_structure.side_effect = ([RuntimeError('temporary'), structure]
                    if failure_stage == 'snapshot' else [structure, structure])
                fc.query_sheets.side_effect = ([RuntimeError('temporary'), []]
                    if failure_stage in ('result', 'fixed_result') else [[]])
                selection = SimpleNamespace(period_id='seq-3', row_number=4,
                    source_url='https://tenant.feishu.cn/sheets/SourceToken')
                with self.assertRaisesRegex(RuntimeError, 'temporary'):
                    initialize_weekly_assets(fc, store, selection, {}, manager_open_id='ou_admin')
                data, _ = initialize_weekly_assets(fc, store, selection, {}, manager_open_id='ou_admin')
                self.assertEqual(data['generation'], 1)
                self.assertEqual(fc.copy_file.call_count, 1)
                self.assertEqual(fc.create_spreadsheet.call_count, 0 if failure_stage == 'fixed_result' else 1)
                if failure_stage == 'fixed_result':
                    self.assertEqual(data['result']['spreadsheet_token'], 'fixed-token')
                    self.assertEqual(data['result']['url'], 'fixed-url')
                    self.assertTrue(all(c.args[0] == 'snap' for c in fc.ensure_permission_member.call_args_list))

    def test_price_flow_ignores_html_and_rename_failure_still_notifies(self):
        with tempfile.TemporaryDirectory() as root:
            output = Path(root); store = WeeklyAssetStore(output / 'weekly_runs')
            data = manifest()
            data.update(period_id='seq-2', status='ready', snapshot_run_id='run1', base_sync_pending=True)
            data['source']['url'] = 'url'
            data['result'].update(name='Existing result', url='https://example.com/result')
            data['sheet_mappings'][0].update(marketplace='CA', source_order=1)
            store.save('seq-2', data)
            execution.claim_latest_run(store, data, 'run1')
            execution.atomic_json(store.root / 'fixed_result.json', {
                'spreadsheet_token': data['result']['spreadsheet_token'], 'url': 'https://example.com/result', 'period_id': 'seq-2'})
            fc = FakeFeishu()
            fc.inspect_weekly_registry = Mock(return_value={'records': []})
            fc.application_collaborators = Mock(return_value=['ou_manager', 'ou_viewer'])
            fc.send_post_message = Mock(return_value='message')
            fc.send_text_message = Mock(return_value='warning')
            def rename(*args):
                self.assertTrue(list(output.glob('daily_runs/*/run1_weekly_bundle.json')))
                raise RuntimeError('rename unavailable')
            fc.rename_spreadsheet = rename
            row = ReportRow(3, 'B000000001')
            plans = [{'mapping': data['sheet_mappings'][0], 'valid_rows': [row], 'invalid': [], 'rows': [row]}]
            cfg = {'weekly_registry_url': 'registry', 'feishu_allowed_hosts': ['example.com'],
                   'parser_rule_version': 'current', 'price_tolerance': '0.50',
                   'max_error_ratio_for_push': 1, 'html_archive_enabled': True,
                   'html_archive_required': True, 'html_server_enabled': True,
                   'feishu_manager_open_id': 'ou_manager'}
            args = SimpleNamespace(sheets='', asins='', limit=0, dry_run=False, fetch_only=False,
                no_headless=False, force_fetch=True, force_push=False, run_id='run1')
            def fetch(run_id, sheet, rows, run_cfg, **kw):
                self.assertFalse(run_cfg['html_archive_enabled'])
                self.assertFalse(run_cfg['html_archive_required'])
                self.assertFalse(run_cfg['html_server_enabled'])
                self.assertEqual(run_cfg['sheet_profiles'][sheet], 'CA')
                return [result('B000000001', archive=False)]
            with patch.object(main, 'OUTPUT_DIR', output), \
                 patch.object(main, 'select_for_scheduled_slot', return_value=SimpleNamespace(
                     period_id='seq-2', source_url='url', scheduled_slot='manual',
                     selection_mode='manual', row_number=0, pending_period_change=None)), \
                 patch.object(main, '_read_source_plan', return_value=plans), \
                 patch.object(main, 'make_run_id', return_value='run1'), \
                 patch.object(main, 'ensure_price_week', return_value=data), \
                 patch.object(main, 'sync_weekly_result_base', return_value={'written_rows': 1, 'base_rows_written': 1, 'blocked': [], 'failures': []}), \
                 patch.object(main, 'save_snapshot'), patch.object(main, 'export_results'), \
                 patch.object(main, 'summarize', return_value=0), \
                 patch.object(main, 'run_fetch', side_effect=fetch), \
                 patch('html_server.server_status', side_effect=AssertionError('must not call HTML')):
                main.weekly_daily_flow(fc, cfg, ['old-config-name'], args, Mock())
            self.assertEqual(fc.send_post_message.call_count, 2)
            report = json.loads(next(output.glob('daily_runs/*/run1_delivery.json')).read_text(encoding='utf-8'))
            self.assertEqual(report['written_rows'], 1)
            self.assertEqual(report['failures'][0]['stage'], 'rename')
            self.assertTrue(cfg['html_archive_enabled'])  # no mutation of optional HTML settings


if __name__ == '__main__':
    unittest.main()
