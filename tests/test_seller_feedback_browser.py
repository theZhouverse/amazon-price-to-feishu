# -*- coding: utf-8 -*-
import unittest

from seller_feedback_browser import (FeedbackDataError, FeedbackStoreCollector,
                                      SafetyStop, validate_feedback_manager_url)


def selectors():
    return {
        'latest_feedback_marker': '.latest-feedback',
        'store_identity_selector': '.store-identity',
        'row_selector': '.feedback-row',
        'date_selector': '.date',
        'rating_selector': '.rating',
        'order_id_selector': '.order',
        'comment_selector': '.comment',
        'feedback_id_selector': '.feedback-id',
        'order_link_selector': 'a.order-link',
        'detail': {
            'marker': '.order-detail',
            'order_id_selector': '.detail-order',
            'order_item_number_selector': '.item-number',
            'asin_selector': '.asin',
            'sku_selector': '.sku',
        },
    }


class FakeRunner:
    def __init__(self):
        self.calls = []
        self.page = 0
        self.in_detail = False

    def store_open(self, store_id):
        self.calls.append(('open', store_id))

    def store_close(self, store_id):
        self.calls.append(('close', store_id))

    def page_visit(self, store_id, url):
        self.calls.append(('visit', store_id, url))

    def page_wait_nav(self, store_id, timeout_ms=30000):
        self.calls.append(('wait-nav', store_id))

    def page_exec(self, store_id, script, timeout_ms=30000):
        self.calls.append(('exec', script.split('*/', 1)[0]))
        if 'feedback-click-order' in script:
            self.in_detail = True
            order_id = 'ORDER-2' if 'ORDER-2' in script else 'ORDER-1'
            return {'clicked': True, 'order_id': order_id}
        if 'feedback-read-detail' in script:
            order_id = 'ORDER-2' if self.page == 1 else 'ORDER-1'
            return {
                'ok': True, 'marker_found': True, 'order_id': order_id,
                'order_item_number': 'ITEM-1', 'asin': 'B000TEST01', 'sku': 'SKU-1',
            }
        if 'feedback-back' in script:
            self.in_detail = False
            return {'started': True}
        if 'feedback-click-next' in script:
            self.page = 1
            return {'clicked': True, 'count': 1}
        if 'feedback-read' in script:
            if self.page == 0:
                return {
                    'ok': True, 'marker_found': True, 'store_identity': 'STORE-A',
                    'signature': 'page-1',
                    'rows': [
                        {'feedback_id': 'F-1', 'feedback_date': '2026-09-09',
                         'rating': '3 stars', 'order_id': 'ORDER-1', 'content': 'bad'},
                        {'feedback_id': 'F-4', 'feedback_date': '2026-09-09',
                         'rating': '4 stars', 'order_id': 'ORDER-4', 'content': 'good'},
                    ],
                    'next': {'count': 1, 'disabled': False},
                }
            return {
                'ok': True, 'marker_found': True, 'store_identity': 'STORE-A',
                'signature': 'page-2',
                'rows': [{'feedback_id': 'F-2', 'feedback_date': '2026-09-08',
                          'rating': '2 stars', 'order_id': 'ORDER-2', 'content': 'bad 2'}],
                'next': {'count': 0, 'disabled': False},
            }
        raise AssertionError('unexpected fake script')


class SellerFeedbackBrowserTest(unittest.TestCase):
    def store(self):
        return {
            'key': 'store_a', 'store_id': 'store-id-a',
            'expected_store_identity': 'STORE-A',
            'feedback_manager_url': 'https://sellercentral.amazon.com/feedback-manager',
            'selectors': selectors(),
        }

    def test_detail_and_next_are_serial_and_page_changes(self):
        runner = FakeRunner()
        collector = FeedbackStoreCollector(
            self.store(), runner, page_wait_min=1, page_wait_max=1,
            detail_wait_min=1, detail_wait_max=1, sleep_fn=lambda _: None,
        )
        result = collector(window={'start': '2026-09-03', 'end': '2026-09-09'})
        self.assertEqual(result['next_clicks'], 1)
        self.assertEqual(result['detail_attempted'], 2)
        self.assertEqual(result['detail_complete'], 2)
        self.assertEqual(len(result['pages']), 2)
        self.assertEqual(result['pages'][0]['items'][0]['asin'], 'B000TEST01')
        self.assertEqual(runner.calls[0], ('open', 'store-id-a'))
        self.assertEqual(runner.calls[-1], ('close', 'store-id-a'))

    def test_missing_selector_fails_closed(self):
        broken = self.store()
        broken['selectors'] = {}
        with self.assertRaises(FeedbackDataError):
            FeedbackStoreCollector(broken, FakeRunner(), sleep_fn=lambda _: None)

    def test_non_seller_central_feedback_url_is_rejected_before_store_open(self):
        runner = FakeRunner()
        store = self.store()
        store['feedback_manager_url'] = 'https://www.amazon.com/reviews'
        collector = FeedbackStoreCollector(store, runner, sleep_fn=lambda _: None)
        with self.assertRaises(FeedbackDataError):
            collector(window={'start': '2026-09-03', 'end': '2026-09-09'})
        self.assertEqual(runner.calls, [])

    def test_feedback_manager_url_requires_feedback_path(self):
        with self.assertRaises(FeedbackDataError):
            validate_feedback_manager_url('https://sellercentral.amazon.com/orders')

    def test_unchanged_page_after_next_is_safety_stop(self):
        runner = FakeRunner()
        original = runner.page_exec

        def same_page(store_id, script, timeout_ms=30000):
            value = original(store_id, script, timeout_ms)
            if 'feedback-read' in script and isinstance(value, dict):
                value['signature'] = 'same-page'
            return value

        runner.page_exec = same_page
        collector = FeedbackStoreCollector(
            self.store(), runner, page_wait_min=1, page_wait_max=1,
            detail_wait_min=1, detail_wait_max=1, sleep_fn=lambda _: None,
        )
        with self.assertRaises(SafetyStop):
            collector(window={'start': '2026-09-03', 'end': '2026-09-09'})
        self.assertEqual(runner.calls[-1], ('close', 'store-id-a'))


if __name__ == '__main__':
    unittest.main()
