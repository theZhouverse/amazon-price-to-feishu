"""Regression coverage for the 2026-08-26 audit; no live browser/cloud calls."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

import main
from amazon.crawler import AmazonBrowser
from amazon.parser import parse_main_price, select_main_price
from cache import SCHEMA_VERSION, validate_recovery_metadata
from models import ReportRow, PageStatus, CrawlResult
from product_links import MARKETPLACES
import test_price_refresh as refresh_fixture
from test_price_refresh import MemoryTable, row, result

class PriceEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.b = object.__new__(AmazonBrowser)
        self.b.profile = MARKETPLACES['CA']
        self.b.marketplace = 'CA'
        self.b.postal_code = 'M5V3A8'
        self.b.location_verified = True
        self.b._sleep = Mock()
        self.row = ReportRow(3, 'B000000001', marketplace='CA')
        self.cfg = {'page_timeout': 5, 'price_wait_timeout': 1,
                    'ambiguous_price_ratio': '0.05', 'per_asin_timeout': 10,
                    'retry': 1, 'risk_cooldown_min': 60, 'risk_cooldown_max': 180}

    def fetch(self, html, url='https://www.amazon.ca/dp/B000000001?th=1', title='Product'):
        self.tab = Mock()
        self.tab.run_js.side_effect = lambda script, **kw: (
            {'url': url, 'title': title, 'html': html, 'asin': self.row.asin,
             'postal': self.b.postal_code})
        with patch('amazon.crawler.time.sleep'):
            return self.b.fetch_once(self.tab, self.row, self.cfg)

    def price(self, value='$19.99'):
        return f'<div id="corePrice_feature_div"><span class="a-offscreen">{value}</span></div>'

    def test_recommendation_only_not_a_main_price(self):
        html = '<div id="recommendations"><span class="a-offscreen">$19.99</span></div>'
        self.assertIsNone(select_main_price(parse_main_price(html), '0.05')[0])
        self.assertEqual(self.fetch(html).status, PageStatus.PARSE_ERROR)

    def test_empty_main_container_cannot_leak_into_following_price(self):
        html = '<div id="corePrice_feature_div"></div><span class="a-offscreen">$19.99</span>'
        self.assertIsNone(select_main_price(parse_main_price(html), '0.05')[0])

    def test_hidden_recommendation_price_to_pay_ignored(self):
        for attrs in ('id="recommendations"', 'style="display:none"', 'hidden'):
            html = f'<section {attrs}><span class="priceToPay"><span class="a-offscreen">$99</span></span></section>'
            self.assertIsNone(select_main_price(parse_main_price(html), '0.05')[0])

    def test_script_and_recommended_unavailability_are_not_evidence(self):
        html = '<div id="corePrice_feature_div"><script>"<span class=\"a-offscreen\">$99</span>"</script></div>'
        self.assertIsNone(select_main_price(parse_main_price(html), '0.05')[0])
        self.assertEqual(self.fetch('<div id="recommendations"><div id="availability">Currently unavailable</div></div>').status, PageStatus.PARSE_ERROR)

    def test_ca_correct_price_and_query_parameter(self):
        for currency in ('$', 'CA$', 'CAD '):
            cr = self.fetch(self.price(currency + '19.99'))
            self.assertEqual(cr.status, PageStatus.OK)
            self.assertEqual(cr.currency_code, 'CAD')
            self.assertEqual(cr.display_price, Decimal('19.99'))

    def test_us_price_on_ca_is_blocked(self):
        cr = self.fetch(self.price('US$19.99'))
        self.assertEqual(cr.status, PageStatus.CURRENCY_ERROR)
        self.assertIsNone(cr.display_price)

    def test_us_domain_and_currency(self):
        self.b.profile, self.b.marketplace = MARKETPLACES['US'], 'US'
        self.row.marketplace = 'US'
        cr = self.fetch(self.price(), url='https://www.amazon.com/dp/B000000001')
        self.assertEqual((cr.status, cr.currency_code), (PageStatus.OK, 'USD'))

    def test_currency_unknown_and_wrong_domain_blocked(self):
        self.assertEqual(self.fetch(self.price('19.99')).status, PageStatus.CURRENCY_ERROR)
        self.assertEqual(self.fetch(self.price(), url='https://www.amazon.com/dp/B000000001').status, PageStatus.CURRENCY_ERROR)

    def test_failed_postal_setup_and_fetch_both_block(self):
        self.b.page = Mock()
        self.b._set_postal_code = lambda: False
        self.assertFalse(self.b.setup())
        self.assertEqual(self.fetch(self.price()).status, PageStatus.CRAWL_ERROR)

    def test_postal_setup_retries_transient_address_component(self):
        self.b.page = Mock()
        self.b._set_postal_code = Mock(side_effect=[False, True])
        self.assertTrue(self.b.setup())
        self.assertEqual(self.b._set_postal_code.call_count, 2)

    def test_conflict_is_parse_error_not_sold_out(self):
        html = self.price('$10') + '<div id="buybox"><span class="a-offscreen">$20</span></div>'
        self.assertEqual(self.fetch(html).status, PageStatus.PARSE_ERROR)

    def test_used_offer_does_not_conflict_with_new_item_price(self):
        html = self.price('$37.99') + '<div data-csa-c-buying-option-type="USED">' + self.price('$36.09') + '</div>'
        cr = self.fetch(html)
        self.assertEqual(cr.status, PageStatus.OK)
        self.assertEqual(cr.display_price, Decimal('37.99'))

    def test_only_explicit_unavailability_is_sold_out(self):
        self.assertEqual(self.fetch('<div id="availability">Currently unavailable</div>').status, PageStatus.SOLD_OUT)
        self.assertEqual(self.fetch('<body></body>').status, PageStatus.PARSE_ERROR)

    def test_404_still_precedes_identity(self):
        self.assertEqual(self.fetch('', url='https://www.amazon.ca/dp/B000000002', title='Page Not Found').status, PageStatus.PAGE_NOT_FOUND)

    def test_over_budget_success_is_rejected(self):
        self.b.fetch_once = Mock(return_value=CrawlResult(asin=self.row.asin, display_price=Decimal('10')))
        with patch('amazon.crawler.time.monotonic', side_effect=[0, 0, 11]):
            cr, _ = self.b.fetch_with_retry(Mock(), self.row, self.cfg)
        self.assertEqual(cr.status, PageStatus.CRAWL_ERROR)
        self.assertIsNone(cr.display_price)
        self.assertIn('deadline_exceeded', cr.error)

    def test_navigation_timeout_is_clipped_to_remaining_budget(self):
        self.cfg['_deadline'] = 2
        with patch('amazon.crawler.time.monotonic', return_value=0):
            self.fetch(self.price())
        self.assertEqual(self.tab.get.call_args.kwargs['timeout'], 2)
        self.assertEqual(self.tab.get.call_args.kwargs['retry'], 0)

    def test_failed_navigation_does_not_read_stale_tab(self):
        self.tab = Mock()
        self.tab.get.return_value = False
        cr = self.b.fetch_once(self.tab, self.row, self.cfg)
        self.assertEqual(cr.status, PageStatus.CRAWL_ERROR)
        self.assertIn('navigation_failed', cr.error)
        self.tab.run_js.assert_not_called()

    def test_doc_loaded_timeout_does_not_read_stale_tab(self):
        self.tab = Mock()
        self.tab.get.return_value = True
        self.tab.wait.doc_loaded.side_effect = TimeoutError('not ready')
        cr = self.b.fetch_once(self.tab, self.row, self.cfg)
        self.assertEqual(cr.status, PageStatus.CRAWL_ERROR)
        self.assertIn('navigation_timeout', cr.error)
        self.tab.run_js.assert_not_called()

class RecoveryTests(unittest.TestCase):
    def test_manual_menu_uses_weekly_entry_and_readonly_minimal_sample(self):
        menu = (Path(main.__file__).resolve().parents[1] / '启动中心.bat').read_text(encoding='utf-8')
        self.assertIn('--weekly-run --sheets PD03 --limit 1 --dry-run', menu)
        self.assertIn('--weekly-run --confirm --force-fetch', menu)
        self.assertNotIn('08:00', menu)

    def test_new_sheet_transient_failure_can_resume(self):
        case = refresh_fixture.PriceRefreshTest()
        case.setUp()
        self.addCleanup(case.doCleanups)
        fc = MemoryTable([row('B000000001')])
        del fc.result_sheets['PD03']
        def add(token, title, index):
            fc.result_sheets[title] = 'new'
            fc.grids['new'] = []
            return 'new'
        fc.add_sheet = add
        original = fc.backup_target_sheet
        fc.backup_target_sheet = Mock(side_effect=[RuntimeError('transient'), None])
        results = {'PD03': [result('B000000001')], 'EMPTY': []}
        first = case.publish(fc, results)
        self.assertEqual(first['status'], 'partial')
        fc.backup_target_sheet = original
        manifest = case.store.load('seq-3')
        self.assertEqual(manifest['pending_result_sheets']['PD03'], 'new')
        with patch.object(main, '_rename_run_result'):
            second = main._deliver_weekly_results(fc, case.store, manifest, 'run1', results, case.cfg, case.root)
        self.assertEqual(second['status'], 'complete')
        self.assertEqual(second['written_rows'], 1)

    def test_unknown_empty_sheet_still_blocked(self):
        case = refresh_fixture.PriceRefreshTest()
        case.setUp()
        self.addCleanup(case.doCleanups)
        fc = MemoryTable([row('B000000001')])
        fc.grids['r1'] = []
        report = case.publish(fc, {'PD03': [result('B000000001')], 'EMPTY': []})
        self.assertEqual(report['status'], 'partial')
        self.assertFalse(fc.writes)

    def test_recovery_metadata_rejects_stale_rules_tolerance_and_clock(self):
        cfg = {'parser_rule_version': 'new', 'price_tolerance': '0.50', 'cache_max_age_hours': 12}
        meta = {**cfg, 'created_at': datetime.now().isoformat()}
        validate_recovery_metadata(meta, cfg)
        for change in ({'parser_rule_version': 'old'}, {'price_tolerance': '1'},
                       {'created_at': (datetime.now() - timedelta(hours=13)).isoformat()},
                       {'created_at': (datetime.now() + timedelta(hours=1)).isoformat()}, {'created_at': 'bad'}):
            with self.assertRaises(RuntimeError):
                validate_recovery_metadata({**meta, **change}, cfg)

    def test_actual_bundle_loader_rejects_old_schema_and_stale_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            meta = {'period_id': 'seq-3', 'snapshot_spreadsheet_token': 'snapshot', 'result_spreadsheet_token': 'result'}
            manifest = {'period_id': 'seq-3', 'snapshot': {'spreadsheet_token': 'snapshot'}, 'result': {'spreadsheet_token': 'result'}}
            cfg = {'html_archive_enabled': False, 'parser_rule_version': 'current', 'price_tolerance': '0.50', 'cache_max_age_hours': 12}
            main.atomic_json(root / 'snapshots/run1/source.json', {'source_meta': meta})
            bundle = {**meta, 'schema_version': 2, 'run_id': 'run1', 'created_at': datetime.now().isoformat(),
                      'parser_rule_version': 'current', 'price_tolerance': '0.50', 'sheets': {'PD03': [result('B000000001').as_dict()]}}
            path = root / 'daily_runs/test/run1_weekly_bundle.json'
            with patch.object(main, 'OUTPUT_DIR', root):
                main.atomic_json(path, bundle)
                self.assertEqual(len(main._load_weekly_push_results('run1', ['PD03'], manifest, cfg)['PD03']), 1)
                for change in ({'schema_version': 1}, {'parser_rule_version': 'old'}, {'created_at': '2000-01-01T00:00:00'}):
                    main.atomic_json(path, {**bundle, **change})
                    with self.assertRaises(RuntimeError):
                        main._load_weekly_push_results('run1', ['PD03'], manifest, cfg)

if __name__ == '__main__':
    unittest.main()
