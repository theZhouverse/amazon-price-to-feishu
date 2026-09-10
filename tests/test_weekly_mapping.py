# -*- coding: utf-8 -*-
import unittest
from unittest.mock import Mock

from weekly_mapping import build_discovery, classify_sheet, find_asin_header, validate_discovery


class TestWeeklyMapping(unittest.TestCase):
    def test_header_detection_and_routes(self):
        self.assertEqual(find_asin_header([['x'], ['SKU', 'ASIN']]), (2, 2))
        self.assertEqual(find_asin_header([['x'], ['ASIN\n(颜色/变体说明)', 'SKU']]), (2, 1))
        self.assertIsNone(find_asin_header([['ASIN_CODE', 'SKU']]))
        self.assertEqual(classify_sheet('CPD03', True)[0], 'CA')
        self.assertEqual(classify_sheet('PD03', True)[0], 'US')
        self.assertEqual(classify_sheet('说明', False)[0], 'excluded')
        self.assertEqual(classify_sheet('CPD03', False, True)[0], 'unknown')
        self.assertEqual(classify_sheet('CPD03', False, False)[0], 'CA')
        self.assertEqual(classify_sheet('PD17', False, False)[0], 'US')
        self.assertEqual(classify_sheet('BI源数据', True, True)[0], 'excluded')
        self.assertEqual(classify_sheet('Mystery', True)[0], 'unknown')
        self.assertEqual(classify_sheet(
            'Sheet20', True, True, ['父ASIN', 'ASIN', 'MSKU', '销量'])[0], 'excluded')
        self.assertEqual(classify_sheet(
            'Sheet20', True, True, ['ASIN', '正常售价', '目标成交价'])[0], 'unknown')

    def test_discovery_maps_business_and_excludes_auxiliary(self):
        fc = Mock()
        fc.query_sheets.return_value = [
            {'sheet_id': 'us', 'title': 'PD03', 'grid_properties': {}},
            {'sheet_id': 'ca', 'title': 'CPD17', 'grid_properties': {}},
            {'sheet_id': 'note', 'title': '说明', 'grid_properties': {}},
        ]
        fc.read_values.side_effect = [
            [['ASIN', 'SKU']], [['SKU', 'ASIN']], [['说明']],
            [['B0ABCDEF12'], ['bad']], [['B0ZZZZZZ99']],
        ]
        report = build_discovery(fc, 'snapshot-token')
        validate_discovery(report)
        self.assertEqual(report['mapped_count'], 2)
        self.assertEqual(report['excluded_count'], 1)
        self.assertEqual(report['sheets'][0]['preliminary_valid_asins'], 1)
        fc.read_values_batch.assert_not_called()
        self.assertEqual(fc.read_values.call_args_list[0].args,
                         ('snapshot-token', 'us', 'A1:Z10'))

    def test_unknown_and_duplicate_are_blocking(self):
        report = {'duplicate_result_sheets': [], 'unknown_sheets': ['Mystery'],
                  'mapped_count': 1}
        with self.assertRaisesRegex(RuntimeError, '未知 Marketplace'):
            validate_discovery(report)


if __name__ == '__main__':
    unittest.main()
