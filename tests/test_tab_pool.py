# -*- coding: utf-8 -*-
"""单浏览器多 tab 池测试（不启动真实浏览器）。"""
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from amazon.crawler import AmazonBrowser
from models import CrawlResult, PageStatus


class TestTabPool(unittest.TestCase):
    def _browser(self):
        b = AmazonBrowser.__new__(AmazonBrowser)
        b.page = mock.MagicMock()
        b._lock = threading.Lock()
        b._free = []
        b._live = set()
        return b

    def test_created_tab_is_not_free_until_release(self):
        b = self._browser()
        tab = mock.MagicMock()
        b.page.new_tab.return_value = tab

        acquired = b.acquire()
        self.assertIs(acquired, tab)
        self.assertEqual(b._free, [])

        b.release(acquired)
        self.assertEqual(b._free, [tab])

    def test_rebuilt_tab_is_not_exposed_to_other_worker(self):
        b = self._browser()
        old = mock.MagicMock()
        new = mock.MagicMock()
        b._live.add(id(old))
        b.page.new_tab.return_value = new

        rebuilt = b.rebuild(old)
        self.assertIs(rebuilt, new)
        self.assertNotIn(old, b._free)
        self.assertNotIn(new, b._free)
        self.assertNotIn(id(old), b._live)
        self.assertIn(id(new), b._live)

        b.release(rebuilt)
        self.assertEqual(b._free, [new])

    def test_two_workers_never_acquire_same_tab(self):
        b = self._browser()
        t1, t2 = mock.MagicMock(), mock.MagicMock()
        b._free = [t1, t2]
        b._live = {id(t1), id(t2)}

        a = b.acquire()
        c = b.acquire()
        self.assertIsNot(a, c)
        self.assertEqual(b._free, [])

    def test_risk_cooldown_and_post_archive_delay_ranges(self):
        b = self._browser()
        b._sleep = mock.Mock()
        cfg = {'post_archive_delay_min': 1.0, 'post_archive_delay_max': 3.0}
        with mock.patch('amazon.crawler.random.uniform', return_value=2.0):
            self.assertEqual(b.wait_after_archive(cfg, archive_validated=True), 2.0)
        b._sleep.assert_called_once_with(2.0)
        cr = CrawlResult(asin='B0ABCDEF12', status=PageStatus.CRAWL_ERROR,
                         error='risk_503: unavailable')
        self.assertTrue(b.is_risk_result(cr))

    def test_risk_retry_cools_down_and_stays_inside_deadline(self):
        b = self._browser()
        b._sleep = mock.Mock()
        b.fetch_once = mock.Mock(side_effect=[
            CrawlResult(asin='B0ABCDEF12', status=PageStatus.CRAWL_ERROR,
                        error='captcha: challenge'),
            CrawlResult(asin='B0ABCDEF12', status=PageStatus.OK),
        ])
        rebuilt = mock.MagicMock()
        b.rebuild = mock.Mock(return_value=rebuilt)
        cfg = {'per_asin_timeout': 70, 'risk_cooldown_min': 60,
               'risk_cooldown_max': 180, 'retry': 1, 'page_timeout': 5}
        row = mock.MagicMock(asin='B0ABCDEF12')
        tab = mock.MagicMock()
        with mock.patch('amazon.crawler.time.monotonic', return_value=0), \
                mock.patch('amazon.crawler.random.uniform', return_value=90):
            result, returned_tab = b.fetch_with_retry(tab, row, cfg)
        self.assertEqual(result.status, PageStatus.OK)
        self.assertEqual(returned_tab, rebuilt)
        b.rebuild.assert_called_once_with(tab)
        # 随机值 90 秒，但 deadline 只剩 70 秒，因此等待必须被总预算截断。
        b._sleep.assert_called_once_with(70)

    def test_profile_builds_ca_url_without_tracking(self):
        b = self._browser()
        from product_links import MARKETPLACES
        b.profile = MARKETPLACES['CA']
        b.marketplace = 'CA'
        b.location_verified = True
        b._sleep = mock.Mock()
        row = mock.MagicMock(asin='B0ABCDEF12', h_type='', product_url='')
        tab = mock.MagicMock()
        tab.url = 'https://www.amazon.ca/dp/B0ABCDEF12'
        tab.title = 'Robot Check'
        tab.run_js.return_value = {'url': tab.url, 'title': tab.title}
        cfg = {'page_timeout': 5, 'price_wait_timeout': 1,
               'ambiguous_price_ratio': '0.05'}
        result = b.fetch_once(tab, row, cfg)
        self.assertEqual(result.product_url, 'https://www.amazon.ca/dp/B0ABCDEF12')
        self.assertEqual(result.currency_code, 'CAD')
        self.assertEqual(result.error, 'captcha: 机器人验证页')

    def test_redirect_to_different_asin_is_blocked_before_price_parse(self):
        b = self._browser()
        from product_links import MARKETPLACES
        b.profile = MARKETPLACES['CA']
        b.marketplace = 'CA'
        b.location_verified = True
        row = mock.MagicMock(asin='B0ABCDEF12', h_type='', product_url='')
        tab = mock.MagicMock()
        tab.url = 'https://www.amazon.ca/dp/B0ZZZZZZ99?th=1'
        tab.title = 'A different product'
        tab.run_js.return_value = {'url': tab.url, 'title': tab.title}
        cfg = {'page_timeout': 5, 'price_wait_timeout': 1,
               'ambiguous_price_ratio': '0.05'}
        result = b.fetch_once(tab, row, cfg)
        self.assertEqual(result.status, PageStatus.IDENTITY_MISMATCH)
        self.assertIn('identity_mismatch', result.error)
        tab.wait.ele_displayed.assert_not_called()

    def test_explicit_not_found_precedes_redirect_identity_check(self):
        b = self._browser()
        from product_links import MARKETPLACES
        b.profile = MARKETPLACES['CA']
        b.marketplace = 'CA'
        b.location_verified = True
        row = mock.MagicMock(asin='B0ABCDEF12', h_type='', product_url='')
        tab = mock.MagicMock()
        tab.url = 'https://www.amazon.ca/dp/B0ZZZZZZ99?th=1'
        tab.title = 'Amazon.ca: Page Not Found'
        tab.run_js.return_value = {'url': tab.url, 'title': tab.title}
        cfg = {'page_timeout': 5, 'price_wait_timeout': 1,
               'ambiguous_price_ratio': '0.05'}
        result = b.fetch_once(tab, row, cfg)
        self.assertEqual(result.status, PageStatus.PAGE_NOT_FOUND)
        self.assertEqual(result.error, '')

    def test_ca_postal_is_split_into_two_fields(self):
        b = self._browser()
        b.postal_code = 'M5V 3A8'
        b.marketplace = 'CA'
        b.location_verification_method = ''
        b.location_error = ''
        b._sleep = mock.Mock()
        initial_ingress = mock.MagicMock(text='Select your address')
        final_ingress = mock.MagicMock(text='Toronto M5V 3A8')
        trigger, first, second, button = (mock.MagicMock() for _ in range(4))
        elements = {
            'css:#nav-global-location-popover-link': trigger,
            'css:#GLUXZipUpdateInput_0': first,
            'css:#GLUXZipUpdateInput_1': second,
            'css:#GLUXZipUpdate': button,
            'css:#GLUXConfirmClose': None,
        }
        ingress_calls = [initial_ingress, final_ingress]
        b.page.ele.side_effect = lambda selector: (
            ingress_calls.pop(0) if selector == 'css:#glow-ingress-line2'
            else elements.get(selector))
        self.assertTrue(b._set_postal_code())
        first.input.assert_called_once_with('M5V', clear=True)
        second.input.assert_called_once_with('3A8', clear=True)

    def test_ca_visible_postal_prefix_is_accepted_but_us_is_not(self):
        b = self._browser()
        b.postal_code = 'M5V 3A8'
        b.marketplace = 'CA'
        b.location_verification_method = ''
        self.assertTrue(b._postal_matches('M5V 3A…'))
        self.assertEqual(b.location_verification_method, 'visible_prefix5')
        b.marketplace = 'US'
        b.postal_code = '90210'
        self.assertFalse(b._postal_matches('9021…'))


if __name__ == '__main__':
    unittest.main()
