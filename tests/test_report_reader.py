# -*- coding: utf-8 -*-
"""test_report_reader.py — 源表读取：表头探测 + 列名定位 + target 兜底 + 无效行

覆盖真实周报的两种布局：
- PD03 型：表头第 2 行，目标成交价在 K 列，K 是公式（本地读不到缓存值）
- PD05 型：目标成交价在 L 列（K 列是"广告策略"）
"""
import sys
import unittest
from io import BytesIO
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from feishu import FeishuClient, read_source_rows
from config import DEFAULTS


def mk_cfg(**kw):
    cfg = dict(DEFAULTS)
    cfg.update(kw)
    return cfg


def pd03_layout():
    """行1=分类标题, 行2=表头, 行3起=数据; K 列=目标成交价(公式,本地无缓存值)"""
    return [
        ['', '', '', '', '', '策略执行', '', '', '', '', '', '', ''],
        ['ASIN', 'SKU', '颜色', '尺寸', '正常售价', '上周折扣形式', '上周折扣%',
         '本周折扣形式', '本周折扣%', '广告策略', '目标成交价', 'BD/LD/日常折扣'],
        ['B0C5R56QTF', 'PPDD03-1', 'BLACK', "2.5'X8'", 33.99, '原价调整', 33.99,
         '原价调整', 37.99, '', None, 27.19],       # K 公式 → 本地算 37.99
        ['B0CLRVSVXG', 'PD17-2', 'WHITE', "5'X7'", 31.99, '价格折扣', 0.1,
         '价格折扣', 0.1, '', None, 0.15],          # K 公式 → 本地算 31.99*0.9=28.79
        ['B0CCP1R78Z', 'PPDD05-3', 'BLUE', '', 34.99, '', '', '', '', '', None, ''],
    ]


def pd05_layout():
    """PD05 型：目标成交价在 L 列（K 列=广告策略）"""
    return [
        ['', '', '', '', '', '策略执行', '', '', '', '', '', '', '', ''],
        ['ASIN', 'SKU', '颜色', '尺寸', '正常售价', '上周折扣形式', '上周折扣%',
         '本周折扣形式', '本周折扣%', '备注', '广告策略', '目标成交价', 'BD/LD/日常折扣'],
        ['B0CCP1R78Z', 'PPDD05-blue', 'BLUE', '', 34.99, '原价调整', 32.99,
         '原价调整', 34.99, '', '', 34.99, 0.14],   # L=34.99 直接读
        ['B0XYZ12345', 'PD05-x', '', '', None, '', '', '', '', '', '', None, ''],
    ]


class TestReadSourceRows(unittest.TestCase):
    def test_formula_size_text_is_resolved_from_sku(self):
        data = pd03_layout()
        data[2][3] = '=IF($A3="","",INDEX(\'BI源数据\'!A:A,1))'
        data[3][3] = 'IF($A4="","",INDEX(\'BI源数据\'!A:A,1))'
        data[2][1] = 'PPDD03-blackgray-5x8'
        data[3][1] = 'PD17-greywhite-8R'
        rows, invalid = read_source_rows(data, mk_cfg())
        self.assertEqual(len(invalid), 0)
        self.assertEqual(rows[0].size, "5'X8'")
        self.assertEqual(rows[1].size, "8'")

    def test_pd03_layout_k_formula_local_fallback(self):
        rows, invalid = read_source_rows(pd03_layout(), mk_cfg())
        self.assertEqual(len(rows), 3)
        self.assertEqual(len(invalid), 0)
        # K 公式无缓存值 → 本地 calc_target_price 兜底
        self.assertEqual(rows[0].target_price, Decimal('37.99'))       # 原价调整 I=37.99
        self.assertEqual(rows[1].target_price, Decimal('28.79'))       # 31.99*(1-0.1)
        # 行号从表头行+1 开始（表头在第 2 行 → 数据从第 3 行）
        self.assertEqual(rows[0].row_num, 3)
        # 5.x 来源追踪：K 空 → local_fallback
        self.assertEqual(rows[0].target_price_source, 'local_fallback')

    def test_target_source_excel_value(self):
        """K 列直接有值 → excel_cached_value（本地 xlsx 场景）"""
        data = pd03_layout()
        data[2][10] = 41.17                               # K 列有缓存值
        rows, _ = read_source_rows(data, mk_cfg(), source_kind='excel')
        self.assertEqual(rows[0].target_price, Decimal('41.17'))
        self.assertEqual(rows[0].target_price_source, 'excel_cached_value')

    def test_target_source_feishu(self):
        data = pd03_layout()
        data[2][10] = 41.17
        rows, _ = read_source_rows(data, mk_cfg(), source_kind='feishu')
        self.assertEqual(rows[0].target_price_source, 'feishu_value')

    def test_pd05_layout_target_in_col_l(self):
        rows, invalid = read_source_rows(pd05_layout(), mk_cfg())
        self.assertEqual(len(rows), 1)                     # L 空但 E 非空 → E 兜底仍有效
        self.assertEqual(rows[0].target_price, Decimal('34.99'))       # L 列直接读
        # 第二行 E/L 都空 → 无效
        self.assertEqual(len(invalid), 1)
        self.assertEqual(invalid[0]['asin'], 'B0XYZ12345')
        self.assertIn('目标成交价', invalid[0]['reason'])

    def test_asin_header_with_annotation_is_read_consistently(self):
        data = pd05_layout()
        data[1][0] = 'ASIN\n(颜色/变体说明)'
        rows, invalid = read_source_rows(data, mk_cfg())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].asin, 'B0CCP1R78Z')
        self.assertEqual(len(invalid), 1)

    def test_i_value_percent_string(self):
        data = pd03_layout()
        data[2][8] = '20%'                                # I 列 '20%' → 0.20
        rows, _ = read_source_rows(data, mk_cfg())
        # H=原价调整 且 I=0.20 (0<i<1) → target = E*(1-0.2)
        self.assertEqual(rows[0].i_value, Decimal('0.20'))
        self.assertEqual(rows[0].target_price, Decimal('27.19'))

    def test_missing_header_raises(self):
        with self.assertRaises(RuntimeError):
            read_source_rows([['无表头'], ['B0AAAA1111', 1, 2]], mk_cfg())

    def test_normal_price_empty_invalid(self):
        data = pd03_layout()
        data[3][4] = None                                 # E 空
        rows, invalid = read_source_rows(data, mk_cfg())
        self.assertNotIn('B0CLRVSVXG', [r.asin for r in rows])
        self.assertTrue(any(iv['asin'] == 'B0CLRVSVXG' for iv in invalid))

    def test_inserted_intermediate_columns_do_not_change_business_mapping(self):
        """源表插入/移动辅助列时，业务字段仍按表头读取。"""
        data = [
            ['周报说明'],
            ['辅助', 'SKU', '插入字段', 'ASIN\n(颜色/变体)', '商品尺寸',
             '正常价格', '中间策略', '本周折扣类型', '本周折扣％', '另一个辅助',
             '目标价格'],
            ['x', 'PD-new', 'ignore', 'B0C5R56QTF', "5'X7'", 31.99, 'ignore',
             '价格折扣', '10%', 'ignore', 28.79],
        ]
        rows, invalid = read_source_rows(data, mk_cfg())
        self.assertFalse(invalid)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].asin, 'B0C5R56QTF')
        self.assertEqual(rows[0].sku, 'PD-new')
        self.assertEqual(rows[0].size, "5'X7'")
        self.assertEqual(rows[0].normal_price, Decimal('31.99'))
        self.assertEqual(rows[0].target_price, Decimal('28.79'))

    def test_duplicate_helper_header_is_audited_but_primary_is_used(self):
        data = pd03_layout()
        data[1][11] = '正常售价'
        rows, invalid = read_source_rows(data, mk_cfg())
        self.assertFalse(invalid)
        self.assertEqual(rows[0].normal_price, Decimal('33.99'))
        from weekly_mapping import source_schema
        schema = source_schema(data[1])
        self.assertEqual(schema['columns']['normal_price'], 5)
        self.assertEqual(schema['duplicate']['normal_price'], 2)

    def test_missing_required_header_does_not_fall_back_to_old_column(self):
        data = pd03_layout()
        data[1][4] = '旧字段'
        with self.assertRaisesRegex(RuntimeError, '缺少字段=normal_price'):
            read_source_rows(data, mk_cfg())

    def test_uploaded_xlsx_is_not_truncated_at_column_o(self):
        """上传文件路径也必须读取插入辅助列后的宽表。"""
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = 'PD03'
        headers = [''] * 22
        headers[17:24] = ['ASIN', 'SKU', '商品尺寸', '正常价格',
                          '本周折扣形式', '本周折扣%', '目标成交价']
        values = [''] * 22
        values[17:24] = ['B0C5R56QTF', 'PD-wide', "5'X7'", 31.99,
                         '价格折扣', '10%', 28.79]
        for col, value in enumerate(headers, start=1):
            ws.cell(1, col).value = value
        for col, value in enumerate(values, start=1):
            ws.cell(2, col).value = value
        stream = BytesIO()
        wb.save(stream)

        client = object.__new__(FeishuClient)
        client.cfg = mk_cfg()
        client.token = 'test-token'
        client._token_expires_at = 0
        client._client = Mock()
        response = Mock()
        response.content = stream.getvalue()
        response.raise_for_status.return_value = None
        client._client.get.return_value = response
        rows_by_sheet, _, invalid = client.read_source_file(
            'file-token', ['PD03'], mk_cfg())
        self.assertFalse(invalid)
        self.assertEqual(rows_by_sheet['PD03'][0].target_price, Decimal('28.79'))


if __name__ == '__main__':
    unittest.main()
