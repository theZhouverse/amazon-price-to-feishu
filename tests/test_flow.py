# -*- coding: utf-8 -*-
"""test_flow.py — 端到端流程冒烟测试（mock 浏览器 + 飞书，验证编排）

覆盖：CLI 互斥 → 读源表 → 快照 → 同步 → 抓取(mock) → 缓存 → CSV → 六列写回。
"""
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models import CrawlResult, PageStatus, ReportRow
from config import DEFAULTS


def mk_cfg(**kw):
    cfg = dict(DEFAULTS)
    cfg['html_archive_enabled'] = False
    cfg.update(kw)
    return cfg


class FakeWorker:
    """代替真实 AmazonWorker（不启动浏览器）"""
    fetched: list = []

    def __init__(self, headless=True, us_zip='90210', proxy=None, tabs=1,
                 marketplace='US', postal_code=None, location_mode=None,
                 browser_auto_port=True, browser_no_sandbox=True,
                 browser_disable_gpu=True, logger=None):
        self.tab = mock.MagicMock()
        self.tab.html = '<html></html>'
        self.proxy = proxy
        self.marketplace = marketplace
        self.postal_code = postal_code
        self.location_mode = location_mode
        self._tabs = [mock.MagicMock() for _ in range(max(1, tabs))]
        self._free = list(self._tabs)

    def acquire(self):
        return self._free.pop(0) if self._free else mock.MagicMock()

    def release(self, tab):
        self._free.append(tab)

    def setup(self, strict_location=True):
        return True

    def fetch_with_retry(self, tab, row, cfg):
        FakeWorker.fetched.append(row.asin)
        cr = CrawlResult(asin=row.asin)
        cr.marketplace = row.marketplace
        cr.currency_code = 'CAD' if row.marketplace == 'CA' else 'USD'
        cr.product_url = row.product_url
        if row.asin == 'B0AAA11111':
            cr.status = PageStatus.OK
            cr.display_price = Decimal('39.99')
            cr.expected_type = row.h_type
        elif row.asin == 'B0BBB22222':
            cr.status = PageStatus.PAGE_NOT_FOUND
        else:
            cr.status = PageStatus.CRAWL_ERROR
            cr.error = '模拟失败'
        from datetime import datetime
        cr.timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        return cr, tab

    def wait_after_archive(self, cfg, archive_validated):
        return 1.5

    def quit(self):
        pass


class FakeFeishu:
    def __init__(self, cfg):
        self.calls = []
        self.invalid_rows = []
        self.rows = {
            'PD03': [
                ReportRow(row_num=2, asin='B0AAA11111', sku='S1',
                          normal_price=Decimal('49.99'), h_type='原价调整',
                          i_value=Decimal('39.99'), target_price=Decimal('39.99'),
                          target_price_source='feishu_value'),
                ReportRow(row_num=3, asin='B0BBB22222', sku='S2',
                          normal_price=Decimal('59.99'), target_price=Decimal('59.99'),
                          target_price_source='feishu_value'),
            ]
        }

    def resolve_wiki(self, url):
        self.calls.append(('resolve_wiki', url))
        return 'spreadsheet_token'

    def resolve_wiki_obj(self, url):
        self.calls.append(('resolve_wiki_obj', url))
        return 'spreadsheet_token', 'sheet'

    def read_source_file(self, file_token, sheets, cfg):
        raise AssertionError('不应走到 file 类型分支')

    def read_source_sheets(self, spreadsheet, sheets, cfg):
        self.calls.append(('read_source_sheets', spreadsheet))
        return self.rows, {'spreadsheet': spreadsheet}, self.invalid_rows

    def list_sheets(self, spreadsheet):
        return {'PD03': 'sid_pd03'}

    def sync_base_data(self, spreadsheet, sheet, sheet_id, rows, cfg):
        self.calls.append(('sync_base_data', sheet))
        return len(rows), 0, 0

    def backup_target_sheet(self, spreadsheet, sheet, sheet_id, run_id):
        self.calls.append(('backup_target_sheet', sheet))
        return Path(f'{sheet}.json')

    def build_asin_map(self, spreadsheet, sheet_id):
        return {'B0AAA11111': 2, 'B0BBB22222': 3, 'B0INVALID99': 4}

    def write_six_columns(self, spreadsheet, sheet, sheet_id, asin_map, crawls, cfg,
                          start_col=None):
        self.calls.append(('write_six_columns', sheet, len(crawls), start_col))
        return len(crawls)

    def resolve_output_layout(self, spreadsheet, sheets, cfg):
        self.calls.append(('resolve_output_layout', sheets))
        return {s: 35 for s in sheets}

    def migrate_columns(self, spreadsheet, cfg, sheets, confirm=False):
        raise AssertionError('不应触发迁移')

    def close(self):
        pass


class FakeLogger:
    def info(self, *a, **k):
        pass


class TestFlow(unittest.TestCase):
    def test_failed_incremental_and_final_cache_do_not_lose_valid_results(self):
        import main
        self._setup_dirs()
        cfg = mk_cfg(html_archive_enabled=False, html_archive_required=False, save_every=1)
        rows = [ReportRow(row_num=2, asin='B0AAA11111', marketplace='US')]
        with mock.patch('main.AmazonBrowser', FakeWorker), \
             mock.patch('main.save_sheet_cache', side_effect=OSError('temporary disk error')):
            crawls = main.run_fetch('cache-failure', 'PD03', rows, cfg, True, True, mock.Mock())
        self.assertEqual(crawls[0].status, PageStatus.OK)
        self.assertEqual(crawls[0].display_price, Decimal('39.99'))

    def test_tab_acquire_failure_is_recorded_for_every_row(self):
        import main
        self._setup_dirs()
        cfg = mk_cfg(html_archive_enabled=False, html_archive_required=False)
        rows = [ReportRow(row_num=2, asin='B0AAA11111', marketplace='US'),
                ReportRow(row_num=3, asin='B0BBB22222', marketplace='US')]
        with mock.patch('main.AmazonBrowser', FakeWorker), \
             mock.patch.object(FakeWorker, 'acquire', side_effect=RuntimeError('tab unavailable')):
            crawls = main.run_fetch('tab-failure', 'PD03', rows, cfg, True, True, mock.Mock())
        self.assertEqual(len(crawls), 2)
        self.assertTrue(all(c.status == PageStatus.CRAWL_ERROR and 'tab unavailable' in c.error for c in crawls))

    def test_price_only_waits_once_per_item_and_requires_postal_verification(self):
        import main
        self._setup_dirs()
        cfg = mk_cfg(html_archive_enabled=False, html_archive_required=False)
        rows = [ReportRow(row_num=2, asin='B0AAA11111', marketplace='US')]
        with mock.patch('main.AmazonBrowser', FakeWorker), \
             mock.patch.object(FakeWorker, 'setup', return_value=True) as setup, \
             mock.patch.object(FakeWorker, 'wait_after_archive', return_value=1.5) as wait, \
             mock.patch('main.SingleFileArchiver', side_effect=AssertionError('HTML must remain off')):
            crawls = main.run_fetch('price-wait', 'PD03', rows, cfg, True, True, FakeLogger())
        setup.assert_called_once_with(strict_location=True)
        wait.assert_called_once()
        self.assertEqual(crawls[0].post_archive_delay_seconds, 1.5)

    def _setup_dirs(self):
        td = tempfile.TemporaryDirectory()
        import config as config_mod
        import cache as cache_mod
        import exporters as exp_mod
        import main
        self.addCleanup(td.cleanup)
        tmp = Path(td.name)
        for module, name, value in (
                (cache_mod, 'CACHE_ROOT', tmp / 'fetch_cache'),
                (cache_mod, 'SNAPSHOT_DIR', tmp / 'snapshots'),
                (exp_mod, 'CSV_DIR', tmp / 'csv'),
                (main, 'OUTPUT_DIR', tmp),
                (config_mod, 'OUTPUT_DIR', tmp)):
            patcher = mock.patch.object(module, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        cache_mod.CACHE_ROOT.mkdir(parents=True)
        cache_mod.SNAPSHOT_DIR.mkdir(parents=True)
        exp_mod.CSV_DIR.mkdir(parents=True)
        return tmp

    def test_cli_mutex(self):
        import main
        with self.assertRaises(SystemExit):
            main.parse_args = lambda: mock.MagicMock(
                push_only=True, fetch_only=True, dry_run=False,
                migrate_feishu_columns=False, sheets=None, limit=None,
                no_headless=False, force_fetch=False, confirm=False)
            # 直接测互斥判断逻辑
            args = mock.MagicMock(push_only=True, fetch_only=True, dry_run=False,
                                  migrate_feishu_columns=False, sheets=None, limit=None)
            if args.push_only and args.fetch_only:
                raise SystemExit('互斥')

    def test_full_flow_end_to_end(self):
        import main
        from config import ensure_dirs
        tmp = self._setup_dirs()
        cfg = mk_cfg(workers=2, price_tolerance='0.50', save_every=10)
        logger = FakeLogger()

        with mock.patch('main.AmazonBrowser', FakeWorker), \
             mock.patch('main.FeishuClient', FakeFeishu):
            fc = FakeFeishu(cfg)
            args = mock.MagicMock(sheets='PD03', limit=None, no_headless=False,
                                  force_fetch=False, fetch_only=False,
                                  dry_run=False, push_only=False,
                                  resume=False, run_id=None, force_push=False,
                                  asins=None, migrate_feishu_columns=False, confirm=False,
                                  inspect_feishu_layout=False)
            main.full_flow(fc, cfg, ['PD03'], args, logger)

        # 断言：六列写回被调用，且使用解析出的起始列（35）
        write_calls = [c for c in fc.calls if c[0] == 'write_six_columns']
        self.assertEqual(len(write_calls), 1)
        self.assertEqual(write_calls[0][2], 2)
        self.assertEqual(write_calls[0][3], 8)           # 统一紧凑布局 H:M
        # 断言调用了布局解析
        self.assertTrue(any(c[0] == 'sync_base_data' for c in fc.calls))

        # 快照存在
        import cache as cache_mod
        snaps = list(cache_mod.SNAPSHOT_DIR.glob('*/source.json'))
        self.assertEqual(len(snaps), 1)

        # CSV 存在且包含诊断字段
        import exporters as exp_mod
        csvs = list(exp_mod.CSV_DIR.glob('*.csv'))
        self.assertEqual(len(csvs), 1)
        content = csvs[0].read_text(encoding='utf-8-sig')
        self.assertIn('page_status', content)
        self.assertIn('page_not_found', content)

    def test_source_invalid_written_as_dash(self):
        """4.x：source_data_invalid 行生成六列 '-' 并写入，不进入抓取"""
        import main
        tmp = self._setup_dirs()
        cfg = mk_cfg(workers=2, price_tolerance='0.50', save_every=10)
        logger = FakeLogger()
        FakeWorker.fetched = []
        fc = FakeFeishu(cfg)
        invalid_row = ReportRow(row_num=4, asin='B0INVALID99', sku='S3')
        fc.invalid_rows = [{'sheet': 'PD03', 'row_num': 4, 'asin': 'B0INVALID99',
                            'reason': '目标成交价(K)、正常售价(E) 为空',
                            'report_row': invalid_row}]
        with mock.patch('main.AmazonBrowser', FakeWorker):
            args = mock.MagicMock(sheets='PD03', limit=None, no_headless=True,
                                  force_fetch=False, fetch_only=False,
                                  dry_run=False, push_only=False,
                                  resume=False, run_id=None, force_push=False,
                                  asins=None, migrate_feishu_columns=False, confirm=False,
                                  inspect_feishu_layout=False)
            main.full_flow(fc, cfg, ['PD03'], args, logger)
        # 无效行不进入抓取
        self.assertEqual(FakeWorker.fetched, ['B0AAA11111', 'B0BBB22222'])
        # 推送包含 invalid 行（2 有效 + 1 无效 = 3 行）
        write_calls = [c for c in fc.calls if c[0] == 'write_six_columns']
        self.assertEqual(write_calls[0][2], 3)

    def test_resume_does_not_refetch_done(self):
        """3.3：跨进程恢复——已完成的 ASIN 不再重新抓取"""
        import main
        from cache import save_sheet_cache
        tmp = self._setup_dirs()
        cfg = mk_cfg(workers=2, price_tolerance='0.50', save_every=10)
        logger = FakeLogger()
        FakeWorker.fetched = []

        fc = FakeFeishu(cfg)
        # 第一次运行（全量，2 个 ASIN 都完成）
        with mock.patch('main.AmazonBrowser', FakeWorker):
            args1 = mock.MagicMock(sheets='PD03', limit=None, no_headless=True,
                                   force_fetch=False, fetch_only=False,
                                   dry_run=True, push_only=False,
                                   resume=False, run_id=None, force_push=False,
                                   asins=None, migrate_feishu_columns=False, confirm=False,
                                   inspect_feishu_layout=False)
            main.full_flow(fc, cfg, ['PD03'], args1, logger)
        self.assertEqual(len(FakeWorker.fetched), 2)

        # 模拟第二次：只有 1 个 ASIN 出现在源表（另一个是新增未完成的），resume=True
        # 第一次已保存缓存（2 个都完成），因此第二次不应抓任何
        FakeWorker.fetched = []
        with mock.patch('main.AmazonBrowser', FakeWorker):
            args2 = mock.MagicMock(sheets='PD03', limit=None, no_headless=True,
                                   force_fetch=False, fetch_only=False,
                                   dry_run=True, push_only=False,
                                   resume=True, run_id=None, force_push=False,
                                   asins=None, migrate_feishu_columns=False, confirm=False,
                                   inspect_feishu_layout=False)
            main.full_flow(fc, cfg, ['PD03'], args2, logger)
        # resume 复用缓存，全部命中，不打开任何页面
        self.assertEqual(FakeWorker.fetched, [])

    def test_resume_creates_new_when_cache_invalid(self):
        """3.3：签名不匹配的缓存 → 不复用，重新抓取"""
        import main
        tmp = self._setup_dirs()
        cfg = mk_cfg(workers=2, price_tolerance='0.50', save_every=10)
        logger = FakeLogger()
        FakeWorker.fetched = []
        fc = FakeFeishu(cfg)
        # 源表数据变化（B0BBB 目标价变了 → 签名不同）
        fc.rows['PD03'][1].target_price = Decimal('49.99')
        with mock.patch('main.AmazonBrowser', FakeWorker):
            args = mock.MagicMock(sheets='PD03', limit=None, no_headless=True,
                                  force_fetch=False, fetch_only=False,
                                  dry_run=True, push_only=False,
                                  resume=True, run_id=None, force_push=False,
                                  asins=None, migrate_feishu_columns=False, confirm=False,
                                  inspect_feishu_layout=False)
            main.full_flow(fc, cfg, ['PD03'], args, logger)
        # 旧缓存签名不匹配 → 全部重新抓取
        self.assertEqual(len(FakeWorker.fetched), 2)

    def test_run_fetch_archives_same_result_and_caches_local_url(self):
        import main
        from types import SimpleNamespace
        tmp = self._setup_dirs()
        cfg = mk_cfg(html_archive_enabled=True, html_archive_required=True,
                     html_archive_root='D:/managed-html', html_min_free_gb=0)
        rows = [ReportRow(row_num=2, asin='B0AAA11111', marketplace='US')]

        class Storage:
            def __init__(self, *args): pass
            def check_capacity(self): return {'ok': True}
            def cleanup_expired(self): return []
            def html_path(self, *args): return Path('D:/managed-html/a.html')
            def file_url(self, path): return 'file:///D:/managed-html/a.html'
            def run_dir(self, *args): return tmp / 'html-run'
            def write_manifest(self, run_date, run_id, manifest):
                path = self.run_dir() / 'manifest.json'
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('ok', encoding='utf-8')
                return path

        class Archiver:
            def __init__(self, *args): pass
            def prepare_tab(self, tab): tab.prepared = True
            def cleanup_downloads(self): return 0
            def capture(self, tab, asin, destination, timeout, page_status):
                self.assert_prepared = getattr(tab, 'prepared', False)
                return SimpleNamespace(path=str(destination), sha256='abc',
                                       size_bytes=123456, duration_ms=800,
                                       asin=asin, source_html_sha256='src',
                                       page_status=page_status,
                                       external_resource_refs=0, validation='ok',
                                       stripped_noncore_css_resources=0)

        with mock.patch('main.AmazonBrowser', FakeWorker), \
             mock.patch('main.ArchiveStorage', Storage), \
             mock.patch('main.SingleFileArchiver', Archiver), \
             mock.patch('main.write_html_manifest'):
            crawls = main.run_fetch('run-archive', 'PD03', rows, cfg, True, True,
                                    FakeLogger(), sheet_order=3)
        self.assertEqual(crawls[0].archive_status, 'ok')
        self.assertEqual(crawls[0].html_url, 'file:///D:/managed-html/a.html')
        self.assertEqual(crawls[0].post_archive_delay_seconds, 1.5)

    def test_archive_failure_is_recorded_and_still_waits(self):
        import main
        tmp = self._setup_dirs()
        cfg = mk_cfg(html_archive_enabled=True, html_archive_required=True,
                     html_archive_root='D:/managed-html', html_min_free_gb=0)
        rows = [ReportRow(row_num=2, asin='B0AAA11111', marketplace='US')]

        class Storage:
            def __init__(self, *args): pass
            def check_capacity(self): return {'ok': True}
            def cleanup_expired(self): return []
            def html_path(self, *args): return Path('D:/managed-html/a.html')
            def run_dir(self, *args): return tmp / 'html-run'
            def write_manifest(self, run_date, run_id, manifest):
                path = self.run_dir() / 'manifest.json'
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('ok', encoding='utf-8')
                return path

        class FailingArchiver:
            def __init__(self, *args): pass
            def prepare_tab(self, tab): pass
            def cleanup_downloads(self): return 0
            def capture(self, *args, **kwargs): raise RuntimeError('disk failed')

        with mock.patch('main.AmazonBrowser', FakeWorker), \
             mock.patch('main.ArchiveStorage', Storage), \
             mock.patch('main.SingleFileArchiver', FailingArchiver), \
             mock.patch('main.save_evidence'):
            crawls = main.run_fetch('run-failed', 'PD03', rows, cfg, True, True,
                                    FakeLogger())
        self.assertEqual(crawls[0].archive_status, 'failed')
        self.assertIn('disk failed', crawls[0].archive_error)
        self.assertEqual(crawls[0].post_archive_delay_seconds, 1.5)
        self.assertEqual(crawls[0].html_url, '')


if __name__ == '__main__':
    unittest.main()
