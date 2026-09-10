# -*- coding: utf-8 -*-
"""test_cache.py — 缓存有效性 + 断点恢复单测"""
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cache import (
    SCHEMA_VERSION, data_signature, is_cache_valid, load_sheet_cache, restore_from_cache,
    save_sheet_cache,
)
from models import CrawlResult, PageStatus, ReportRow
from config import DEFAULTS
from frontend_checks import FRONTEND_CHECK_RULE_VERSION


def mk_cfg(**kw):
    cfg = dict(DEFAULTS)
    cfg.update(kw)
    return cfg


def mk_rows():
    return [
        ReportRow(row_num=2, asin='B0AAA11111', normal_price=Decimal('49.99'),
                  h_type='原价调整', i_value=Decimal('39.99'), target_price=Decimal('39.99')),
        ReportRow(row_num=3, asin='B0BBB22222', normal_price=Decimal('59.99'),
                  h_type='', i_value=None, target_price=Decimal('59.99')),
    ]


def mk_crawls():
    r1 = CrawlResult(asin='B0AAA11111', status=PageStatus.OK,
                     display_price=Decimal('39.99'), discount_type='原价调整',
                     discount_value='-10.00', final_price=Decimal('39.99'),
                     match='✅(0.00)', timestamp='2026-08-21 16:00:00',
                     marketplace='US', currency_code='USD',
                     product_url='https://www.amazon.com/dp/B0AAA11111',
                     html_path='D:/archive/a.html', html_url='file:///D:/archive/a.html',
                     html_sha256='abc', html_size_bytes=123456, archive_ms=987,
                     archive_status='ok', post_archive_delay_seconds=1.5)
    r2 = CrawlResult(asin='B0BBB22222', status=PageStatus.CRAWL_ERROR,
                     error='captcha', match='-', timestamp='2026-08-21 16:00:00')
    return [r1, r2]


class TestSignature(unittest.TestCase):
    def test_signature_changes_on_source(self):
        rows = mk_rows()
        s1 = data_signature(rows)
        rows[1].target_price = Decimal('49.99')
        s2 = data_signature(rows)
        self.assertNotEqual(s1, s2)


class TestCacheValid(unittest.TestCase):
    def test_valid(self):
        with tempfile.TemporaryDirectory() as td:
            run_id = 'test_run'
            # 直接构造 meta（避免依赖全局目录）
            meta = {
                'schema_version': SCHEMA_VERSION,
                'frontend_check_rule_version': FRONTEND_CHECK_RULE_VERSION,
                'parser_rule_version': DEFAULTS['parser_rule_version'],
                'snapshot_id': run_id,
                'sheet': 'PD03',
                'asin_signature': data_signature(mk_rows()),
                'price_tolerance': '0.50',
                'marketplaces': ['US'],
                'html_archive_enabled': False,
                'created_at': datetime.now().isoformat(timespec='seconds'),
                'records': {},
            }
            cfg = mk_cfg(html_archive_enabled=False)
            self.assertTrue(is_cache_valid(meta, cfg, 'PD03', mk_rows()))
            # sheet 不符
            self.assertFalse(is_cache_valid(meta, cfg, 'PD17', mk_rows()))
            # 容差不符
            self.assertFalse(is_cache_valid(meta, mk_cfg(price_tolerance='1.00', html_archive_enabled=False), 'PD03', mk_rows()))
            # 规则版本不符
            self.assertFalse(is_cache_valid(meta, mk_cfg(parser_rule_version='old', html_archive_enabled=False), 'PD03', mk_rows()))
            # 前端检查规则不符时必须重新抓取，不能复用旧的BSR/AC判定
            stale_frontend = dict(meta, frontend_check_rule_version='2026-09-10-v13')
            self.assertFalse(is_cache_valid(stale_frontend, cfg, 'PD03', mk_rows()))
            # 源数据变化
            rows2 = mk_rows()
            rows2[0].normal_price = Decimal('55.00')
            self.assertFalse(is_cache_valid(meta, cfg, 'PD03', rows2))

    def test_expired(self):
        meta = {
            'schema_version': SCHEMA_VERSION,
            'frontend_check_rule_version': FRONTEND_CHECK_RULE_VERSION,
            'parser_rule_version': DEFAULTS['parser_rule_version'],
            'snapshot_id': 'r', 'sheet': 'PD03',
            'asin_signature': data_signature(mk_rows()),
            'price_tolerance': '0.50',
            'marketplaces': ['US'],
            'html_archive_enabled': False,
            'created_at': (
                datetime.now() - timedelta(hours=DEFAULTS['cache_max_age_hours'] + 1)
            ).isoformat(timespec='seconds'),
            'records': {},
        }
        self.assertFalse(is_cache_valid(meta, mk_cfg(html_archive_enabled=False), 'PD03', mk_rows()))

    def test_enabled_archive_cache_requires_existing_file(self):
        meta = {
            'schema_version': SCHEMA_VERSION,
            'frontend_check_rule_version': FRONTEND_CHECK_RULE_VERSION,
            'parser_rule_version': DEFAULTS['parser_rule_version'],
            'snapshot_id': 'r', 'sheet': 'PD03',
            'asin_signature': data_signature(mk_rows()),
            'price_tolerance': '0.50', 'marketplaces': ['US'],
            'html_archive_enabled': True,
            'created_at': datetime.now().isoformat(timespec='seconds'),
            'records': {'B0AAA11111': {
                'status': 'ok', 'archive_status': 'ok',
                'html_path': 'D:/does-not-exist/archive.html',
            }},
        }
        self.assertFalse(is_cache_valid(meta, mk_cfg(html_archive_enabled=True),
                                        'PD03', mk_rows()))


class TestSaveRestore(unittest.TestCase):
    def test_roundtrip_and_resume(self):
        with tempfile.TemporaryDirectory() as td:
            import cache as cache_mod
            orig_root = cache_mod.CACHE_ROOT
            cache_mod.CACHE_ROOT = Path(td)
            try:
                rows = mk_rows()
                crawls = mk_crawls()
                save_sheet_cache('run1', 'PD03', rows, crawls, mk_cfg())
                meta = load_sheet_cache('run1', 'PD03')
                self.assertIsNotNone(meta)
                self.assertEqual(meta['marketplaces'], ['US'])
                self.assertEqual(meta['currency_codes'], ['USD'])
                # 断点：复用 ok，跳过 crawl_error
                reuse = restore_from_cache(meta, rows)
                self.assertIn('B0AAA11111', reuse)
                self.assertNotIn('B0BBB22222', reuse)
                self.assertEqual(reuse['B0AAA11111'].final_price, Decimal('39.99'))
                self.assertEqual(reuse['B0AAA11111'].currency_code, 'USD')
                self.assertEqual(reuse['B0AAA11111'].product_url,
                                 'https://www.amazon.com/dp/B0AAA11111')
                self.assertEqual(reuse['B0AAA11111'].html_url,
                                 'file:///D:/archive/a.html')
                self.assertEqual(reuse['B0AAA11111'].archive_status, 'ok')
                # 中断后续跑：todo 只包含未完成
                todo = [r for r in rows if r.asin not in reuse]
                self.assertEqual([r.asin for r in todo], ['B0BBB22222'])
            finally:
                cache_mod.CACHE_ROOT = orig_root

    def test_roundtrip_preserves_promotion_evidence(self):
        """3.4：缓存往返后促销证据完整，重新计算不改变折扣类型"""
        from pricing import compute_result
        with tempfile.TemporaryDirectory() as td:
            import cache as cache_mod
            orig_root = cache_mod.CACHE_ROOT
            cache_mod.CACHE_ROOT = Path(td)
            try:
                row = ReportRow(row_num=2, asin='B0COUP1X', normal_price=Decimal('59.99'),
                                h_type='coupon', target_price=Decimal('44.99'))
                cr = CrawlResult(asin='B0COUP1X', status=PageStatus.OK,
                                 display_price=Decimal('59.99'),
                                 coupon_pct=Decimal('0.25'), coupon_amount=Decimal('15.00'),
                                 expected_type='coupon', marketplace='US', currency_code='USD')
                compute_result(row, cr, '0.50')
                before = cr.six_columns()

                save_sheet_cache('run9', 'PD03', [row], [cr], mk_cfg())
                meta = load_sheet_cache('run9', 'PD03')
                reuse = restore_from_cache(meta, [row])
                self.assertIn('B0COUP1X', reuse)
                r2 = reuse['B0COUP1X']
                # 促销证据恢复
                self.assertEqual(r2.coupon_pct, Decimal('0.25'))
                self.assertEqual(r2.coupon_amount, Decimal('15.00'))
                self.assertEqual(r2.expected_type, 'coupon')
                # 重新计算后六列一致
                compute_result(row, r2, '0.50')
                after = r2.six_columns()
                self.assertEqual(before, after)
            finally:
                cache_mod.CACHE_ROOT = orig_root


if __name__ == '__main__':
    unittest.main()
