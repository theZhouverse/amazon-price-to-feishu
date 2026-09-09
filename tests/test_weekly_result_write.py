import sys
import unittest
import re
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

from models import CrawlResult, PageStatus
from weekly_result import RESULT_HEADERS, LEGACY_RESULT_HEADERS, write_weekly_result_columns


class FakeFeishu:
    def __init__(self, header=None, asins=None):
        self.header = header or RESULT_HEADERS
        self.asins = ['B000000001', 'B000000002'] if asins is None else asins
        self.writes = []
        self.backups = []
        self.cells = {}
        self.migrated = None

    def read_values(self, token, sid, rng):
        if rng in ('A2:P2', 'A2:U2', 'A2:V2'): return [self.header]
        single = re.fullmatch(r'A(\d+):A(\d+)', rng)
        if single:
            start, end = map(int, single.groups())
            return [[asin] for asin in self.asins[max(0, start - 3):max(0, end - 2)]]
        if rng.startswith('M3:V') and self.migrated is not None:
            return self.migrated
        legacy = re.fullmatch(r'M(\d+):P(\d+)', rng)
        if legacy:
            values = [['时间戳', 'HTML链接', '币种', 'Amazon链接'],
                      ['2026-09-08 00:00:00', 'file:///old.html', 'CAD',
                       'https://www.amazon.ca/dp/B000000001']]
            start, end = map(int, legacy.groups())
            return values[max(0, start - 2):max(0, end - 1)]
        match = re.fullmatch(r'A(\d+):([AV])(\d+)', rng)
        if match:
            start, col, end = match.groups()
            return [[self.asins[r-3]] + ([''] * 6 + self.cells.get((sid, r), [''] * 15) if col == 'V' else [])
                    for r in range(int(start), int(end)+1)]
        match = re.fullmatch(r'H(\d+):V(\d+)', rng)
        if match:
            start, end = map(int, match.groups())
            return [self.cells.get((sid, r), [''] * 15)
                    for r in range(start, end + 1)]
        return []

    def write_values(self, token, sid, rng, values):
        self.writes.append((token, sid, rng, values))
        if rng == 'A2:V2':
            self.header = list(values[0])
        if rng.startswith('M3:V'):
            self.migrated = values
        match = re.fullmatch(r'H(\d+):V(\d+)', rng)
        if match:
            for i, row in enumerate(values, start=int(match.group(1))):
                self.cells[(sid, i)] = row

    def backup_target_sheet(self, *args):
        if args not in self.backups:
            self.backups.append(args)

    def query_sheets(self, token):
        return [{'title': 'PD03', 'sheet_id': 'sid',
                 'grid_properties': {'row_count': max(3, len(self.asins) + 2), 'column_count': 21}},
                {'title': 'PD17', 'sheet_id': 's2',
                 'grid_properties': {'row_count': max(3, len(self.asins) + 2), 'column_count': 16}}]


def manifest():
    return {
        'business_ready': True,
        'registry': {'spreadsheet_token': 'registry'},
        'source': {'spreadsheet_token': 'source'},
        'snapshot': {'spreadsheet_token': 'snapshot'},
        'result': {'spreadsheet_token': 'result'},
        'sheet_mappings': [{'result_sheet': 'PD03', 'result_sheet_id': 'sid'}],
    }


def result(asin, run_id='run1', status=PageStatus.OK, archive=True):
    from datetime import datetime
    cr = CrawlResult(asin=asin, run_id=run_id, status=status,
                     display_price=Decimal('10.00'), currency_code='USD',
                     product_url=f'https://www.amazon.com/dp/{asin}')
    if archive:
        cr.archive_status = 'ok'
        cr.html_url = f'file:///D:/html/{asin}.html'
    cr.timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    return cr


class WeeklyResultWriteTest(unittest.TestCase):
    def test_writes_contiguous_h_to_v_for_same_run(self):
        fc = FakeFeishu()
        report = write_weekly_result_columns(
            fc, manifest(), 'run1',
            {'PD03': [result('B000000001'), result('B000000002')]},
            {'html_archive_required': True})
        self.assertEqual(report['written_rows'], 2)
        row_write = next(item for item in fc.writes if item[2] == 'H3:V4')
        self.assertEqual(len(row_write[3][0]), 15)
        self.assertEqual(row_write[3][0][5], 'USD')

    def test_archive_failure_blocks_only_affected_row(self):
        fc = FakeFeishu()
        bad = result('B000000001', archive=False)
        bad.archive_status = 'failed'; bad.archive_error = 'capture failed'
        report = write_weekly_result_columns(
            fc, manifest(), 'run1', {'PD03': [bad, result('B000000002')]},
            {'html_archive_required': True})
        self.assertEqual(report['written_rows'], 1)
        self.assertEqual(report['blocked'][0]['asin'], 'B000000001')
        self.assertTrue(any(item[2] == 'H3:V4' for item in fc.writes))

    def test_legacy_layout_migration_backed_up_and_idempotent(self):
        fc = FakeFeishu(header=LEGACY_RESULT_HEADERS, asins=['B000000001'])
        args = (fc, manifest(), 'run1', {'PD03': [result('B000000001')]}, {'html_archive_required': False})
        write_weekly_result_columns(*args)
        self.assertEqual(len(fc.backups), 1)
        self.assertEqual(fc.writes[0][2], 'M3:V3')
        self.assertEqual(fc.writes[0][3][0][0], 'CAD')
        self.assertEqual(fc.writes[0][3][0][9], 'https://www.amazon.ca/dp/B000000001')
        self.assertEqual(fc.writes[1][2], 'A2:V2')
        write_weekly_result_columns(*args)
        self.assertEqual(len(fc.backups), 1)

    def test_disabled_archive_writes_without_html(self):
        fc = FakeFeishu(asins=['B000000001'])
        report = write_weekly_result_columns(fc, manifest(), 'run1',
            {'PD03': [result('B000000001', archive=False)]}, {'html_archive_required': False})
        self.assertEqual(report['written_rows'], 1)
        self.assertNotIn('HTML链接', RESULT_HEADERS)

    def test_run_id_mismatch_blocks_before_any_write(self):
        fc = FakeFeishu()
        with self.assertRaisesRegex(RuntimeError, 'run_id'):
            write_weekly_result_columns(
                fc, manifest(), 'run1',
                {'PD03': [result('B000000001'), result('B000000002', run_id='old')]},
                {'html_archive_required': True})
        self.assertEqual(fc.writes, [])

    def test_layout_duplicate_missing_and_protected_target_block(self):
        cases = [
            (FakeFeishu(header=['bad']), manifest(), '布局'),
            (FakeFeishu(asins=['B000000001', 'B000000001']), manifest(), '重复'),
            (FakeFeishu(asins=['B000000009']), manifest(), '找不到'),
        ]
        for fc, data, reason in cases:
            with self.subTest(reason=reason), self.assertRaisesRegex(RuntimeError, reason):
                write_weekly_result_columns(
                    fc, data, 'run1', {'PD03': [result('B000000001')]},
                    {'html_archive_required': True})
            self.assertEqual(fc.writes, [])
        protected = manifest(); protected['result']['spreadsheet_token'] = 'snapshot'
        fc = FakeFeishu()
        with self.assertRaisesRegex(RuntimeError, '独立结果'):
            write_weekly_result_columns(fc, protected, 'run1', {}, {})

    def test_currency_error_is_not_written(self):
        fc = FakeFeishu(asins=['B000000001'])
        cr = result('B000000001', status=PageStatus.CURRENCY_ERROR, archive=False)
        report = write_weekly_result_columns(
            fc, manifest(), 'run1', {'PD03': [cr]}, {'html_archive_required': True})
        self.assertEqual(report['written_rows'], 0)
        self.assertEqual(report['blocked'][0]['reason'], 'currency_error')

    def test_identity_mismatch_is_not_written(self):
        fc = FakeFeishu(asins=['B000000001'])
        cr = result('B000000001', status=PageStatus.IDENTITY_MISMATCH, archive=False)
        cr.error = 'identity_mismatch: 请求 B000000001，最终页面 B000000002'
        report = write_weekly_result_columns(
            fc, manifest(), 'run1', {'PD03': [cr]}, {'html_archive_required': False})
        self.assertEqual(report['written_rows'], 0)
        self.assertIn('identity_mismatch', report['blocked'][0]['reason'])

    def test_silent_write_failure_is_blocked_not_counted(self):
        fc = FakeFeishu(asins=['B000000001'])
        fc.write_values = lambda *args: None
        report = write_weekly_result_columns(fc, manifest(), 'run1',
                    {'PD03': [result('B000000001')]}, {'html_archive_required': False})
        self.assertEqual(report['written_rows'], 0)
        self.assertEqual(len(report['blocked']), 1)
        self.assertEqual(report['failures'][0]['stage'], 'write_or_verify')

    def test_one_sheet_failure_does_not_stop_next(self):
        fc = FakeFeishu(asins=['B000000001'])
        write = fc.write_values
        def fail_first(token, sid, rng, values):
            if sid == 'sid':
                raise RuntimeError('API unavailable')
            write(token, sid, rng, values)
        fc.write_values = fail_first
        data = manifest()
        data['sheet_mappings'].append({'result_sheet': 'CPD03', 'result_sheet_id': 'sid2'})
        report = write_weekly_result_columns(fc, data, 'run1',
                    {'PD03': [result('B000000001')], 'CPD03': [result('B000000001')]},
                    {'html_archive_required': False})
        self.assertEqual(report['written_rows'], 1)
        self.assertEqual(report['blocked'][0]['sheet'], 'PD03')

    def test_rich_link_migration_and_empty_sheet(self):
        fc = FakeFeishu(header=LEGACY_RESULT_HEADERS, asins=[])
        read = fc.read_values
        def rich(token, sid, rng):
            values = read(token, sid, rng)
            if re.fullmatch(r'M2:P\d+', rng):
                values[1][3] = [{'type': 'url', 'text': 'Amazon', 'link': 'https://www.amazon.ca/dp/B000000001'}]
            return values
        fc.read_values = rich
        report = write_weekly_result_columns(fc, manifest(), 'run1', {'PD03': []},
                                             {'html_archive_required': False})
        self.assertEqual(report['failures'], [])
        self.assertEqual(fc.migrated[0][9], 'https://www.amazon.ca/dp/B000000001')


if __name__ == '__main__': unittest.main()
