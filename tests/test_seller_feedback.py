import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

from seller_feedback import (  # noqa: E402
    FEEDBACK_HEADERS,
    FeedbackDataError,
    advance_feedback_state,
    can_advance_feedback_state,
    collect_feedback,
    feedback_matrix,
    feedback_row_values,
    feedback_window,
    load_feedback_state,
    merge_feedback_rows,
    normalize_feedback_pages,
    normalize_feedback_record,
    parse_feedback_matrix,
    publish_feedback_sheet,
    save_feedback_state,
)


NOW = datetime.fromisoformat('2026-09-09T07:30:00+08:00')


def raw_feedback(feedback_id, rating, day, *, order='ORDER-1', content='bad',
                 item='ITEM-1', asin='B000000001', sku='SKU-1'):
    return {
        'id': feedback_id,
        'rating': rating,
        'date': day,
        'order_id': order,
        'comment': content,
        'order_item_number': item,
        'asin': asin,
        'sku': sku,
    }


class FakeFeishu:
    def __init__(self, values):
        self.values = [list(row) for row in values]
        self.backups = []

    def read_values(self, spreadsheet, sheet_id, rng):
        end = int(rng.rsplit('I', 1)[1])
        rows = self.values[:end]
        return [list(row) for row in rows]

    def write_values(self, spreadsheet, sheet_id, rng, values):
        left, right = rng.split(':')
        start_col = ord(left[0]) - ord('A')
        start_row = int(left[1:])
        end_row = int(right[1:])
        width = ord(right[0]) - ord('A') + 1 - start_col
        while len(self.values) < end_row:
            self.values.append([''] * (start_col + width))
        for row_index, values_row in enumerate(values, start=start_row - 1):
            while len(self.values[row_index]) < start_col + width:
                self.values[row_index].append('')
            for col_index, value in enumerate(values_row[:width], start=start_col):
                self.values[row_index][col_index] = value

    def backup_target_sheet(self, spreadsheet, sheet, sheet_id, run_id):
        self.backups.append((spreadsheet, sheet, sheet_id, run_id))


class SellerFeedbackTest(unittest.TestCase):
    def test_rating_three_is_kept_but_four_and_missing_are_rejected(self):
        pages = [[
            raw_feedback('f1', 3, '2026-09-09'),
            raw_feedback('f2', 4, '2026-09-09'),
            {'id': 'f3', 'rating': 2, 'date': '', 'comment': 'unknown date'},
        ]]
        rows, stats = normalize_feedback_pages(
            'store_a', pages, 'run1',
            window=feedback_window(NOW),
        )
        self.assertEqual([row['rating'] for row in rows], [3])
        self.assertEqual(stats['raw_rows'], 3)
        self.assertEqual(stats['eligible_rows'], 1)
        self.assertEqual(stats['invalid_date_rows'], 1)

    def test_initial_window_then_incremental_window(self):
        state = load_feedback_state(Path('missing-feedback-state.json'))
        first = feedback_window(NOW, state)
        self.assertEqual(first['mode'], 'initial_7d')
        self.assertEqual((first['start'], first['end']), ('2026-09-03', '2026-09-09'))

        state['initial_window_complete'] = True
        next_window = feedback_window(NOW, state)
        self.assertEqual(next_window['mode'], 'incremental_3d')
        self.assertEqual((next_window['start'], next_window['end']), ('2026-09-07', '2026-09-09'))

    def test_feedback_date_parser_accepts_chinese_and_timezone_values(self):
        row = normalize_feedback_record(
            raw_feedback('f1', '3星', '2026年9月8日'), 'store_a', 'run1')
        self.assertEqual(row['date'], '2026-09-08')
        row = normalize_feedback_record(
            raw_feedback('f2', 1, '2026-09-08T23:30:00+00:00'), 'store_a', 'run1')
        self.assertEqual(row['date'], '2026-09-09')

    def test_fallback_key_is_stable_and_merge_is_idempotent(self):
        row = normalize_feedback_record(
            {'rating': 1, 'date': '2026-09-08', 'order_id': 'O1', 'content': 'bad'},
            'store_a', 'run1')
        again = normalize_feedback_record(
            {'rating': 1, 'date': '2026-09-08', 'order_id': 'O1', 'content': 'bad'},
            'store_a', 'run2')
        self.assertTrue(row['_degraded_key'])
        self.assertEqual(row['_feedback_key'], again['_feedback_key'])
        self.assertEqual(len(merge_feedback_rows([row], [again])), 1)

    def test_partial_detail_does_not_erase_previous_complete_detail(self):
        complete = normalize_feedback_record(
            raw_feedback('f1', 2, '2026-09-08'), 'store_a', 'run1')
        partial = normalize_feedback_record(
            raw_feedback('f1', 2, '2026-09-08', item='', asin='', sku=''),
            'store_a', 'run2')
        merged = merge_feedback_rows([complete], [partial])
        self.assertEqual(merged[0]['order_item_number'], 'ITEM-1')
        self.assertEqual(merged[0]['asin'], 'B000000001')
        self.assertEqual(merged[0]['sku'], 'SKU-1')
        self.assertEqual(merged[0]['_detail_status'], 'ok')

    def test_matrix_keeps_ten_days_and_groups_stores(self):
        old = normalize_feedback_record(
            raw_feedback('old', 1, '2026-08-30'), 'store_a', 'old')
        a = normalize_feedback_record(
            raw_feedback('a', 3, '2026-09-09'), 'store_a', 'run')
        b = normalize_feedback_record(
            raw_feedback('b', 1, '2026-09-08'), 'store_b', 'run')
        headers, matrix, stats = feedback_matrix(
            [old], [b, a], now=NOW, store_order=('store_a', 'store_b'))
        self.assertEqual(headers, list(FEEDBACK_HEADERS))
        self.assertEqual([row[0] for row in matrix], ['store_a', 'store_b'])
        self.assertEqual(stats['feedback_rows_expired_deleted'], 1)
        self.assertEqual(len(matrix[0]), 9)

    def test_legacy_a_l_header_is_rejected(self):
        with self.assertRaises(FeedbackDataError):
            parse_feedback_matrix([[
                '店铺', 'feedback唯一标识', '反馈时间', '星级', '评论内容', 'code',
                'ASIN', '订单/交易标识', '来源页面', '抓取时间', 'run_id', '处理状态',
            ]])

    def test_two_store_failure_continues_and_redacts_credentials(self):
        def good():
            return {'source_url': 'https://seller.example/a', 'pages': [[
                raw_feedback('f1', 3, '2026-09-09')
            ]]}

        def blocked():
            raise RuntimeError('Authorization: Bearer secret-value; captcha')

        with tempfile.TemporaryDirectory() as temp:
            report = collect_feedback(
                'run1', {'store_a': good, 'store_b': blocked}, Path(temp),
                window=feedback_window(NOW), store_order=('store_a', 'store_b'))
            self.assertEqual(report['status'], 'partial')
            self.assertEqual(report['stores']['store_a']['rows_in_window'], 1)
            self.assertNotIn('secret-value', str(report))
            self.assertTrue((Path(temp) / 'feedback_collection.json').is_file())

    def test_state_advances_only_after_two_store_readback(self):
        report = {
            'run_id': 'run1',
            'finished_at': '2026-09-09T08:00:00+08:00',
            'window': {'mode': 'initial_7d'},
            'stores': {'store_a': {'status': 'ok'}, 'store_b': {'status': 'ok'}},
        }
        sheet = {'status': 'ok', 'readback': {'status': 'ok'}}
        self.assertTrue(can_advance_feedback_state(report, sheet))
        state = advance_feedback_state({'version': 1}, report, sheet)
        self.assertTrue(state['initial_window_complete'])

        report['stores']['store_b']['status'] = 'partial'
        self.assertFalse(can_advance_feedback_state(report, sheet))

    def test_publish_writes_exact_nine_columns_and_clears_old_rows(self):
        old = normalize_feedback_record(
            raw_feedback('old', 1, '2026-08-30'), 'store_a', 'old')
        old2 = normalize_feedback_record(
            raw_feedback('old2', 2, '2026-08-30', order='ORDER-2'), 'store_a', 'old')
        old_matrix = [feedback_row_values(old), feedback_row_values(old2)]
        fc = FakeFeishu([list(FEEDBACK_HEADERS)] + old_matrix)
        incoming = [normalize_feedback_record(
            raw_feedback('new', 3, '2026-09-09'), 'store_b', 'run1')]
        with tempfile.TemporaryDirectory() as temp:
            result = publish_feedback_sheet(
                fc, 'spreadsheet', 'sheet', incoming, 'run1', Path(temp), now=NOW,
                store_order=('store_a', 'store_b'))
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(fc.values[0], list(FEEDBACK_HEADERS))
        self.assertEqual(fc.values[1][0], 'store_b')
        self.assertTrue(all(value == '' for value in fc.values[2][:9]))
        self.assertEqual(len(fc.backups), 1)

    def test_state_file_is_atomic_and_non_sensitive(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'feedback_state.json'
            save_feedback_state(path, {
                'version': 1, 'initial_window_complete': True,
                'authorization': 'secret-value',
            })
            loaded = load_feedback_state(path)
            self.assertTrue(loaded['initial_window_complete'])
            self.assertNotIn('secret-value', path.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
