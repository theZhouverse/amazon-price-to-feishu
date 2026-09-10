"""Full audit regression matrix. No real cloud writes or browser navigation."""
import json
import tempfile
import threading
import unittest
import subprocess
import sys
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

import main
import cache
import runtime_state
from amazon.parser import parse_code, parse_coupon, parse_save, PromotionEvidenceError
from models import CrawlResult, PageStatus, ReportRow
from pricing import compute_result
from publication_guard import claim_latest_run
from runtime_state import atomic_json, exclusive_run, RunBusy
from sheet_io import read_rows
from weekly_result import base_fingerprint, _read_source_plan
import test_price_refresh as fixture
import test_review_regressions as browser_fixture
import test_weekly_result as base_fixture

PRICE = '<div id="corePrice_feature_div"><span class="a-offscreen">$100</span></div>'
COUPON = '<div class="ct-coupon-tile" aria-label="50% off coupon applied"></div>'
CODE = '<div class="a-alert-content">Save 40% at checkout</div>'
SAVE = '<span class="savingsPercentage">-30%</span>'

class PromotionBoundaryTests(unittest.TestCase):
    def test_runtime_css_hidden_annotation_is_respected(self):
        self.assertEqual(parse_coupon('<div data-price-audit-hidden="true">' + COUPON + '</div>'), (None, None, ''))

    def test_conflicting_save_in_one_container_rejected(self):
        with self.assertRaises(PromotionEvidenceError):
            parse_save(PRICE.replace('</div>', SAVE + '<span class="savingsPercentage">-50%</span></div>'))
    def test_hidden_recommendation_review_and_script_promotions_ignored(self):
        for attrs in ('id="recommendations"', 'data-hook="reviewRichContentContainer"', 'style="display:none"', 'class="aok-hidden"'):
            html = PRICE + f'<section {attrs}>' + COUPON + CODE + SAVE + '</section>'
            self.assertEqual(parse_coupon(html), (None, None, ''))
            self.assertEqual(parse_code(html), (None, ''))
            self.assertEqual(parse_save(html), (None, ''))
        self.assertEqual(parse_coupon('<script>' + COUPON + '</script>'), (None, None, ''))

    def test_save_cannot_leak_outside_main_container(self):
        self.assertEqual(parse_save(PRICE + SAVE), (None, ''))

    def test_adjacent_saving_does_not_change_coupon_amount(self):
        pct, amount, _ = parse_coupon(COUPON + '<div>Saving $80.00 on another item</div>')
        self.assertEqual(pct, Decimal('0.5'))
        self.assertIsNone(amount)

    def test_distinct_coupon_controls_not_combined(self):
        html = COUPON + '<div class="couponText">Save $20 with coupon</div>'
        with self.assertRaises(PromotionEvidenceError): parse_coupon(html)

    def test_conflicting_code_in_same_or_distinct_controls_rejected(self):
        for html in (CODE + '<div class="a-alert-content">Save 20% at checkout</div>',
                     '<div class="a-alert-content">Save 20% at checkout or Save 40% at checkout</div>'):
            with self.assertRaises(PromotionEvidenceError): parse_code(html)

    def test_coupon_conflict_in_same_control_rejected(self):
        with self.assertRaises(PromotionEvidenceError):
            parse_coupon('<div class="couponText">Apply 20% coupon or Apply 40% coupon</div>')

    def test_legitimate_controls_still_parse(self):
        self.assertEqual(parse_coupon(COUPON)[0], Decimal('0.5'))
        self.assertEqual(parse_code(CODE)[0], Decimal('0.4'))
        self.assertEqual(parse_save(PRICE.replace('</div>', SAVE + '</div>'))[0], Decimal('0.3'))

class CalculationTests(unittest.TestCase):
    def calculate(self, **kw):
        row = ReportRow(3, 'B000000001', normal_price=Decimal('100'), target_price=Decimal('100'))
        cr = CrawlResult(asin=row.asin, display_price=Decimal('100'), currency_code='USD', **kw)
        compute_result(row, cr, '0')
        return cr

    def test_coupon_cannot_produce_negative_price(self):
        cr = self.calculate(coupon_amount=Decimal('101'))
        self.assertEqual(cr.status, PageStatus.PARSE_ERROR)
        self.assertIsNone(cr.final_price)

    def test_invalid_percentages_and_nonfinite_values_rejected(self):
        for key, value in [('coupon_pct', '1.5'), ('code_pct', '-.1'), ('save_pct', 'NaN'), ('coupon_amount', 'Infinity')]:
            self.assertEqual(self.calculate(**{key: Decimal(value)}).status, PageStatus.PARSE_ERROR)

    def test_zero_tolerance_is_not_replaced_with_half_dollar(self):
        row = ReportRow(3, 'B000000001', normal_price=Decimal('100'), target_price=Decimal('99.99'))
        cr = CrawlResult(asin=row.asin, display_price=Decimal('100'), currency_code='USD')
        compute_result(row, cr, '0')
        self.assertTrue(cr.match.startswith('❌'))

class PageSnapshotTests(unittest.TestCase):
    def setup_browser(self):
        case = browser_fixture.PriceEvidenceTests()
        case.setUp()
        return case

    def test_late_redirect_blocks_wrong_product(self):
        c = self.setup_browser()
        tab = Mock()
        tab.run_js.side_effect = [
            {'url': 'https://www.amazon.ca/dp/B000000001', 'title': 'A'},
            {'url': 'https://www.amazon.ca/dp/B000000002', 'title': 'B', 'html': PRICE, 'postal': 'M5V3A8', 'asin': 'B000000002'}]
        with patch('amazon.crawler.time.sleep'):
            cr = c.b.fetch_once(tab, c.row, c.cfg)
        self.assertEqual(cr.status, PageStatus.IDENTITY_MISMATCH)
        self.assertIn('identity_mismatch', cr.error)

    def test_current_page_postal_is_rechecked(self):
        c = self.setup_browser()
        tab = Mock()
        tab.run_js.return_value = {'url': 'https://www.amazon.ca/dp/B000000001', 'title': 'A', 'html': PRICE, 'postal': 'M5V3A9', 'asin': 'B000000001'}
        with patch('amazon.crawler.time.sleep'):
            cr = c.b.fetch_once(tab, c.row, c.cfg)
        self.assertEqual(cr.status, PageStatus.CRAWL_ERROR)
        self.assertFalse(cr.location_verified)

    def test_full_wrong_canadian_postal_not_accepted_as_truncation(self):
        c = self.setup_browser()
        self.assertFalse(c.b._postal_matches('Toronto M5V 3A9'))
        self.assertTrue(c.b._postal_matches('Toronto M5V 3A…'))

    def test_identity_mismatch_is_blocked_but_not_counted_as_browser_failure(self):
        mismatch = CrawlResult(asin='B000000001', status=PageStatus.IDENTITY_MISMATCH,
                               error='identity_mismatch: 请求 B000000001，最终页面 B000000002')
        parse = CrawlResult(asin='B000000002', status=PageStatus.PARSE_ERROR)
        logger = Mock()
        self.assertEqual(main.summarize(
            {'CPD03': [mismatch, parse]}, {'max_error_ratio_for_push': 0.1}, logger), 0.5)

class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.case = fixture.PriceRefreshTest()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)

    def test_late_old_week_cannot_write_or_move_pointer(self):
        c = self.case
        fc = fixture.MemoryTable([fixture.row('B000000001')])
        old = base_fixture.WeeklyResultTest().manifest()
        old.update(period_id='seq-2', snapshot_run_id='old', base_sync_pending=True)
        old['result'].update(url='url', name='old')
        c.store.save('seq-2', old)
        newest = {**old, 'period_id': 'seq-3', 'snapshot_run_id': 'new'}
        c.store.save('seq-3', newest)
        claim_latest_run(c.store, newest, 'new')
        before = c.store.fixed_result()
        report = main._deliver_weekly_results(fc, c.store, old, 'old', {'PD03': [fixture.result('B000000001', run_id='old')], 'EMPTY': []}, c.cfg, c.root)
        self.assertEqual(report['written_rows'], 0)
        self.assertEqual(c.store.fixed_result(), before)
        self.assertFalse(fc.writes)

    def test_same_week_old_run_cannot_write(self):
        c = self.case
        fc = fixture.MemoryTable([fixture.row('B000000001')])
        old = base_fixture.WeeklyResultTest().manifest()
        old.update(period_id='seq-3', snapshot_run_id='old', base_sync_pending=True)
        old['result'].update(url='url', name='old')
        newest = {**old, 'snapshot_run_id': 'new'}
        c.store.save('seq-3', newest)
        claim_latest_run(c.store, newest, 'new')
        report = main._deliver_weekly_results(fc, c.store, old, 'old', {'PD03': [], 'EMPTY': []}, c.cfg, c.root)
        self.assertEqual(report['status'], 'partial')
        self.assertFalse(fc.writes)

    def test_verified_progress_survives_final_manifest_failure(self):
        c = self.case
        manifest = base_fixture.WeeklyResultTest().manifest()
        manifest.update(period_id='seq-3', snapshot_run_id='run1', base_sync_pending=True)
        c.store.save('seq-3', manifest)
        claim_latest_run(c.store, manifest, 'run1')
        def publish(*args, checkpoint, **kwargs):
            checkpoint({'run_id': 'run1', 'written_rows': 1, 'base_rows_written': 1,
                        'verified': {'PD03:B000000001': True}, 'blocked': [], 'failures': []})
            raise OSError('final manifest disk failure')
        with patch.object(main, 'sync_weekly_result_base', side_effect=publish), patch.object(main, '_rename_run_result'):
            report = main._deliver_weekly_results(Mock(), c.store, manifest, 'run1',
                {'PD03': [fixture.result('B000000001'), fixture.result('B000000002')], 'EMPTY': []}, c.cfg, c.root)
        self.assertEqual(report['written_rows'], 1)
        self.assertEqual([r['asin'] for r in report['blocked']], ['B000000002'])
        self.assertEqual(report['status'], 'partial')

    def test_snapshot_base_edit_between_fetch_and_publish_is_blocked(self):
        c = self.case
        fc = fixture.MemoryTable([fixture.row('B000000001')])
        manifest = base_fixture.WeeklyResultTest().manifest()
        manifest.update(period_id='seq-3', snapshot_run_id='run1', base_sync_pending=True)
        manifest['result'].update(url='url', name='name')
        plans = _read_source_plan(fc, 'snapshot', manifest['sheet_mappings'], c.cfg)
        manifest['source_fingerprints'] = {p['mapping']['result_sheet']: base_fingerprint(p) for p in plans}
        c.store.save('seq-3', manifest)
        claim_latest_run(c.store, manifest, 'run1')
        fc.source_values['s1'][2][10] = 999
        report = main._deliver_weekly_results(fc, c.store, manifest, 'run1', {'PD03': [fixture.result('B000000001')], 'EMPTY': []}, c.cfg, c.root)
        self.assertEqual(report['written_rows'], 0)
        self.assertFalse(fc.writes)

class RuntimeTests(unittest.TestCase):
    def test_source_header_detection_matches_discovery(self):
        from feishu import _detect_header_row, _resolve_cols
        header = [''] * 20 + [{'text': 'asin'}, {'text': 'SKU'}]
        self.assertEqual(_detect_header_row([[]] * 9 + [header]), 9)
        self.assertEqual(_resolve_cols(header, {'source_cols': {}})['asin'], 21)

    def test_second_process_cannot_enter_shared_lock(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'runtime.lock'
            script = 'from runtime_state import exclusive_run,RunBusy\nimport sys\ntry:\n with exclusive_run(sys.argv[1]): pass\nexcept RunBusy: sys.exit(75)'
            with exclusive_run(path):
                result = subprocess.run([sys.executable, '-c', script, str(path)], capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 75, result.stderr)

    def test_run_id_collision_uses_suffix(self):
        store = Mock()
        store.load.return_value = {'snapshot_run_id': 'same'}
        with patch.object(main, 'make_run_id', return_value='same'):
            self.assertTrue(main._price_run_id(store, 'seq-1', SimpleNamespace()).startswith('same_'))
    def test_parallel_atomic_writes_do_not_share_temporary_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / 'cache.json'
            barrier = threading.Barrier(2)
            mkstemp = runtime_state.tempfile.mkstemp
            names = []
            def synchronized(**kwargs):
                fd, name = mkstemp(**kwargs)
                names.append(name)
                barrier.wait(timeout=5)
                return fd, name
            with patch.object(runtime_state.tempfile, 'mkstemp', side_effect=synchronized):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    list(pool.map(lambda n: cache._atomic_write(target, {'n': n}), [1, 2]))
            self.assertEqual(len(set(names)), 2)
            self.assertIn(json.loads(target.read_text())['n'], (1, 2))
            self.assertFalse(list(Path(temp).glob('*.tmp')))

    def test_failed_atomic_write_preserves_previous_file_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / 'cache.json'
            atomic_json(target, {'old': True})
            with patch.object(runtime_state.os, 'replace', side_effect=OSError('disk')):
                with self.assertRaises(OSError): atomic_json(target, {'new': True})
            self.assertEqual(json.loads(target.read_text()), {'old': True})
            self.assertFalse(list(Path(temp).glob('*.tmp')))

    def test_shared_lock_excludes_second_entry_and_releases(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'runtime.lock'
            with exclusive_run(path):
                with self.assertRaises(RunBusy):
                    with exclusive_run(path): pass
            with exclusive_run(path): pass

    def test_main_does_not_dispatch_when_busy(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(main, 'OUTPUT_DIR', Path(temp)), patch.object(main, '_main_unlocked') as dispatch:
            with exclusive_run(Path(temp) / 'weekly_scheduler.lock'):
                with self.assertRaises(SystemExit) as exc: main.main()
            self.assertEqual(exc.exception.code, 75)
            dispatch.assert_not_called()

class FullReadAndNotificationTests(unittest.TestCase):
    def test_source_fields_beyond_column_o_are_read(self):
        from test_weekly_result import CFG
        fc = Mock()
        fc.query_sheets.return_value = [{'title': 'PD03', 'sheet_id': 'sid', 'grid_properties': {'row_count': 3, 'column_count': 26}}]
        headers = [''] * 19 + ['ASIN', 'SKU', '尺寸', '正常售价', '本周折扣形式', '本周折扣%', '目标成交价']
        values = [''] * 19 + ['B000000001', 'sku', 'L', 30, 'coupon', '10%', 27]
        fc.read_values.return_value = [headers, values]
        mapping = {'source_order': 1, 'source_sheet': 'PD03', 'source_sheet_id': 'sid', 'result_sheet': 'PD03', 'status': 'mapped', 'marketplace': 'US'}
        plan = _read_source_plan(fc, 'snapshot', [mapping], CFG)[0]
        self.assertEqual(plan['rows'][0].target_price, Decimal('27'))
        self.assertEqual(fc.read_values.call_args.args[-1], 'A1:Z3')
    def test_source_url_and_rich_link_are_not_silently_skipped(self):
        from feishu import read_source_rows
        from test_weekly_result import CFG, source, row
        for value in ('https://www.amazon.ca/dp/B000000001?th=1',
                      [{'type': 'url', 'text': 'link', 'link': 'https://www.amazon.ca/dp/B000000001'}]):
            values = source(row(value))
            rows, invalid = read_source_rows(values, {**CFG, 'source_marketplace': 'CA'})
            self.assertEqual([r.asin for r in rows], ['B000000001'])
            self.assertFalse(invalid)
            self.assertTrue(rows[0].source_product_url.startswith('https://www.amazon.ca/dp/B000000001'))
            self.assertEqual(rows[0].product_url, rows[0].source_product_url)

    def test_record_age_cannot_be_extended_by_rewriting_metadata(self):
        cfg = {'cache_max_age_hours': 12}
        with self.assertRaises(RuntimeError):
            cache.validate_record_ages([{'status': 'ok', 'timestamp': (datetime.now()-timedelta(hours=13)).isoformat()}], cfg)
        cache.validate_record_ages([{'status': 'ok', 'timestamp': datetime.now().isoformat()}], cfg)

    def test_read_after_2000_preserves_absolute_row_positions(self):
        fc = Mock()
        fc.read_values.side_effect = [[['first']], [['row2001'], ['row2002']]]
        values = read_rows(fc, 'token', 'sid', last='A', row_count=2002)
        self.assertEqual(len(values), 2002)
        self.assertEqual(values[2000], ['row2001'])
        self.assertEqual(fc.read_values.call_args.args[-1], 'A2001:A2002')

    def test_wide_read_limits_cells_per_request(self):
        fc = Mock()
        fc.read_values.return_value = []
        read_rows(fc, 'token', 'sid', last='CV', row_count=201)
        self.assertEqual([c.args[-1] for c in fc.read_values.call_args_list], ['A1:CV100', 'A101:CV200', 'A201:CV201'])

    def test_identical_notifications_are_checkpointed_and_not_resent(self):
        with tempfile.TemporaryDirectory() as temp:
            fc = Mock()
            fc.application_collaborators.return_value = ['manager', 'viewer']
            fc.send_post_message.return_value = 'message'
            out = Path(temp)
            text = 'Hi，有个任务完成请查收.\n运行编号：r\n写入行：1\n本地数据：private'
            cfg = {'feishu_manager_open_id': 'manager'}
            main._notify_run_collaborators(fc, cfg, Mock(), 'r', text, out)
            main._notify_run_collaborators(fc, cfg, Mock(), 'r', text, out)
            self.assertEqual(fc.send_post_message.call_count, 2)
            ledger = json.loads((out / 'notification_receipts/r.json').read_text())
            self.assertEqual(len(next(iter(ledger.values()))['sent']), 2)

    def test_failed_recipient_only_is_retried(self):
        with tempfile.TemporaryDirectory() as temp:
            fc = Mock()
            fc.application_collaborators.return_value = ['manager', 'viewer']
            fc.send_post_message.side_effect = ['ok', RuntimeError('unavailable'), 'ok2']
            fc.send_text_message.return_value = 'warning'
            text = 'Hi，有个任务完成请查收.\n写入行：1'
            for _ in range(2): main._notify_run_collaborators(fc, {'feishu_manager_open_id': 'manager'}, Mock(), 'r', text, Path(temp))
            self.assertEqual([c.args[0] for c in fc.send_post_message.call_args_list], ['manager', 'viewer', 'viewer'])

    def test_manager_only_notification_does_not_discover_collaborators(self):
        with tempfile.TemporaryDirectory() as temp:
            fc = Mock()
            fc.application_collaborators.return_value = ['manager', 'viewer']
            fc.send_post_message.return_value = 'message'
            main._notify_run_collaborators(
                fc, {'feishu_manager_open_id': 'manager'}, Mock(), 'r',
                'Hi，有个任务完成请查收.', Path(temp), manager_only=True)
            fc.application_collaborators.assert_not_called()
            self.assertEqual([c.args[0] for c in fc.send_post_message.call_args_list], ['manager'])

if __name__ == '__main__': unittest.main()
