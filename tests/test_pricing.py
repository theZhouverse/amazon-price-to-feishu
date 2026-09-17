# -*- coding: utf-8 -*-
"""test_pricing.py — 纯计算单测（Decimal/四类型/一致性）"""
import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models import CrawlResult, PageStatus, ReportRow
from pricing import (
    calc_target_price, compute_result, decide_discount_type, format_amount,
    format_pct, normalize_type, parse_pct_text, parse_price_text, q2,
)


def mk_row(normal='49.99', h='', i=None, target=None):
    return ReportRow(row_num=3, asin='B0TEST1234',
                     normal_price=Decimal(normal) if normal is not None else None,
                     h_type=h, i_value=i, target_price=target)


class TestParsing(unittest.TestCase):
    def test_price_text(self):
        self.assertEqual(parse_price_text('$29.99'), Decimal('29.99'))
        self.assertEqual(parse_price_text('$1,299.00'), Decimal('1299.00'))
        self.assertEqual(parse_price_text('37.99USD'), Decimal('37.99'))
        self.assertEqual(parse_price_text('  $0.50\xa0'), Decimal('0.50'))
        self.assertIsNone(parse_price_text(''))
        self.assertIsNone(parse_price_text(None))
        self.assertIsNone(parse_price_text('Currently unavailable'))

    def test_pct_text(self):
        self.assertEqual(parse_pct_text('30%'), Decimal('0.30'))
        self.assertEqual(parse_pct_text('-25%'), Decimal('0.25'))
        self.assertEqual(parse_pct_text('Apply 15.5% coupon'), Decimal('0.155'))
        self.assertIsNone(parse_pct_text('no discount'))

    def test_format(self):
        self.assertEqual(format_amount(Decimal('-10')), '-10.00')
        self.assertEqual(format_amount(Decimal('15')), '15.00')
        self.assertEqual(format_pct(Decimal('0.30')), '30%')
        self.assertEqual(format_pct(Decimal('0.255')), '25.5%')


class TestTargetPrice(unittest.TestCase):
    def test_i_empty_use_e(self):
        self.assertEqual(calc_target_price(mk_row('49.99')), Decimal('49.99'))

    def test_i_pct(self):
        self.assertEqual(calc_target_price(mk_row('49.99', i=Decimal('0.20'))), Decimal('39.99'))

    def test_i_zero_use_e(self):
        self.assertEqual(calc_target_price(mk_row('49.99', i=Decimal('0'))), Decimal('49.99'))

    def test_original_adjust_abs(self):
        self.assertEqual(calc_target_price(mk_row('49.99', h='原价调整', i=Decimal('39.99'))),
                         Decimal('39.99'))

    def test_original_adjust_blank_is_missing_not_normal_price(self):
        self.assertIsNone(calc_target_price(mk_row('49.99', h='原价调整')))

    def test_original_adjust_range_uses_right_hand_price(self):
        row = mk_row('49.99', h='原价调整')
        row.i_raw = '26.99-28.99'
        self.assertEqual(calc_target_price(row), Decimal('28.99'))

    def test_original_adjust_status_is_missing_not_normal_price(self):
        row = mk_row('49.99', h='原价调整')
        row.i_raw = '断货'
        self.assertIsNone(calc_target_price(row))

    def test_no_price(self):
        self.assertIsNone(calc_target_price(mk_row(normal=None)))


class TestDecideType(unittest.TestCase):
    def test_expected_coupon(self):
        cr = CrawlResult(asin='x', expected_type='coupon', coupon_pct=Decimal('0.25'))
        self.assertEqual(decide_discount_type(cr), ('coupon', False))

    def test_mismatch_single_actual(self):
        cr = CrawlResult(asin='x', expected_type='原价调整', code_pct=Decimal('0.30'))
        self.assertEqual(decide_discount_type(cr), ('code', True))

    def test_conflict_multiple(self):
        cr = CrawlResult(asin='x', expected_type='原价调整',
                         coupon_pct=Decimal('0.25'), code_pct=Decimal('0.30'))
        actual, mismatch = decide_discount_type(cr)
        self.assertEqual(actual, 'coupon')       # 页面真实证据固定优先级
        self.assertTrue(mismatch)                # 周报预期仅作为诊断

    def test_default_priority(self):
        cr = CrawlResult(asin='x', coupon_pct=Decimal('0.25'), save_pct=Decimal('0.10'))
        self.assertEqual(decide_discount_type(cr), ('coupon', False))

    def test_no_evidence_original(self):
        cr = CrawlResult(asin='x')
        self.assertEqual(decide_discount_type(cr), ('原价调整', False))


class TestCompute(unittest.TestCase):
    def _compute(self, row, **kwargs):
        cr = CrawlResult(asin=row.asin, marketplace=row.marketplace,
                         currency_code='CAD' if row.marketplace == 'CA' else 'USD')
        for k, v in kwargs.items():
            setattr(cr, k, v)
        compute_result(row, cr, '0.50')
        return cr

    def test_currency_us_and_ca_are_compared_in_their_own_currency(self):
        us = self._compute(mk_row('49.99', target=Decimal('49.99')),
                           display_price=Decimal('49.99'))
        ca_row = mk_row('78.63', target=Decimal('78.63'))
        ca_row.marketplace = 'CA'
        ca = self._compute(ca_row, display_price=Decimal('78.63'))
        self.assertEqual((us.currency_code, us.match), ('USD', '✅(0.00)'))
        self.assertEqual((ca.currency_code, ca.match), ('CAD', '✅(0.00)'))

    def test_unknown_or_cross_currency_blocks_comparison(self):
        row = mk_row('49.99', target=Decimal('49.99'))
        unknown = self._compute(row, display_price=Decimal('49.99'), currency_code='')
        wrong = self._compute(row, display_price=Decimal('49.99'), currency_code='CAD')
        for cr in (unknown, wrong):
            self.assertEqual(cr.status, PageStatus.CURRENCY_ERROR)
            self.assertEqual(cr.match, '-')
            self.assertIsNone(cr.price_diff)
            self.assertIn('currency_mismatch', cr.error)

    def test_original_adjustment(self):
        row = mk_row(normal='49.99', h='原价调整', i=Decimal('39.99'), target=Decimal('39.99'))
        cr = self._compute(row, display_price=Decimal('39.99'), expected_type='原价调整')
        self.assertEqual(cr.discount_type, '原价调整')
        self.assertEqual(cr.discount_value, '-10.00')     # 39.99 - 49.99
        self.assertEqual(cr.final_price, Decimal('39.99'))
        self.assertEqual(cr.match, '✅(0.00)')

    def test_code(self):
        row = mk_row('49.99', target=Decimal('34.99'))
        cr = self._compute(row, display_price=Decimal('49.99'),
                           code_pct=Decimal('0.30'), expected_type='code')
        self.assertEqual(cr.discount_type, 'code')
        self.assertEqual(cr.discount_value, '30%')
        self.assertEqual(cr.final_price, Decimal('34.99'))  # 49.99*0.70=34.993 → 34.99
        self.assertEqual(cr.match, '✅(0.00)')

    def test_price_discount_no_double_cut(self):
        row = mk_row('59.99', target=Decimal('59.99'))
        cr = self._compute(row, display_price=Decimal('59.99'),
                           save_pct=Decimal('0.25'), expected_type='价格折扣')
        self.assertEqual(cr.discount_type, '价格折扣')
        self.assertEqual(cr.discount_value, '25%')
        self.assertEqual(cr.final_price, Decimal('59.99'))  # 不重复扣减

    def test_coupon_pct(self):
        row = mk_row('59.99', target=Decimal('44.99'))
        cr = self._compute(row, display_price=Decimal('59.99'),
                           coupon_pct=Decimal('0.25'), expected_type='coupon')
        self.assertEqual(cr.discount_type, 'coupon')
        self.assertEqual(cr.discount_value, '25%')
        self.assertEqual(cr.final_price, Decimal('44.99'))  # 59.99*0.75=44.9925→44.99

    def test_coupon_amount_saving(self):
        row = mk_row('59.99', target=Decimal('44.99'))
        cr = self._compute(row, display_price=Decimal('59.99'),
                           coupon_amount=Decimal('15.00'), expected_type='coupon')
        self.assertEqual(cr.discount_value, '15.00')
        self.assertEqual(cr.final_price, Decimal('44.99'))

    def test_coupon_final_priority(self):
        row = mk_row('59.99', target=Decimal('44.99'))
        cr = self._compute(row, display_price=Decimal('59.99'),
                           coupon_pct=Decimal('0.25'), coupon_final=Decimal('44.99'),
                           expected_type='coupon')
        self.assertEqual(cr.final_price, Decimal('44.99'))

    def test_tolerance_pass_and_fail(self):
        row = mk_row('49.99', target=Decimal('39.99'))
        cr = self._compute(row, display_price=Decimal('40.19'), expected_type='原价调整')
        self.assertEqual(cr.match, '✅(+0.20)')      # 40.19-39.99=0.20 ≤ 0.50
        cr2 = self._compute(row, display_price=Decimal('40.74'), expected_type='原价调整')
        self.assertEqual(cr2.match, '❌(+0.75)')

    def test_negative_diff(self):
        row = mk_row('49.99', target=Decimal('39.99'))
        cr = self._compute(row, display_price=Decimal('39.24'), expected_type='原价调整')
        self.assertEqual(cr.match, '❌(-0.75)')      # 0.75 > 容差 0.50

    def test_abnormal_no_price(self):
        row = mk_row('49.99')
        cr = self._compute(row, display_price=None)
        self.assertEqual(cr.match, '-')

    def test_decimal_no_float_error(self):
        # 59.99 * 0.75 用 float 会是 44.992499999999996，Decimal 保证 44.99
        row = mk_row('59.99', target=Decimal('44.99'))
        cr = self._compute(row, display_price=Decimal('59.99'),
                           coupon_pct=Decimal('0.25'), expected_type='coupon')
        self.assertEqual(cr.final_price, Decimal('44.99'))
        self.assertNotEqual(cr.final_price, Decimal('44.9925'))

    def test_round_half_up(self):
        self.assertEqual(q2(Decimal('1.005')), Decimal('1.01'))

    def test_tolerance_boundaries(self):
        """4.1 容差边界：0.00/±0.49/-0.50 → ✅；±0.51 → ❌"""
        row = mk_row('49.99', target=Decimal('39.99'))
        target = Decimal('39.99')
        def case(diff_str):
            cr = self._compute(row, display_price=target + Decimal(diff_str),
                               expected_type='原价调整')
            return cr.match
        self.assertEqual(case('0.00'), '✅(0.00)')
        self.assertEqual(case('0.49'), '✅(+0.49)')
        self.assertEqual(case('-0.50'), '✅(-0.50)')
        self.assertEqual(case('0.51'), '❌(+0.51)')
        self.assertEqual(case('-0.51'), '❌(-0.51)')

    def test_coupon_pct_and_saving_dual_evidence(self):
        """3.10：pct + Saving 金额同时出现 → 折扣值输出百分比，最终价按 Saving 金额"""
        row = mk_row('59.99', target=Decimal('44.99'))
        cr = self._compute(row, display_price=Decimal('59.99'),
                           coupon_pct=Decimal('0.25'), coupon_amount=Decimal('15.00'),
                           expected_type='coupon')
        self.assertEqual(cr.discount_type, 'coupon')
        self.assertEqual(cr.discount_value, '25%')            # 折扣值优先百分比
        self.assertEqual(cr.final_price, Decimal('44.99'))    # 最终价 = 59.99 - 15.00
        self.assertEqual(cr.match, '✅(0.00)')


if __name__ == '__main__':
    unittest.main()
