import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

from weekly_assets import WeeklyAssetStore
from weekly_result import RESULT_HEADERS, sync_weekly_result_base


CFG = {
    'source_cols': {'asin': 1, 'sku': 2, 'size': 4, 'normal_price': 5,
                    'h_type': 8, 'i_value': 9, 'target_price': 11},
}


def source(*rows):
    header = ['ASIN', 'SKU', '颜色', '尺寸', '正常售价', '', '',
              '本周折扣形式', '本周折扣%', '', '目标成交价']
    return [['周报'], header, *rows]


def row(asin, sku='SKU', normal=20, target=18):
    return [asin, sku, '', 'L', normal, '', '', 'coupon', '10%', '', target]


class FakeFeishu:
    def __init__(self, source_values, with_result=False):
        self.source_values = source_values
        self.result_sheets = {'PD03': 'r1'} if with_result else {}
        self.headers = {}
        self.asins = {}
        self.base_values = {}
        self.writes = []
        self.backups = []

    def list_sheets(self, token):
        return {'PD03': 's1', 'EMPTY': 's2'} if token == 'snapshot' else dict(self.result_sheets)

    def query_sheets(self, token):
        return [{'title': title, 'sheet_id': sid}
                for title, sid in self.list_sheets(token).items()]

    def read_values(self, token, sid, rng):
        if token == 'snapshot':
            return self.source_values[sid]
        if rng == 'A2:V2':
            return [self.headers[sid]] if sid in self.headers else []
        if rng.startswith('A3:A'):
            return [[asin] for asin in self.asins.get(sid, [])]
        if rng.startswith('A3:G'):
            return self.base_values.get(sid, [])
        return []

    def add_sheet(self, token, title, index):
        sid = f'r{len(self.result_sheets) + 1}'
        self.result_sheets[title] = sid
        return sid

    def backup_target_sheet(self, token, title, sid, run_id):
        self.backups.append((title, sid, run_id))
        return Path('backups') / run_id / f'{title}.json'

    def write_values(self, token, sid, rng, values):
        self.writes.append((sid, rng, values))
        if rng == 'A2:V2':
            self.headers[sid] = list(values[0])
        elif rng.startswith('A3:V'):
            self.asins[sid] = []
        elif rng.startswith('A3:G'):
            self.asins[sid] = [str(item[0]) for item in values]
            self.base_values[sid] = values


class WeeklyResultTest(unittest.TestCase):
    def test_base_non_asin_corruption_is_rejected(self):
        fc = FakeFeishu({'s1': source(row('B000000001')), 's2': []})
        original_read = fc.read_values
        def corrupt(token, sid, rng):
            values = original_read(token, sid, rng)
            if token == 'result' and rng.startswith('A3:G'):
                values = [list(row) for row in values]
                values[0][1] = 'wrong SKU'
            return values
        fc.read_values = corrupt
        with self.assertRaisesRegex(RuntimeError, '回读校验失败'):
            self.run_sync(fc)

    def manifest(self):
        return {
            'status': 'ready', 'mapping_ready': True, 'business_ready': False,
            'registry': {'spreadsheet_token': 'registry'},
            'source': {'spreadsheet_token': 'original'},
            'snapshot': {'spreadsheet_token': 'snapshot'},
            'result': {'spreadsheet_token': 'result'},
            'sheet_mappings': [
                {'source_order': 1, 'source_sheet': 'PD03', 'source_sheet_id': 's1',
                 'result_sheet': 'PD03', 'status': 'mapped'},
                {'source_order': 2, 'source_sheet': 'EMPTY', 'source_sheet_id': 's2',
                 'result_sheet': 'EMPTY', 'status': 'mapped_empty'},
            ],
        }

    def run_sync(self, fc, manifest=None):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = WeeklyAssetStore(Path(temp.name))
        store.save('week', manifest or self.manifest())
        result = sync_weekly_result_base(fc, store, 'week', CFG, 'run-1')
        return result, store.load('week')

    def test_create_empty_reorder_and_invalid_row_retained(self):
        vals = source(row('B000000002'), row('B000000001', normal=None, target=None))
        fc = FakeFeishu({'s1': vals, 's2': []})
        result, manifest = self.run_sync(fc)
        self.assertEqual(result['row_count'], 2)
        self.assertEqual(fc.asins['r1'], ['B000000002', 'B000000001'])
        self.assertEqual(fc.headers['r1'], RESULT_HEADERS)
        self.assertEqual(fc.headers['r2'], RESULT_HEADERS)
        self.assertEqual(result['sheets'][0]['invalid_rows_retained'], 1)
        self.assertTrue(manifest['business_ready'])
        self.assertEqual(len(fc.backups), 2)

    def test_reuse_and_authoritative_delete(self):
        fc = FakeFeishu({'s1': source(row('B000000001')), 's2': []}, with_result=True)
        fc.asins['r1'] = ['B000000001', 'B000000002']
        result, _ = self.run_sync(fc)
        self.assertEqual(result['row_count'], 1)
        self.assertEqual(fc.asins['r1'], ['B000000001'])
        self.assertTrue(any(rng.startswith('A3:V') for _, rng, _ in fc.writes))

    def test_duplicate_asin_blocks_before_any_write(self):
        fc = FakeFeishu({'s1': source(row('B000000001'), row('B000000001')), 's2': []})
        with self.assertRaisesRegex(RuntimeError, '重复 ASIN'):
            self.run_sync(fc)
        self.assertEqual(fc.writes, [])
        self.assertEqual(fc.result_sheets, {})

    def test_missing_or_drifted_source_sheet_blocks(self):
        manifest = self.manifest()
        manifest['sheet_mappings'][0]['source_sheet_id'] = 'wrong'
        fc = FakeFeishu({'s1': source(row('B000000001')), 's2': []})
        with self.assertRaisesRegex(RuntimeError, '映射已漂移'):
            self.run_sync(fc, manifest)
        self.assertEqual(fc.writes, [])

    def test_protected_target_blocks(self):
        manifest = self.manifest()
        manifest['result']['spreadsheet_token'] = 'snapshot'
        fc = FakeFeishu({'s1': source(row('B000000001')), 's2': []})
        with self.assertRaisesRegex(RuntimeError, '独立结果'):
            self.run_sync(fc, manifest)
        self.assertEqual(fc.writes, [])


if __name__ == '__main__':
    unittest.main()
