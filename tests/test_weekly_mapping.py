# -*- coding: utf-8 -*-
import unittest
from unittest.mock import Mock

from weekly_mapping import (build_discovery, classify_sheet, find_asin_header,
                             validate_discovery)


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
        complete = ['ASIN', 'SKU', '尺寸', '正常售价', '本周折扣形式',
                    '本周折扣%', '目标成交价']
        self.assertEqual(classify_sheet('New Canada Products', True, True, complete)[0], 'CA')
        self.assertEqual(classify_sheet('US Seasonal Products', True, True, complete)[0], 'US')
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

    def test_four_new_descriptive_tabs_are_discovered_from_explicit_hosts(self):
        """新增子表不在旧静态名单内时，按完整表头和链接域名自动纳入。"""
        fc = Mock()
        titles = ['Outdoor Alpha', 'Outdoor Beta', 'Seasonal Gamma', 'Seasonal Delta']
        hosts = ['amazon.com', 'amazon.ca', 'amazon.com', 'amazon.ca']
        complete_header = [
            '说明', 'SKU', '插入字段', 'ASIN', '商品尺寸', '正常价格',
            '本周折扣类型', '本周折扣％', '目标价格',
        ]
        metadata = [
            {'sheet_id': f's{i}', 'title': title,
             'grid_properties': {'row_count': 2, 'column_count': 12}}
            for i, title in enumerate(titles)
        ]
        fc.query_sheets.return_value = metadata

        def read_values(_token, sid, rng):
            index = int(sid[1:])
            if rng.endswith('10'):
                return [complete_header]
            asin = f'B0ABCDEF{index:02d}'
            row = ['x', 'sku', 'helper',
                   f'https://www.{hosts[index]}/dp/{asin}', "5'X7'", 30,
                   '价格折扣', '10%', 27]
            return [[row[3]]]

        fc.read_values.side_effect = read_values
        report = build_discovery(fc, 'snapshot-token')
        validate_discovery(report)
        self.assertEqual(report['mapped_count'], 4)
        self.assertEqual([item['marketplace'] for item in report['sheets']],
                         ['US', 'CA', 'US', 'CA'])
        self.assertTrue(all(item['route_reason'] in {
            'asin_url_host_us', 'asin_url_host_ca'}
            for item in report['sheets']))

    def test_unknown_complete_tab_without_route_evidence_stays_blocking(self):
        fc = Mock()
        fc.query_sheets.return_value = [{
            'sheet_id': 's1', 'title': 'New Products',
            'grid_properties': {'row_count': 2, 'column_count': 12},
        }]
        header = ['ASIN', 'SKU', '商品尺寸', '正常价格', '本周折扣形式',
                  '本周折扣%', '目标成交价']
        fc.read_values.side_effect = [
            [header], [['B0ABCDEF01']],
        ]
        report = build_discovery(fc, 'snapshot-token')
        with self.assertRaisesRegex(RuntimeError, '未知 Marketplace'):
            validate_discovery(report)


if __name__ == '__main__':
    unittest.main()
