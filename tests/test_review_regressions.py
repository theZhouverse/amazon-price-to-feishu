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
from frontend_checks import FRONTEND_CHECK_RULE_VERSION
from models import ReportRow, PageStatus, CrawlResult
from product_links import MARKETPLACES
import test_price_refresh as refresh_fixture
from test_price_refresh import MemoryTable, row, result

class PriceEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.b = object.__new__(AmazonBrowser)
        self.b.profile = MARKETPLACES['CA']
        self.b.marketplace = 'CA'
        self.b.location_mode = 'postal'
        self.b.postal_code = 'M5V3A8'
        self.b.location_verified = True
        self.b._sleep = Mock()
        self.row = ReportRow(3, 'B000000001', marketplace='CA')
        self.cfg = {'page_timeout': 5, 'price_wait_timeout': 1,
                    'ambiguous_price_ratio': '0.05', 'per_asin_timeout': 10,
                    'retry': 1, 'risk_cooldown_min': 60, 'risk_cooldown_max': 180}

    def fetch(self, html, url='https://www.amazon.ca/dp/B000000001?th=1', title='Product',
              product_shell=None):
        self.tab = Mock()
        sample = {'url': url, 'title': title, 'html': html, 'asin': self.row.asin,
                  'postal': self.b.postal_code}
        if product_shell is not None:
            sample['product_shell'] = product_shell
        self.tab.run_js.side_effect = lambda script, **kw: sample
        with patch('amazon.crawler.time.sleep'):
            return self.b.fetch_once(self.tab, self.row, self.cfg)

    def price(self, value='$19.99'):
        return f'<div id="corePrice_feature_div"><span class="a-offscreen">{value}</span></div>'

    def test_recommendation_only_not_a_main_price(self):
        html = '<div id="recommendations"><span class="a-offscreen">$19.99</span></div>'
        self.assertIsNone(select_main_price(parse_main_price(html), '0.05')[0])
        self.assertEqual(self.fetch(html).status, PageStatus.PARSE_ERROR)

    def test_product_wait_does_not_accept_recommendation_price(self):
        self.fetch(self.price())
        selector = self.tab.wait.ele_displayed.call_args.args[0]
        self.assertIn('#corePrice_feature_div', selector)
        self.assertIn('#productTitle', selector)
        self.assertNotIn('css:.a-price, css:.priceToPay', selector)

    def test_incomplete_product_shell_is_explicit_technical_error(self):
        html = '<div id="recommendations"><span class="a-offscreen">$19.99</span></div>'
        shell = {'title': False, 'main_image': False, 'center': True,
                 'buybox': False, 'price': False, 'availability': False}
        cr = self.fetch(html, product_shell=shell)
        self.assertEqual(cr.status, PageStatus.CRAWL_ERROR)
        self.assertIn('incomplete_product_page', cr.error)
        self.assertIn("'price': False", cr.error)

    def test_incomplete_page_retry_rebuilds_once_without_risk_cooldown(self):
        first = CrawlResult(asin=self.row.asin, status=PageStatus.CRAWL_ERROR,
                            error='incomplete_product_page: shell')
        second = CrawlResult(asin=self.row.asin, status=PageStatus.CRAWL_ERROR,
                             error='incomplete_product_page: shell')
        self.b.fetch_once = Mock(side_effect=[first, second])
        self.b.rebuild = Mock(return_value=Mock())
        cfg = {**self.cfg, 'retry': 2}
        with patch('amazon.crawler.random.uniform', return_value=2.0):
            result, _ = self.b.fetch_with_retry(Mock(), self.row, cfg)
        self.assertEqual(result.status, PageStatus.CRAWL_ERROR)
        self.assertEqual(self.b.fetch_once.call_count, 2)
        self.b.rebuild.assert_called_once()
        self.b._sleep.assert_called_once_with(2.0)

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

    def test_postal_setup_waits_for_slow_address_modal(self):
        class Element:
            def __init__(self, text=''):
                self.text = text
                self.inputs = []
                self.clicked = 0

            def input(self, value, clear=False):
                self.inputs.append((value, clear))

            def click(self, **kwargs):
                self.clicked += 1

        trigger = Element('Deliver to United Kingdom')
        field = Element()
        button = Element('Done')
        ingress = Element('M5V3A8')
        page = Mock()
        page.ele.side_effect = lambda selector: (
            (ingress if button.clicked else Element('United Kingdom'))
                if selector == 'css:#glow-ingress-line2' else
            trigger if selector == 'css:#nav-global-location-popover-link' else
            field if selector == 'css:#GLUXZipUpdateInput' and page.ele.call_count >= 7 else
            button if selector in ('css:#GLUXZipUpdate', 'css:#GLUXZipUpdate-announce') and page.ele.call_count >= 9 else
            None)
        self.b.page = page
        self.b._sleep = Mock()
        self.assertTrue(self.b._set_postal_code())
        self.assertEqual(field.inputs, [('M5V3A8', True)])
        self.assertGreaterEqual(self.b._sleep.call_count, 2)

    def test_us_proxy_setup_does_not_inject_legacy_zip(self):
        """US 位置由代理出口决定，不写 90210 cookie，也不打开地址弹窗。"""
        self.b.profile = MARKETPLACES['US']
        self.b.marketplace = 'US'
        self.b.location_mode = 'proxy'
        self.b.postal_code = ''
        self.b.page = Mock()
        self.b._sleep = Mock()
        self.b._set_postal_code = Mock(side_effect=AssertionError('proxy mode must not set postal'))
        self.assertTrue(self.b.setup(strict_location=True))
        self.assertTrue(self.b.location_verified)
        self.assertEqual(self.b.location_verification_method, 'proxy_egress')
        self.b.page.run_js.assert_not_called()

    def test_ca_direct_no_postal_setup_skips_address_context(self):
        """CA 无邮编模式只确认 amazon.ca 首页，不打开地址弹窗。"""
        self.b.profile = MARKETPLACES['CA']
        self.b.marketplace = 'CA'
        self.b.location_mode = 'direct_no_postal'
        self.b.postal_code = ''
        self.b.page = Mock()
        self.b._sleep = Mock()
        self.b._set_postal_code = Mock(side_effect=AssertionError(
            'direct_no_postal must not set postal'))
        self.assertTrue(self.b.setup(strict_location=True))
        self.assertFalse(self.b.location_verified)
        self.assertTrue(self.b.location_context_ready)
        self.assertEqual(self.b.location_verification_method, 'direct_no_postal')
        self.b.page.run_js.assert_not_called()

    def test_ca_direct_no_postal_accepts_current_cad_price_without_postal(self):
        self.b.profile = MARKETPLACES['CA']
        self.b.marketplace = 'CA'
        self.b.location_mode = 'direct_no_postal'
        self.b.postal_code = ''
        self.b.location_verified = False
        self.b.location_context_ready = True
        self.b.location_verification_method = 'direct_no_postal'
        cr = self.fetch(self.price('$19.99'),
                        url='https://www.amazon.ca/dp/B000000001?th=1')
        self.assertEqual(cr.status, PageStatus.OK)
        self.assertEqual(cr.currency_code, 'CAD')
        self.assertFalse(cr.location_verified)
        self.assertTrue(cr.location_context_ready)
        self.assertEqual(cr.location_verification_method, 'direct_no_postal')

    def test_setup_navigation_false_but_target_host_reached_continues(self):
        """Chrome 慢加载时 get=False 不应误阻断已到达的 Amazon 首页。"""
        self.b.profile = MARKETPLACES['US']
        self.b.marketplace = 'US'
        self.b.location_mode = 'proxy'
        self.b.postal_code = ''
        self.b.page = Mock()
        self.b.page.get.return_value = False
        self.b.page.url = 'https://www.amazon.com/?language=en_US'
        self.b._sleep = Mock()
        self.assertTrue(self.b.setup(strict_location=True))
        self.assertTrue(self.b.location_verified)

    def test_setup_navigation_false_wrong_host_fails_closed(self):
        """get=False 且仍停在其它域名时必须阻断，防止复用旧会话。"""
        self.b.profile = MARKETPLACES['US']
        self.b.marketplace = 'US'
        self.b.location_mode = 'proxy'
        self.b.postal_code = ''
        self.b.page = Mock()
        self.b.page.get.return_value = False
        self.b.page.url = 'https://www.amazon.ca/'
        self.assertFalse(self.b.setup(strict_location=True))
        self.assertIn('navigation_failed', self.b.location_error)

    @patch('amazon.crawler.ChromiumPage')
    def test_chrome_152_cdp_compatibility_flags_are_present(self, chromium_page):
        """浏览器启动必须放行本机CDP并规避已知GPU/沙箱启动崩溃。"""
        chromium_page.return_value.new_tab.return_value = Mock()
        browser = AmazonBrowser(headless=True, marketplace='US',
                                location_mode='proxy', tabs=1)
        options = chromium_page.call_args.args[0]
        self.assertIn('--remote-allow-origins=*', options.arguments)
        self.assertIn('--disable-gpu', options.arguments)
        self.assertIn('--no-sandbox', options.arguments)
        self.assertNotIn('--in-process-gpu', options.arguments)
        self.assertTrue(options.is_auto_port)
        self.assertEqual(browser.postal_code, '')

    def test_browser_log_redacts_proxy_argument(self):
        args = ['--proxy-server=user:secret@127.0.0.1:7897',
                '--user-data-dir=C:\\private\\profile']
        safe = AmazonBrowser._safe_args(args)
        self.assertNotIn('secret', safe)
        self.assertNotIn('C:\\private', safe)
        self.assertIn('--proxy-server=<configured>', safe)
        self.assertIn('--user-data-dir=<managed>', safe)

    def test_ca_setup_rejects_homepage_redirect_to_us_domain(self):
        """CA 初始化若首页先跳到 amazon.com，不得继续套用CAD/CA规则。"""
        self.b.profile = MARKETPLACES['CA']
        self.b.marketplace = 'CA'
        self.b.location_mode = 'postal'
        self.b.postal_code = 'M5V 3A8'
        self.b.page = Mock()
        self.b.page.url = 'https://www.amazon.com/'
        self.assertFalse(self.b.setup(strict_location=True))
        self.assertIn('marketplace_mismatch', self.b.location_error)
        self.b.page.run_js.assert_not_called()

    def test_us_proxy_fetch_does_not_require_page_postal_text(self):
        """US proxy 模式即使导航栏没有邮编文本也允许继续解析商品。"""
        self.b.profile = MARKETPLACES['US']
        self.b.marketplace = 'US'
        self.b.location_mode = 'proxy'
        self.b.postal_code = ''
        self.b.location_verified = True
        cr = self.fetch(self.price('$19.99'),
                        url='https://www.amazon.com/dp/B000000001')
        self.assertEqual(cr.status, PageStatus.OK)
        self.assertTrue(cr.location_verified)

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

    def test_navigation_false_matching_asin_continues_to_dom_gates(self):
        """get=False 但 URL 已切到请求 ASIN 时，继续由页面门禁决定结果。"""
        self.tab = Mock()
        self.tab.get.return_value = False
        self.tab.url = 'https://www.amazon.ca/dp/B000000001?th=1'
        cr = self.fetch_once_with_tab(self.tab, self.price('CA$19.99'))
        self.assertEqual(cr.status, PageStatus.OK)

    def test_doc_loaded_false_matching_asin_continues_to_dom_gates(self):
        """doc_loaded=False 可是 URL 已是当前 ASIN，不能误读上一页或直接丢弃。"""
        self.tab = Mock()
        self.tab.get.return_value = True
        self.tab.url = 'https://www.amazon.ca/dp/B000000001?th=1'
        self.tab.wait.doc_loaded.return_value = False
        cr = self.fetch_once_with_tab(self.tab, self.price('CA$19.99'))
        self.assertEqual(cr.status, PageStatus.OK)

    def test_navigation_false_different_asin_fails_closed(self):
        """URL 指向上一条 ASIN 时，不能进入 run_js/价格解析。"""
        self.tab = Mock()
        self.tab.get.return_value = False
        self.tab.url = 'https://www.amazon.ca/dp/B000000002?th=1'
        cr = self.fetch_once_with_tab(self.tab, self.price('CA$19.99'))
        self.assertEqual(cr.status, PageStatus.CRAWL_ERROR)
        self.assertIn('navigation_failed', cr.error)
        self.tab.run_js.assert_not_called()

    def test_chrome_error_page_is_navigation_failure_not_identity_mismatch(self):
        """Chrome 内置错误页必须记录为导航失败，便于定位网络/CDP问题。"""
        cr = self.fetch('', url='chrome-error://chromewebdata/', title='www.amazon.com')
        self.assertEqual(cr.status, PageStatus.CRAWL_ERROR)
        self.assertIn('Chrome错误页', cr.error)

    def fetch_once_with_tab(self, tab, html):
        sample = {'url': tab.url, 'title': 'Product', 'html': html,
                  'asin': self.row.asin, 'postal': self.b.postal_code}
        tab.run_js.side_effect = lambda script, **kw: sample
        with patch('amazon.crawler.time.sleep'):
            return self.b.fetch_once(tab, self.row, self.cfg)

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

    def test_schema3_bundle_with_stale_frontend_rules_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            meta = {'period_id': 'seq-3', 'snapshot_spreadsheet_token': 'snapshot',
                    'result_spreadsheet_token': 'result'}
            manifest = {'period_id': 'seq-3',
                        'snapshot': {'spreadsheet_token': 'snapshot'},
                        'result': {'spreadsheet_token': 'result'}}
            cfg = {'html_archive_enabled': False, 'parser_rule_version': 'current',
                   'price_tolerance': '0.50', 'cache_max_age_hours': 12}
            main.atomic_json(root / 'snapshots/run1/source.json', {'source_meta': meta})
            bundle = {**meta, 'schema_version': 3, 'run_id': 'run1',
                      'created_at': datetime.now().isoformat(),
                      'parser_rule_version': 'current', 'price_tolerance': '0.50',
                      'frontend_check_rule_version': '2026-09-10-v13',
                      'sheets': {'PD03': [result('B000000001').as_dict()]}}
            path = root / 'daily_runs/test/run1_weekly_bundle.json'
            with patch.object(main, 'OUTPUT_DIR', root):
                main.atomic_json(path, bundle)
                with self.assertRaisesRegex(RuntimeError, '前端检查规则版本'):
                    main._load_weekly_push_results('run1', ['PD03'], manifest, cfg)

                bundle['frontend_check_rule_version'] = FRONTEND_CHECK_RULE_VERSION
                main.atomic_json(path, bundle)
                self.assertEqual(
                    len(main._load_weekly_push_results('run1', ['PD03'], manifest, cfg)['PD03']),
                    1)

if __name__ == '__main__':
    unittest.main()
