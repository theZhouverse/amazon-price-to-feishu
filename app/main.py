# -*- coding: utf-8 -*-
"""main.py — 正式流程：登记表 → 当批周报副本 → 抓取计算 → 固定结果表A:V。

正式入口 --weekly-run --confirm；恢复 --weekly-push-only --run-id。
所有CLI共享运行锁。以下旧参数仅供受限制的兼容/维护流程，不是正式全量入口。

CLI（方案 19 + 当前测试方案修正）：
  --sheets PD03,PD17       只处理指定子表
  --asins B0...,B0...      只对指定 ASIN 实时抓取（默认 dry-run，在线专项测试）
  --limit 10               每表前 N 行（自动隐含 --dry-run）
  --no-headless            显示浏览器
  --force-fetch            忽略缓存重新抓取
  --fetch-only             同步数据并抓取，不写飞书商品结果列
  --push-only              用最近快照缓存推送，不抓取
  --dry-run                读取+计算+本地输出，不改飞书
  --resume                 恢复最近有效的未完成批次（跨进程断点续跑）
  --run-id <id>            明确恢复指定批次
  --force-push             技术异常比例超阈值时仍写入（人工确认恢复推送）
  --inspect-feishu-layout  只读预检目标表布局，不写任何数据
  --migrate-feishu-columns [--sheets ...] --confirm  兼容旧维护流程的一次性六列表头迁移
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import queue
import sys
import threading
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

from amazon.crawler import AmazonBrowser
from cache import (
    SCHEMA_VERSION, validate_recovery_metadata, validate_record_ages,
    cleanup_debug_dirs, is_cache_valid, load_latest_snapshot, load_sheet_cache,
    make_run_id, records_to_crawls, restore_from_cache, save_sheet_cache,
    save_snapshot,
)
from config import (BASE_DIR, DEBUG_DIR, LOG_DIR, OUTPUT_DIR, PROJECT_ROOT,
                    ensure_dirs, load_config)
from diagnostics import save_evidence
from exporters import export_results
from feishu import FeishuClient, col_letter
from models import CrawlResult, PageStatus, ReportRow
from pricing import compute_result, dec
from weekly_registry import select_current_registry_row, select_for_scheduled_slot
from weekly_assets import WeeklyAssetStore, initialize_weekly_assets, require_business_ready
from weekly_result import (_read_source_plan, sync_weekly_result_base, base_fingerprint,
                           write_weekly_result_columns)
from frontend_checks import (frontend_status_counts, FRONTEND_CHECK_RULE_VERSION,
                             FRONTEND_HEADERS)
from archive_storage import ArchiveStorage
from html_archive import SingleFileArchiver, write_manifest as write_html_manifest
from weekly_mapping import build_discovery, save_discovery, validate_discovery
from product_links import audit_manifest_links, save_link_audit
from weekly_execution import price_only_config, ensure_price_week, atomic_json
from publication_guard import assert_latest_run
from registry_freshness import assert_registry_fresh
from runtime_state import exclusive_run, RunBusy


# ============================ 日志 ============================
def setup_logger(cfg: dict) -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M')
    log_file = LOG_DIR / f'run_{ts}.log'
    logger = logging.getLogger('daily')
    logger.setLevel(logging.INFO)
    if logger.handlers:
        for h in list(logger.handlers):
            logger.removeHandler(h)
    fmt = logging.Formatter('%(asctime)s %(levelname)s %(message)s', datefmt='%H:%M:%S')
    fh = logging.FileHandler(log_file, encoding='utf-8')
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    logs = sorted(LOG_DIR.glob('run_*.log'))
    while len(logs) > cfg['log_keep']:
        logs[0].unlink()
        logs = logs[1:]
    return logger


def p(logger, msg: str):
    print(msg, flush=True)
    logger.info(msg)


# ============================ CLI ============================
def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description='Amazon周报前端价格捕捉：--weekly-run --confirm')
    ap.add_argument('--sheets', default=None, help='逗号分隔；正式周报默认发现全部业务子表（含CPD）')
    ap.add_argument('--asins', default=None, help='逗号分隔 ASIN，只抓指定商品(自动 dry-run)')
    ap.add_argument('--limit', type=int, default=None, help='每表前 N 行(自动隐含 --dry-run)')
    ap.add_argument('--no-headless', action='store_true', help='显示浏览器调试')
    ap.add_argument('--force-fetch', action='store_true', help='忽略缓存重新抓取')
    ap.add_argument('--fetch-only', action='store_true', help='只读+抓取，不写飞书')
    ap.add_argument('--push-only', action='store_true', help='已停用；请使用 --weekly-push-only --run-id 批次 --confirm')
    ap.add_argument('--dry-run', action='store_true', help='只读+计算+本地输出，不改飞书')
    ap.add_argument('--resume', action='store_true', help='恢复最近有效的未完成批次')
    ap.add_argument('--run-id', default=None, help='明确恢复指定批次(排错用)')
    ap.add_argument('--scheduled-slot', default='manual',
                    choices=('manual', 'monday_0730', 'monday_1530',
                             'weekday_0730', 'weekday_1530'),
                    help='调度计划槽位；补跑仍按原槽位选择来源，人工运行使用 manual')
    ap.add_argument('--force-push', action='store_true', help='异常比例超阈值时仍写入飞书')
    ap.add_argument('--inspect-feishu-layout', action='store_true',
                    help='只读预检目标表布局(表头行/ASIN起始行/旧列/J:O)')
    ap.add_argument('--inspect-weekly-registry', action='store_true',
                    help='只读解析固定周报登记表并选择当前有效周报')
    ap.add_argument('--create-snapshot-poc', action='store_true',
                    help='创建一个 TEST 周报完整副本并校验（必须配合 --confirm）')
    ap.add_argument('--create-result-poc', action='store_true',
                    help='创建一个 TEST 独立结果 Spreadsheet 与 A:V 表头（必须配合 --confirm）')
    ap.add_argument('--new-week', action='store_true',
                    help='按固定登记表幂等初始化本周正式快照与独立结果表（必须配合 --confirm）')
    ap.add_argument('--recreate-weekly-assets', action='store_true',
                    help='保留旧记录并重建本周正式资源（必须配合 --confirm）')
    ap.add_argument('--discover-weekly-mapping', action='store_true',
                    help='只读发现正式快照子表并生成 US/CA 结果表映射报告')
    ap.add_argument('--audit-product-links', action='store_true',
                    help='只读提取正式快照 ASIN 并生成 US/CA 标准商品 URL 报告')
    ap.add_argument('--sync-weekly-result-base', action='store_true',
                    help='从正式快照初始化/同步独立结果表 A:G（必须 --confirm）')
    ap.add_argument('--weekly-run', action='store_true',
                    help='新批次复制周报；刷新固定结果表A:G、H:M、N:T和U:V')
    ap.add_argument('--notify-manager-only', action='store_true',
                    help='本次正式运行只通知 feishu_manager_open_id；不改变默认协作者范围')
    ap.add_argument('--weekly-push-only', action='store_true',
                    help='用当前有效weekly-run结果恢复固定表A:V（必须 --run-id --confirm）')
    ap.add_argument('--amazon-poc-marketplace', choices=('US', 'CA'), default=None,
                    help='R1.7 单 ASIN Amazon 页面 PoC Marketplace')
    ap.add_argument('--amazon-poc-asin', default=None,
                    help='R1.7 单 ASIN Amazon 页面 PoC 商品编号')
    ap.add_argument('--migrate-feishu-columns', action='store_true',
                    help='一次性清理旧列并写六列表头(可配合 --sheets 限定范围)')
    ap.add_argument('--confirm', action='store_true', help='确认执行破坏性迁移')
    args = ap.parse_args()
    if args.run_id:
        from weekly_assets import safe_period_id
        safe_period_id(args.run_id)
    if args.limit is not None and args.limit <= 0:
        raise SystemExit('--limit 必须大于0')

    # 互斥规则
    if args.push_only and args.fetch_only:
        raise SystemExit('--push-only 与 --fetch-only 互斥')
    if args.push_only and args.dry_run:
        raise SystemExit('--push-only 与 --dry-run 互斥')
    if args.push_only and (args.force_fetch or args.resume):
        raise SystemExit('--push-only 不能与 --force-fetch/--resume 同用')
    inspect_commands = (int(args.inspect_feishu_layout) + int(args.inspect_weekly_registry)
                        + int(args.create_snapshot_poc))
    inspect_commands += int(args.create_result_poc)
    inspect_commands += int(args.new_week) + int(args.recreate_weekly_assets)
    inspect_commands += int(args.discover_weekly_mapping)
    inspect_commands += int(args.audit_product_links)
    inspect_commands += int(args.sync_weekly_result_base)
    inspect_commands += int(args.weekly_run)
    inspect_commands += int(args.weekly_push_only)
    inspect_commands += int(bool(args.amazon_poc_marketplace or args.amazon_poc_asin))
    if inspect_commands > 1:
        raise SystemExit('只读预检命令不能同时使用')
    if args.inspect_feishu_layout and (args.migrate_feishu_columns or args.fetch_only or args.push_only):
        raise SystemExit('--inspect-feishu-layout 是独立只读命令，不能与其他写操作同用')
    if args.inspect_weekly_registry and (args.migrate_feishu_columns or args.fetch_only
                                         or args.push_only or args.force_fetch
                                         or args.resume or args.force_push):
        raise SystemExit('--inspect-weekly-registry 是独立只读命令，不能与抓取或写操作同用')
    if args.create_snapshot_poc and not args.confirm:
        raise SystemExit('--create-snapshot-poc 会创建飞书副本，必须同时提供 --confirm')
    if args.create_snapshot_poc and (args.migrate_feishu_columns or args.fetch_only
                                     or args.push_only or args.force_fetch or args.resume
                                     or args.force_push or args.dry_run or args.limit
                                     or args.asins):
        raise SystemExit('--create-snapshot-poc 是独立资源创建命令，不能与抓取或其他写操作同用')
    if args.create_result_poc and not args.confirm:
        raise SystemExit('--create-result-poc 会创建并写入 TEST 结果表，必须同时提供 --confirm')
    if args.create_result_poc and (args.migrate_feishu_columns or args.fetch_only
                                   or args.push_only or args.force_fetch or args.resume
                                   or args.force_push or args.dry_run or args.limit
                                   or args.asins):
        raise SystemExit('--create-result-poc 是独立资源创建命令，不能与抓取或其他写操作同用')
    if (args.new_week or args.recreate_weekly_assets) and not args.confirm:
        raise SystemExit('每周资源初始化会创建飞书资源，必须同时提供 --confirm')
    if (args.new_week or args.recreate_weekly_assets) and (
            args.migrate_feishu_columns or args.fetch_only or args.push_only
            or args.force_fetch or args.resume or args.force_push or args.dry_run
            or args.limit or args.asins):
        raise SystemExit('每周资源初始化是独立命令，不能与抓取或其他写操作同用')
    if args.discover_weekly_mapping and (
            args.migrate_feishu_columns or args.fetch_only or args.push_only
            or args.force_fetch or args.resume or args.force_push or args.dry_run
            or args.limit or args.asins or args.confirm):
        raise SystemExit('--discover-weekly-mapping 是独立只读飞书发现命令，不能与其他操作同用')
    if args.audit_product_links and (
            args.migrate_feishu_columns or args.fetch_only or args.push_only
            or args.force_fetch or args.resume or args.force_push or args.dry_run
            or args.limit or args.asins or args.confirm):
        raise SystemExit('--audit-product-links 是独立只读飞书审计命令，不能与其他操作同用')
    if args.sync_weekly_result_base and not args.confirm:
        raise SystemExit('--sync-weekly-result-base 会写入独立结果表，必须同时提供 --confirm')
    if args.sync_weekly_result_base and (
            args.migrate_feishu_columns or args.fetch_only or args.push_only
            or args.force_fetch or args.resume or args.force_push or args.dry_run
            or args.limit or args.asins):
        raise SystemExit('--sync-weekly-result-base 是独立写入命令，不能与抓取或其他操作同用')
    if args.weekly_run and args.push_only:
        raise SystemExit('--weekly-run 暂不能与旧 --push-only 同用')
    if args.weekly_run and not (args.dry_run or args.fetch_only or args.limit or args.asins) \
            and not args.confirm:
        raise SystemExit('--weekly-run 正式写入独立结果表时必须同时提供 --confirm')
    if args.notify_manager_only and not args.weekly_run:
        raise SystemExit('--notify-manager-only 只能与 --weekly-run 同用')
    if args.weekly_push_only and (not args.run_id or not args.confirm):
        raise SystemExit('--weekly-push-only 必须同时提供 --run-id 和 --confirm')
    if args.weekly_push_only and (
            args.push_only or args.fetch_only or args.dry_run or args.force_fetch
            or args.resume or args.force_push or args.limit or args.asins):
        raise SystemExit('--weekly-push-only 是独立恢复写入命令，不能与抓取参数同用')
    if bool(args.amazon_poc_marketplace) != bool(args.amazon_poc_asin):
        raise SystemExit('--amazon-poc-marketplace 与 --amazon-poc-asin 必须同时提供')
    if args.amazon_poc_marketplace and (
            args.migrate_feishu_columns or args.fetch_only or args.push_only
            or args.force_fetch or args.resume or args.force_push or args.dry_run
            or args.limit or args.asins or args.confirm):
        raise SystemExit('Amazon 单 ASIN PoC 是独立命令，不能与其他操作同用')
    if args.migrate_feishu_columns and (args.fetch_only or args.push_only or args.dry_run or args.limit):
        raise SystemExit('列迁移不能与抓取/推送参数同时使用(--sheets 可用于限定范围)')
    if args.limit:
        args.dry_run = True                # --limit 隐含 --dry-run
    if args.asins:
        args.dry_run = True                # --asins 在线专项测试默认 dry-run
    if args.asins and args.limit:
        raise SystemExit('--asins 与 --limit 不能同用')
    return args


# ============================ 数据还原 ============================
def row_from_dict(d: dict) -> ReportRow:
    return ReportRow(
        row_num=int(d.get('row_num') or 0),
        asin=d.get('asin') or '',
        sku=d.get('sku') or '',
        size=d.get('size') or '',
        normal_price=dec(d.get('normal_price')),
        h_type=d.get('h_type') or '',
        i_value=dec(d.get('i_value')),
        target_price=dec(d.get('target_price')),
        target_price_source=d.get('target_price_source') or 'missing',
        marketplace=d.get('marketplace') or 'US',
        product_url=d.get('product_url') or '',
        source_product_url=d.get('source_product_url') or '',
    )


def assemble_crawls(rows: list[ReportRow], results_map: dict[str, CrawlResult],
                    cfg: dict, logger) -> list[CrawlResult]:
    """按行顺序组装，补计算字段；缺失任务标 crawl_error"""
    crawls = []
    for r in rows:
        cr = results_map.get(r.asin)
        if cr is None:
            cr = CrawlResult(asin=r.asin, status=PageStatus.CRAWL_ERROR, error='任务未完成')
        if not cr.expected_type:
            cr.expected_type = r.h_type
        compute_result(r, cr, cfg['price_tolerance'])
        crawls.append(cr)
    return crawls


# ============================ 抓取 ============================
def run_fetch(run_id: str, sheet: str, rows: list[ReportRow], cfg: dict,
              headless: bool, force_fetch: bool, logger,
              sheet_order: int = 1) -> list[CrawlResult]:
    marketplace = (cfg.get('sheet_profiles') or {}).get(sheet, 'US')
    from product_links import MARKETPLACES
    profile = MARKETPLACES[marketplace]
    for row in rows:
        row.marketplace = marketplace
        row.product_url = row.product_url or profile.product_url(row.asin)
    reuse: dict[str, CrawlResult] = {}
    if not force_fetch:
        meta = load_sheet_cache(run_id, sheet)
        if is_cache_valid(meta, cfg, sheet, rows):
            reuse = restore_from_cache(meta, rows)
    todo = [r for r in rows if r.asin not in reuse]
    workers = min(int(cfg.get('workers', 1)), max(1, len(todo)))
    p(logger, f'[抓取] {sheet}/{marketplace} 共{len(rows)}行 | 缓存复用 {len(reuse)} '
              f'| 需抓 {len(todo)} | workers={workers}')

    results_map: dict[str, CrawlResult] = dict(reuse)
    if todo:
        q: queue.Queue = queue.Queue()
        for r in todo:
            q.put(r)
        done_count = [0]
        lock = threading.Lock()
        incomplete_count = cfg.get('_run_incomplete_count') or [0]
        circuit_open = cfg.get('_run_circuit_open') or threading.Event()
        circuit_threshold = max(1, int(cfg.get(
            'incomplete_page_circuit_threshold', 8)))
        cache_write_lock = threading.Lock()
        save_state = [0]
        total = len(todo)

        def _save_partial():
            # Serialize snapshot creation AND writes; an older snapshot cannot win last.
            with cache_write_lock:
                with lock:
                    snapshot = dict(results_map)
                crawls = assemble_crawls(rows, snapshot, cfg, logger)
                save_sheet_cache(run_id, sheet, rows, crawls, cfg)
            logger.info(f'[缓存] {sheet} 增量保存 {len(snapshot)} 条')

        location_mode = (cfg.get('ca_location_mode') if marketplace == 'CA'
                         else cfg.get('us_location_mode')) or (
                             'postal' if marketplace == 'CA' else 'proxy')
        # US 默认信任代理出口，不传入兼容旧配置的 90210；CA 才传入
        # 独立加拿大邮编，并且永远不会把邮编拼到商品 URL。
        postal_code = (cfg['ca_postal'] if marketplace == 'CA'
                       and location_mode == 'postal' else None)
        if circuit_open.is_set():
            return [CrawlResult(
                asin=row.asin, run_id=run_id, marketplace=marketplace,
                product_url=row.product_url,
                source_product_url=getattr(row, 'source_product_url', ''),
                status=PageStatus.CRAWL_ERROR,
                error='batch_circuit_breaker: 运行级残缺商品页熔断，未继续请求')
                    for row in rows]
        browser = AmazonBrowser(headless=headless, us_zip=cfg['us_zip'],
                                proxy=cfg.get('proxy') or None,
                                tabs=workers, marketplace=marketplace,
                                postal_code=postal_code,
                                location_mode=location_mode,
                                browser_auto_port=bool(cfg.get('browser_auto_port', True)),
                                browser_no_sandbox=bool(cfg.get('browser_no_sandbox', True)),
                                browser_disable_gpu=bool(cfg.get('browser_disable_gpu', True)),
                                logger=logger)
        archive_enabled = bool(cfg.get('html_archive_enabled', False))
        storage = None
        archiver = None
        item_orders = {row.asin: index for index, row in enumerate(rows, start=1)}
        try:
            if not browser.setup(strict_location=True):
                raise RuntimeError(f'浏览器初始化失败(请确认 {marketplace} 出口与邮编)')
            if archive_enabled:
                storage = ArchiveStorage(
                    cfg['html_archive_root'], cfg['html_retention_days'],
                    cfg['html_min_free_gb'], cfg.get('_html_server_base_url', ''))
                storage.check_capacity()
                storage.cleanup_expired()
                archiver = SingleFileArchiver(
                    PROJECT_ROOT, OUTPUT_DIR / 'archive_work' / marketplace)

            def _worker_loop():
                while True:
                    if circuit_open.is_set():
                        break
                    try:
                        row = q.get_nowait()
                    except queue.Empty:
                        break
                    tab = None
                    try:
                        tab = browser.acquire()
                        item_started = time.monotonic()
                        if archiver is not None:
                            archiver.prepare_tab(tab)
                        cr, tab = browser.fetch_with_retry(tab, row, cfg)
                        cr.run_id = run_id
                        if cr.status in (PageStatus.OK, PageStatus.SOLD_OUT,
                                         PageStatus.PAGE_NOT_FOUND) and archiver is not None:
                            destination = storage.html_path(
                                datetime.now().date(), run_id, sheet_order, sheet,
                                item_orders[row.asin], row.asin)
                            try:
                                remaining = (float(cfg['per_asin_timeout'])
                                             - (time.monotonic() - item_started))
                                if remaining <= 1:
                                    raise TimeoutError(
                                        'deadline_exceeded: 页面阶段已耗尽归档时间预算')
                                archived = archiver.capture(
                                    tab, row.asin, destination,
                                    timeout=remaining,
                                    page_status=cr.status.value)
                                write_html_manifest(
                                    archived, destination.with_suffix('.manifest.json'))
                                cr.html_path = archived.path
                                cr.html_url = storage.file_url(destination)
                                cr.html_sha256 = archived.sha256
                                cr.html_size_bytes = archived.size_bytes
                                cr.archive_ms = archived.duration_ms
                                cr.archive_status = 'ok'
                                cr.stripped_noncore_css_resources = (
                                    archived.stripped_noncore_css_resources)
                            except Exception as archive_exc:
                                cr.archive_status = 'failed'
                                cr.archive_error = (
                                    f'{type(archive_exc).__name__}: {str(archive_exc)[:160]}')
                            finally:
                                cr.duration_ms = int(
                                    (time.monotonic() - item_started) * 1000)
                        if cr.status in (PageStatus.CRAWL_ERROR, PageStatus.IDENTITY_MISMATCH,
                                         PageStatus.PARSE_ERROR, PageStatus.CURRENCY_ERROR) \
                                or cr.archive_status == 'failed':
                            save_evidence(run_id, sheet, cr, tab, cfg)
                    except Exception as e:
                        cr = CrawlResult(asin=row.asin, marketplace=row.marketplace,
                                         product_url=row.product_url,
                                         source_product_url=getattr(
                                             row, 'source_product_url', ''))
                        cr.run_id = run_id
                        cr.status = PageStatus.CRAWL_ERROR
                        cr.error = f'{type(e).__name__}: {str(e)[:80]}'
                        cr.timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    finally:
                        try:
                            cr.post_archive_delay_seconds = browser.wait_after_archive(
                                cfg, archive_validated=cr.archive_status == 'ok')
                        except Exception as exc:
                            cr.status, cr.error = PageStatus.CRAWL_ERROR, f'商品间等待失败: {exc}'
                            logger.error(cr.error)
                        finally:
                            if tab is not None:
                                browser.release(tab)
                    if (cr.error or '').startswith('incomplete_product_page'):
                        with lock:
                            incomplete_count[0] += 1
                            if incomplete_count[0] >= circuit_threshold:
                                circuit_open.set()
                                logger.error(
                                    f'[熔断] {sheet} 连续收到 {incomplete_count[0]} 个残缺商品页，'
                                    '停止继续请求本表；剩余行进入恢复清单')
                    should_save = False
                    with lock:
                        results_map[row.asin] = cr
                        done_count[0] += 1
                        n = done_count[0]
                        if n % cfg['save_every'] == 0 and n // cfg['save_every'] > save_state[0]:
                            save_state[0] = n // cfg['save_every']
                            should_save = True
                    if n % 10 == 0:
                        print(f'  [{n}/{total}] {row.asin} {cr.status.value:<14} {cr.duration_ms}ms', flush=True)
                    if should_save:
                        try:
                            _save_partial()
                        except Exception as exc:
                            logger.error(f'[缓存保存失败] {sheet}: {exc}；继续抓取，最终保存将重试')

            threads = [threading.Thread(target=_worker_loop, daemon=True)
                       for _ in range(workers)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            if circuit_open.is_set():
                # Preserve an explicit result for rows not dequeued after the
                # circuit opened; do not let assemble_crawls call them merely
                # "任务未完成".
                with lock:
                    for pending in rows:
                        if pending.asin not in results_map:
                            results_map[pending.asin] = CrawlResult(
                                asin=pending.asin, run_id=run_id,
                                marketplace=marketplace,
                                product_url=pending.product_url,
                                source_product_url=getattr(
                                    pending, 'source_product_url', ''),
                                status=PageStatus.CRAWL_ERROR,
                                error=('batch_circuit_breaker: 残缺商品页达到阈值，'
                                       '本表剩余行未继续请求'))
        finally:
            browser.quit()
            if archiver is not None:
                archiver.cleanup_downloads()

    crawls = assemble_crawls(rows, results_map, cfg, logger)
    try:
        save_sheet_cache(run_id, sheet, rows, crawls, cfg)
    except Exception as exc:
        logger.error(f'[最终缓存保存失败] {sheet}: {exc}；保留采集结果供weekly bundle持久化')
    if cfg.get('html_archive_enabled', False):
        storage = ArchiveStorage(cfg['html_archive_root'], cfg['html_retention_days'],
                                 cfg['html_min_free_gb'], cfg.get('_html_server_base_url', ''))
        run_date = datetime.now().date()
        manifest_path = storage.run_dir(run_date, run_id) / 'manifest.json'
        manifest = {'schema_version': 1, 'run_id': run_id,
                    'run_date': run_date.isoformat(), 'sheets': {}}
        if manifest_path.is_file():
            try:
                loaded = json.loads(manifest_path.read_text(encoding='utf-8'))
                if loaded.get('run_id') == run_id and isinstance(loaded.get('sheets'), dict):
                    manifest = loaded
            except (OSError, json.JSONDecodeError):
                pass
        manifest['sheets'][sheet] = {
            'sheet_order': sheet_order, 'marketplace': marketplace,
            'records': [{
                'asin': cr.asin, 'page_status': cr.status.value,
                'archive_status': cr.archive_status, 'html_path': cr.html_path,
                'html_url': cr.html_url, 'sha256': cr.html_sha256,
                'size_bytes': cr.html_size_bytes, 'archive_ms': cr.archive_ms,
                'post_archive_delay_seconds': cr.post_archive_delay_seconds,
                'error': cr.archive_error,
                'stripped_noncore_css_resources': cr.stripped_noncore_css_resources,
            } for cr in crawls],
        }
        manifest['updated_at'] = datetime.now().isoformat()
        storage.write_manifest(run_date, run_id, manifest)
    return crawls


# ============================ 汇总 ============================
def summarize(results_by_sheet: dict[str, list[CrawlResult]], cfg: dict, logger) -> float:
    """返回技术异常比例（crawl_error + parse_error）/ 抓取行总数。
    source_data_invalid 单列；identity_mismatch 是严格阻断的源链接/站点
    重定向状态，不伪装为浏览器或网络技术异常。"""
    st = Counter()
    mt = Counter()
    dt = Counter()
    currencies = Counter()
    total = 0
    for crawls in results_by_sheet.values():
        for cr in crawls:
            st[cr.status.value] += 1
            mt[cr.match.split('(')[0] if cr.match else '-'] += 1
            dt[cr.discount_type or '-'] += 1
            currencies[cr.currency_code or 'unknown'] += 1
            if cr.status != PageStatus.SOURCE_INVALID:
                total += 1
    p(logger, f'=== 状态: ' + ' '.join(f'{k}={v}' for k, v in st.most_common()))
    p(logger, f'=== 一致性: ' + ' '.join(f'{k}={v}' for k, v in mt.most_common()))
    p(logger, f'=== 类型: ' + ' '.join(f'{k}={v}' for k, v in dt.most_common()))
    p(logger, f'=== 币种: ' + ' '.join(f'{k}={v}' for k, v in currencies.most_common()))
    if total:
        err = st.get('crawl_error', 0) + st.get('parse_error', 0)
        ratio = err / total
        if ratio > cfg['max_error_ratio_for_push']:
            p(logger, f'[告警] 技术异常占比 {ratio:.1%} 超过阈值 '
                      f'{cfg["max_error_ratio_for_push"]:.0%}，不应将结果直接视为售罄推送')
        return ratio
    return 0.0


# ============================ 批次恢复（3.3） ============================
def resolve_run_id(cfg: dict, rows_by_sheet: dict[str, list[ReportRow]],
                   args, logger) -> str:
    if args.run_id:
        p(logger, f'[恢复] 使用指定批次 {args.run_id}')
        return args.run_id
    if args.resume:
        snap = load_latest_snapshot()
        if snap:
            rid, _ = snap
            for sheet, rows in rows_by_sheet.items():
                meta = load_sheet_cache(rid, sheet)
                if meta and is_cache_valid(meta, cfg, sheet, rows):
                    p(logger, f'[恢复] 找到有效未完成批次 {rid}（{sheet} 缓存有效）')
                    return rid
        p(logger, '[恢复] 没有可恢复的有效批次，创建新批次')
    return make_run_id()


# ============================ 飞书流程 ============================
def full_flow(fc: FeishuClient, cfg: dict, sheets: list[str], args, logger) -> None:
    t_start = time.time()
    # 1. 解析源/目标表
    source_obj, source_type = fc.resolve_wiki_obj(cfg['feishu_source_wiki'])
    target_ss = fc.resolve_wiki(cfg['feishu_target_wiki'])
    p(logger, f'[飞书] 源表 {source_obj} (类型 {source_type}) / 目标表 {target_ss}')

    # 2. 读原始周报（同一份快照）→ 无效行分离
    if source_type == 'file':
        # 原始表是上传的 xlsx 文件 → 下载解析（公式读缓存值，缺则本地兜底）
        rows_by_sheet, meta, invalid = fc.read_source_file(source_obj, sheets, cfg)
    else:
        rows_by_sheet, meta, invalid = fc.read_source_sheets(source_obj, sheets, cfg)
    if not rows_by_sheet:
        raise RuntimeError('原始周报没有读到任何有效数据，请检查源 wiki 与子表名')
    if invalid:
        p(logger, f'[数据完整性] {len(invalid)} 行源数据无效（目标价/正常售价为空），不进入抓取:')
        for iv in invalid:
            p(logger, f"   [{iv['sheet']}] 行{iv['row_num']} {iv['asin']}: {iv['reason']}")
    p(logger, f'[飞书] 读取原始表: ' + ' '.join(f'{s}={len(r)}' for s, r in rows_by_sheet.items()))

    # 2.5 按 --asins 过滤（在线专项测试）
    if args.asins:
        asins = {a.strip() for a in args.asins.split(',') if a.strip()}
        for s in list(rows_by_sheet.keys()):
            rows_by_sheet[s] = [r for r in rows_by_sheet[s] if r.asin in asins]
            if not rows_by_sheet[s]:
                del rows_by_sheet[s]
        p(logger, f'[asins] 只测试 {len(asins)} 个 ASIN: ' + ', '.join(sorted(asins)))

    # 2.6 source_data_invalid：生成六列异常记录（防目标表残留旧成功数据，不静默忽略）
    invalid_crawls: dict[str, list[CrawlResult]] = {}
    for iv in invalid:
        cr = CrawlResult(asin=iv['asin'], status=PageStatus.SOURCE_INVALID,
                         error=f"源数据无效: {iv['reason']}")
        cr.timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        cr.match = '-'
        cr.discount_type = '-'
        invalid_crawls.setdefault(iv['sheet'], []).append(cr)

    # 3. 批次恢复：resume/run-id 才复用旧 run_id，否则新建
    run_id = resolve_run_id(cfg, rows_by_sheet, args, logger)
    p(logger, f'===== 运行批次 {run_id} =====')

    # 4. 保存本地快照
    save_snapshot(run_id, meta, rows_by_sheet)
    p(logger, f'[快照] 已保存 outputs/snapshots/{run_id}/source.json')

    write_to_feishu = not args.dry_run and not args.fetch_only
    target_sheets = {}

    # 5. 基础数据同步到目标表（公式转数值）
    if write_to_feishu:
        target_sheets = fc.list_sheets(target_ss)
        synced = 0
        for sheet, rows in rows_by_sheet.items():
            sid = target_sheets.get(sheet)
            if not sid:
                p(logger, f'  [{sheet}] 目标表无此 sheet，跳过同步')
                continue
            # 每次覆盖前保存目标表 A:V 快照；A:G 使用完整源数据（含无效行）。
            backup = fc.backup_target_sheet(target_ss, sheet, sid, run_id)
            invalid_rows = [iv['report_row'] for iv in invalid
                            if iv.get('sheet') == sheet and iv.get('report_row') is not None]
            base_rows = sorted(rows + invalid_rows, key=lambda r: r.row_num)
            n, appended, removed = fc.sync_base_data(target_ss, sheet, sid, base_rows, cfg)
            synced += n
            p(logger, f'  [{sheet}] A:G 刷新 {appended} 行；H:M 已清空；备份 {backup.name}')
            if removed:
                p(logger, f'  [{sheet}] 提示: 源表已删除 {removed} 个 ASIN（目标表旧行保留未动）')
        p(logger, f'[飞书] 基础数据同步完成，共写入 {synced} 单元格')

    # 6. 抓取 + 计算 + CSV
    results_by_sheet: dict[str, list[CrawlResult]] = {}
    for sheet_order, (sheet, rows) in enumerate(rows_by_sheet.items(), start=1):
        if args.limit:
            rows = rows[:args.limit]
        if not rows:
            continue
        crawls = run_fetch(run_id, sheet, rows, cfg,
                           headless=not args.no_headless,   # 3.2 方向修正
                           force_fetch=args.force_fetch, logger=logger,
                           sheet_order=sheet_order)
        results_by_sheet[sheet] = crawls
        csv_path = export_results(sheet, rows, crawls, run_id)
        p(logger, f'[{sheet}] 完成 {len(crawls)} 行 → {csv_path.name}')

    # 7. 汇总 → 异常比例 + 兜底比例前置判断，达标才写飞书
    error_ratio = summarize(results_by_sheet, cfg, logger)
    should_push = write_to_feishu
    if write_to_feishu and error_ratio > cfg['max_error_ratio_for_push'] and not args.force_push:
        p(logger, f'[推送] 技术异常比例 {error_ratio:.1%} 超过阈值 '
                  f'{cfg["max_error_ratio_for_push"]:.0%}，本次不写入飞书六列（本地 CSV/缓存已保留）。'
                  f'人工确认结果后可用 --force-push 或 --push-only 恢复推送。')
        should_push = False
    # 5.x：目标成交价来源监控 + 缺失比例保护
    # 源表是上传 xlsx 时公式列无缓存值 → 本地兜底是常态路径（已离线验证正确），
    # 只有"本地也算不出来(missing)"才是真正的数据风险，超阈值才阻止推送。
    fb_total = sum(1 for rs in rows_by_sheet.values() for r in rs
                   if getattr(r, 'target_price_source', '') == 'local_fallback')
    miss_total = sum(1 for rs in rows_by_sheet.values() for r in rs
                     if getattr(r, 'target_price_source', '') == 'missing')
    valid_total = sum(len(rs) for rs in rows_by_sheet.values())
    if valid_total and fb_total:
        p(logger, f'[推送] 目标成交价本地兜底 {fb_total}/{valid_total} '
                  f'({fb_total / valid_total:.0%})，缺失 {miss_total} 行'
                  f'（上传 xlsx 公式无缓存值属正常，缺失才是风险）')
    if valid_total and miss_total / valid_total > cfg['max_target_fallback_ratio_for_push'] \
            and write_to_feishu and not args.force_push:
        p(logger, f'[推送] 目标成交价缺失占比 {miss_total / valid_total:.1%} 超过阈值 '
                  f'{cfg["max_target_fallback_ratio_for_push"]:.0%}，停止推送（本地兜底也算不出目标价）。')
        should_push = False

    if should_push:
        if not target_sheets:
            target_sheets = fc.list_sheets(target_ss)
        pushed = 0
        for sheet, crawls in results_by_sheet.items():
            sid = target_sheets.get(sheet)
            if not sid:
                continue
            start_col = 8  # 统一紧凑结构：H:M
            asin_map = fc.build_asin_map(target_ss, sid)
            all_crawls = crawls + invalid_crawls.get(sheet, [])   # 无效行写 '-' 防旧数据残留
            n = fc.write_six_columns(target_ss, sheet, sid, asin_map, all_crawls, cfg,
                                     start_col=start_col)
            pushed += n
            p(logger, f'  [{sheet}] 六列写入 {n} 行（起始列 {col_letter(start_col)}）')
        p(logger, f'[飞书] 六列回写完成，共 {pushed} 行')
    else:
        p(logger, f'[推送] 本次跳过飞书六列写入（dry_run={args.dry_run} fetch_only={args.fetch_only}）')

    elapsed = time.time() - t_start
    # 每日运行留档：日志之外再保存机器可读汇总。
    daily_dir = OUTPUT_DIR / 'daily_runs' / datetime.now().strftime('%Y-%m-%d')
    daily_dir.mkdir(parents=True, exist_ok=True)
    with open(daily_dir / f'{run_id}_summary.json', 'w', encoding='utf-8') as f:
        json.dump({
            'run_id': run_id,
            'finished_at': datetime.now().isoformat(timespec='seconds'),
            'sheets': {s: len(v) for s, v in results_by_sheet.items()},
            'currency_counts': dict(Counter(
                cr.currency_code or 'unknown'
                for values in results_by_sheet.values() for cr in values)),
            'invalid_rows': len(invalid),
            'error_ratio': error_ratio,
            'pushed': bool(should_push),
            'elapsed_seconds': round(elapsed, 1),
        }, f, ensure_ascii=False, indent=1)
    p(logger, f'===== 运行结束（耗时 {elapsed:.0f}s）=====')


def push_only_flow(fc: FeishuClient, cfg: dict, sheets: list[str], logger, args) -> None:
    if args.run_id:
        snapshot_path = OUTPUT_DIR / 'snapshots' / args.run_id / 'source.json'
        if not snapshot_path.exists():
            raise RuntimeError(f'指定快照不存在: {args.run_id}')
        with open(snapshot_path, encoding='utf-8') as f:
            snap = (args.run_id, json.load(f))
    else:
        snap = load_latest_snapshot()
    if not snap:
        raise RuntimeError('没有找到历史快照，请先运行一次完整流程')
    run_id, snapshot = snap
    p(logger, f'[push-only] 使用快照 {run_id}')
    target_ss = fc.resolve_wiki(cfg['feishu_target_wiki'])
    target_sheets = fc.list_sheets(target_ss)

    # push-only 也必须先刷新当前原始数据 A:G，再追加缓存结果 H:M。
    source_obj, source_type = fc.resolve_wiki_obj(cfg['feishu_source_wiki'])
    if source_type == 'file':
        current_rows, _, current_invalid = fc.read_source_file(source_obj, sheets, cfg)
    else:
        current_rows, _, current_invalid = fc.read_source_sheets(source_obj, sheets, cfg)
    for sheet in sheets:
        sid = target_sheets.get(sheet)
        if not sid or sheet not in current_rows:
            continue
        invalid_rows = [iv['report_row'] for iv in current_invalid
                        if iv.get('sheet') == sheet and iv.get('report_row') is not None]
        base_rows = sorted(current_rows[sheet] + invalid_rows, key=lambda r: r.row_num)
        backup = fc.backup_target_sheet(target_ss, sheet, sid, run_id)
        fc.sync_base_data(target_ss, sheet, sid, base_rows, cfg)
        p(logger, f'  [{sheet}] A:G 已刷新 {len(base_rows)} 行；H:M 已清空；备份 {backup.name}')
    pushed = 0
    results_by_sheet: dict[str, list[CrawlResult]] = {}
    for sheet in sheets:
        if sheet not in snapshot.get('sheets', {}):
            continue
        rows = [row_from_dict(d) for d in snapshot['sheets'][sheet]]
        meta = load_sheet_cache(run_id, sheet)
        if not meta:
            p(logger, f'[{sheet}] 无抓取缓存，跳过')
            continue
        crawls = records_to_crawls(meta)
        order = {r.asin: r for r in rows}
        ordered = [next((c for c in crawls if c.asin == a), None) for a in order]
        ordered = [c for c in ordered if c is not None]
        if not ordered:
            continue
        results_by_sheet[sheet] = ordered
        sid = target_sheets.get(sheet)
        if not sid:
            continue
        start_col = 8
        asin_map = fc.build_asin_map(target_ss, sid)
        n = fc.write_six_columns(target_ss, sheet, sid, asin_map, ordered, cfg,
                                 start_col=start_col)
        pushed += n
        p(logger, f'  [{sheet}] 六列写入 {n} 行（起始列 {col_letter(start_col)}）')
    summarize(results_by_sheet, cfg, logger)
    p(logger, f'[push-only] 共写入 {pushed} 行')


def inspect_flow(fc: FeishuClient, cfg: dict, sheets: list[str], logger) -> None:
    p(logger, '===== 飞书布局只读预检（不写任何数据）=====')
    target_ss = fc.resolve_wiki(cfg['feishu_target_wiki'])
    res = fc.inspect_layout(target_ss, sheets, cfg)
    for sheet, info in res.items():
        p(logger, f'--- {sheet} ---')
        p(logger, f'  表头行={info.get("header_row")} | 首个 ASIN: {info.get("first_asin")}')
        p(logger, f'  目标成交价: {info.get("target_price_col")} | 最后业务列: {info.get("last_business_col")}')
        p(logger, f'  旧追加列: {info.get("legacy_cols")} | 已有六列: {info.get("existing_six_cols")}')
        p(logger, f'  建议输出范围: {info.get("suggested_output")} | 覆盖风险: {info.get("overlap_risk")}')
        if info.get('j_o_nonempty'):
            p(logger, f'  J:O 现有非空: {info.get("j_o_nonempty")[:8]}')
        if info.get('error'):
            p(logger, f'  {info["error"]}')
    p(logger, '===== 预检完成：人工确认每个 Sheet 的输出范围后，再执行单 Sheet 迁移 =====')


def inspect_weekly_registry_flow(fc: FeishuClient, cfg: dict, logger) -> None:
    from datetime import timedelta, timezone

    p(logger, '===== 固定周报登记表只读预检（不写任何数据）=====')
    started = time.monotonic()
    info = fc.inspect_weekly_registry(
        cfg['weekly_registry_url'], cfg.get('weekly_registry_sheet_id', ''))
    now = datetime.now(timezone(timedelta(hours=8)))
    selected = select_current_registry_row(
        info['records'], now, cfg['feishu_allowed_hosts'])
    elapsed = time.monotonic() - started
    token = info['spreadsheet_token']
    masked = f'{token[:5]}...{token[-4:]}' if len(token) > 10 else '<masked>'
    p(logger, f'登记表: {info["sheet_title"]} ({info["sheet_id"]})')
    p(logger, f'Spreadsheet Token: {masked} | 工作簿子表数: {info["sheet_count"]}')
    valid_links = sum(bool(str(item.get('source_url') or '').strip())
                      for item in info['records'])
    p(logger, f'扫描数据行: {len(info["records"])} | 有效链接行: {valid_links} | 当前行: {selected.row_number}')
    if selected.sequence is not None:
        p(logger, f'当前序号: {selected.sequence} | 更新时间原值: {selected.raw.get("updated_at")}')
    else:
        p(logger, f'当前周期: {selected.period_id} | 生效时间(UTC): {selected.effective_at.isoformat()}')
    p(logger, f'当前周报资源类型: {selected.source_url.split("/")[3]}')
    p(logger, f'只读预检耗时: {elapsed:.3f}s')
    p(logger, '===== 预检完成：未执行飞书写入 =====')


def create_snapshot_poc_flow(fc: FeishuClient, cfg: dict, logger) -> None:
    """R1.2：从登记表当前周报创建唯一 TEST 副本并验证完整结构。"""
    from datetime import timedelta, timezone

    p(logger, '===== R1.2 TEST 完整快照副本 PoC =====')
    total_started = time.monotonic()
    registry = fc.inspect_weekly_registry(
        cfg['weekly_registry_url'], cfg.get('weekly_registry_sheet_id', ''))
    selected = select_current_registry_row(
        registry['records'], datetime.now(timezone(timedelta(hours=8))),
        cfg['feishu_allowed_hosts'])
    p(logger, f'登记表当前行已解析: row={selected.row_number}, period={selected.period_id}')
    source_token, source_type = fc.resolve_wiki_obj(selected.source_url)
    if source_type != 'sheet':
        raise RuntimeError(f'完整副本 PoC 只接受电子表格，当前底层类型: {source_type}')

    p(logger, '开始读取原周报结构指纹（只读）')
    source_before = fc.spreadsheet_structure(source_token)
    p(logger, f'原周报结构读取完成: {source_before["sheet_count"]} 个子表')
    prefix = 'TEST_周报完整快照_'
    recent = [item for item in fc.list_root_files()
              if str(item.get('name') or '').startswith(prefix)
              and (item.get('type') == 'sheet') and item.get('token')]
    copy_started = time.monotonic()
    if recent:
        copied = recent[0]
        copy_name = copied.get('name') or prefix + 'RECOVERED'
        p(logger, f'找回已有待校验 TEST 副本: {copy_name}；本次不重复创建')
    else:
        copy_name = prefix + datetime.now().strftime('%Y%m%d_%H%M%S')
        copied = fc.copy_file(source_token, 'sheet', copy_name, '')
    copy_elapsed = time.monotonic() - copy_started
    copy_token = copied['token']
    masked_pending = f'{copy_token[:5]}...{copy_token[-4:]}'
    pending_dir = OUTPUT_DIR / 'poc_resources'
    pending_dir.mkdir(parents=True, exist_ok=True)
    pending_path = pending_dir / 'r1_2_snapshot_pending.json'
    pending_path.write_text(json.dumps({
        'name': copy_name, 'token_masked': masked_pending,
        'url': copied.get('url') or '', 'created_at': datetime.now().isoformat(),
        'status': 'pending_validation',
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    p(logger, f'副本 Token 已记录: {masked_pending}；开始只读就绪轮询')

    copied_structure = fc.wait_spreadsheet_structure(copy_token)
    p(logger, '副本结构读取完成，开始复核原周报未变化')
    source_after = fc.spreadsheet_structure(source_token)
    if source_before != source_after:
        raise RuntimeError('复制前后原周报结构指纹发生变化，PoC 停止')
    if source_before['sheets'] != copied_structure['sheets']:
        raise RuntimeError('副本与原周报的子表、行列容量或 A1:P10 关键内容不一致')

    pending_path.write_text(json.dumps({
        'name': copy_name, 'token_masked': masked_pending,
        'url': copied.get('url') or '', 'created_at': datetime.now().isoformat(),
        'status': 'validated', 'source_sha256': source_before['sha256'],
        'copy_sha256': copied_structure['sha256'],
    }, ensure_ascii=False, indent=2), encoding='utf-8')

    masked_source = f'{source_token[:5]}...{source_token[-4:]}'
    masked_copy = f'{copy_token[:5]}...{copy_token[-4:]}'
    p(logger, f'源周报: {masked_source} | 类型: {source_type} | 子表: {source_before["sheet_count"]}')
    p(logger, f'源结构 SHA256: {source_before["sha256"]}')
    p(logger, f'副本: {masked_copy} | 名称: {copied.get("name") or copy_name}')
    p(logger, f'副本 URL: {copied.get("url") or "<API未返回>"}')
    p(logger, f'复制 API 耗时: {copy_elapsed:.3f}s | 总耗时: {time.monotonic() - total_started:.3f}s')
    p(logger, '===== R1.2 PoC 通过：原表未写入，TEST 副本只读校验完成 =====')


RESULT_HEADERS = [
    'ASIN', 'SKU', '尺寸', '正常售价', '本周折扣形式', '本周折扣%', '目标成交价',
    '展示价格', '折扣类型', '折扣值', '最终价格', '价格一致性', '币种',
    *FRONTEND_HEADERS, '时间戳', 'Amazon链接',
]


def create_result_poc_flow(fc: FeishuClient, cfg: dict, logger) -> None:
    """R1.3：创建独立 TEST 结果表，只写测试子表 A2:V2。"""
    p(logger, '===== R1.3 TEST 独立结果 Spreadsheet PoC =====')
    started = time.monotonic()
    prefix = 'TEST_独立结果表_'
    existing = [item for item in fc.list_root_files()
                if str(item.get('name') or '').startswith(prefix)
                and item.get('type') == 'sheet' and item.get('token')]
    if existing:
        file_info = existing[0]
        token = file_info['token']
        title = file_info.get('name') or prefix + 'RECOVERED'
        p(logger, f'找回已有 TEST 独立结果表: {title}；本次不重复创建')
    else:
        title = prefix + datetime.now().strftime('%Y%m%d_%H%M%S')
        created = fc.create_spreadsheet(title, '')
        token = created['spreadsheet_token']
        file_info = {'name': created.get('title') or title,
                     'url': created.get('url') or ''}
        p(logger, '创建 API 已返回独立 Spreadsheet Token')

    masked = f'{token[:5]}...{token[-4:]}'
    record_dir = OUTPUT_DIR / 'poc_resources'
    record_dir.mkdir(parents=True, exist_ok=True)
    record_path = record_dir / 'r1_3_result_spreadsheet.json'
    record_path.write_text(json.dumps({
        'name': title, 'token_masked': masked, 'url': file_info.get('url') or '',
        'status': 'pending_validation', 'created_at': datetime.now().isoformat(),
    }, ensure_ascii=False, indent=2), encoding='utf-8')

    sheets = fc.query_sheets(token)
    matches = [s for s in sheets if s.get('title') == 'TEST_RESULT']
    if len(matches) > 1:
        raise RuntimeError('TEST_RESULT 子表重名，停止写入')
    if matches:
        sheet_id = matches[0]['sheet_id']
    else:
        sheet_id = fc.add_sheet(token, 'TEST_RESULT', len(sheets))
        p(logger, f'已创建测试结果子表: TEST_RESULT ({sheet_id})')

    current = fc.read_values(token, sheet_id, 'A2:V2')
    if current and any(str(cell or '').strip() for cell in current[0]):
        if current[0][:len(RESULT_HEADERS)] != RESULT_HEADERS:
            raise RuntimeError('TEST_RESULT A2:V2 已有非本规格内容，停止覆盖')
    else:
        write_started = time.monotonic()
        fc.write_values(token, sheet_id, 'A2:V2', [RESULT_HEADERS])
        p(logger, f'A2:V2 写入耗时: {time.monotonic() - write_started:.3f}s')
    verified = fc.read_values(token, sheet_id, 'A2:V2')
    if not verified or verified[0][:len(RESULT_HEADERS)] != RESULT_HEADERS:
        raise RuntimeError('结果表 A2:V2 回读与固定 22 列表头不一致')

    # 三个受保护资源必须保持相互独立。
    source_token, _ = fc.resolve_wiki_obj(cfg['feishu_target_wiki'])
    if token == source_token:
        raise RuntimeError('新结果表 Token 与参考目标表相同，停止')
    url = file_info.get('url') or f'https://wit0jhu6kvu.feishu.cn/sheets/{token}'
    record_path.write_text(json.dumps({
        'name': title, 'token_masked': masked, 'url': url, 'sheet_id': sheet_id,
        'status': 'validated', 'header_range': 'A2:V2',
        'header_count': len(RESULT_HEADERS), 'validated_at': datetime.now().isoformat(),
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    p(logger, f'结果 Spreadsheet: {masked} | Sheet: TEST_RESULT ({sheet_id})')
    p(logger, f'结果表 URL: {url}')
    p(logger, f'总耗时: {time.monotonic() - started:.3f}s')
    p(logger, '===== R1.3 PoC 通过：仅写入新建 TEST 结果表 A2:V2 =====')


def initialize_week_flow(fc: FeishuClient, cfg: dict, logger,
                         recreate: bool = False) -> None:
    """R1.4：固定登记表驱动的幂等每周资源初始化。"""
    from datetime import timedelta, timezone

    p(logger, '===== R1.4 每周资源初始化 =====')
    started = time.monotonic()
    registry = fc.inspect_weekly_registry(
        cfg['weekly_registry_url'], cfg.get('weekly_registry_sheet_id', ''))
    selection = select_current_registry_row(
        registry['records'], datetime.now(timezone(timedelta(hours=8))),
        cfg['feishu_allowed_hosts'])
    store = WeeklyAssetStore(OUTPUT_DIR / 'weekly_runs')
    manifest, reused = initialize_weekly_assets(fc, store, selection, {
        'url': cfg['weekly_registry_url'],
        'spreadsheet_token': registry['spreadsheet_token'],
        'sheet_id': registry['sheet_id'],
    }, recreate=recreate,
        manager_open_id=cfg.get('feishu_manager_open_id', ''))
    snap = manifest['snapshot']
    result = manifest['result']
    p(logger, f'周期: {manifest["period_id"]} | generation={manifest["generation"]} '
              f'| reused={reused}')
    p(logger, f'正式快照: {snap["spreadsheet_token"][:5]}...{snap["spreadsheet_token"][-4:]} '
              f'| {snap.get("url") or "<API未返回URL>"}')
    p(logger, f'正式结果表: {result["spreadsheet_token"][:5]}...{result["spreadsheet_token"][-4:]} '
              f'| {result.get("url") or "<API未返回URL>"}')
    p(logger, f'Manifest: outputs/weekly_runs/{manifest["period_id"]}/weekly_manifest.json')
    p(logger, f'总耗时: {time.monotonic() - started:.3f}s')
    p(logger, '===== R1.4 初始化完成 =====')


def _notify_manager(fc: FeishuClient, cfg: dict, logger, text: str) -> str:
    """正式阶段完成后通知固定人工管理员；通知失败即不宣告任务完成。"""
    open_id = cfg.get('feishu_manager_open_id', '')
    if not open_id:
        raise RuntimeError('缺少 feishu_manager_open_id，无法发送任务完成通知')
    message_id = fc.send_text_message(open_id, text)
    p(logger, f'[通知] 已通知周成业 | message_id={message_id}')
    return message_id


def require_daily_week_ready(fc: FeishuClient, cfg: dict) -> dict:
    """在任何普通抓取/写回之前确定当前登记周期并执行 manifest 门禁。"""
    from datetime import timedelta, timezone
    registry = fc.inspect_weekly_registry(
        cfg['weekly_registry_url'], cfg.get('weekly_registry_sheet_id', ''))
    selection = select_current_registry_row(
        registry['records'], datetime.now(timezone(timedelta(hours=8))),
        cfg['feishu_allowed_hosts'])
    store = WeeklyAssetStore(OUTPUT_DIR / 'weekly_runs')
    return require_business_ready(store, selection.period_id)


def discover_weekly_mapping_flow(fc: FeishuClient, cfg: dict, logger) -> None:
    """R1.5：从当前正式快照生成确定性子表映射，不写飞书。"""
    from datetime import timedelta, timezone
    p(logger, '===== R1.5 正式快照子表只读发现 =====')
    started = time.monotonic()
    registry = fc.inspect_weekly_registry(
        cfg['weekly_registry_url'], cfg.get('weekly_registry_sheet_id', ''))
    selection = select_current_registry_row(
        registry['records'], datetime.now(timezone(timedelta(hours=8))),
        cfg['feishu_allowed_hosts'])
    store = WeeklyAssetStore(OUTPUT_DIR / 'weekly_runs')
    manifest = store.load(selection.period_id)
    if not manifest or manifest.get('status') != 'ready':
        raise RuntimeError('当前周期正式资源未初始化，请先运行 --new-week --confirm')
    snapshot_token = manifest['snapshot']['spreadsheet_token']
    report = build_discovery(fc, snapshot_token)
    report_path = save_discovery(
        report, OUTPUT_DIR / 'discovery' / f'{selection.period_id}_sheet_mapping.json')
    validate_discovery(report)
    mappings = [item for item in report['sheets']
                if item['status'] in ('mapped', 'mapped_empty')]
    manifest['mapping_ready'] = True
    manifest['business_ready'] = False  # R1.13 创建结果子表并同步 A:G 后才可置 true。
    manifest['sheet_mappings'] = mappings
    manifest['discovery'] = {
        'report_path': str(report_path.relative_to(OUTPUT_DIR.parent)),
        'discovered_at': report['discovered_at'],
        'sheet_count': report['sheet_count'], 'mapped_count': report['mapped_count'],
        'excluded_count': report['excluded_count'], 'unknown_count': report['unknown_count'],
    }
    store.save(selection.period_id, manifest)
    us = sum(item['marketplace'] == 'US' for item in mappings)
    ca = sum(item['marketplace'] == 'CA' for item in mappings)
    p(logger, f'快照子表: {report["sheet_count"]} | 映射: {len(mappings)} '
              f'(US={us}, CA={ca}) | 排除: {report["excluded_count"]}')
    for item in report['sheets']:
        p(logger, f'  #{item["source_order"]:02d} {item["source_sheet"]}: '
                  f'{item["marketplace"]} | ASIN列非空={item["nonempty_asin_cells"]} '
                  f'| 初步合法={item["preliminary_valid_asins"]}')
    p(logger, f'发现报告: {report_path.relative_to(OUTPUT_DIR.parent)}')
    p(logger, f'总耗时: {time.monotonic() - started:.3f}s')
    p(logger, '===== R1.5 发现完成：未执行飞书写入 =====')


def audit_product_links_flow(fc: FeishuClient, cfg: dict, logger) -> None:
    """R1.6：只读审计 ASIN 单元格并生成标准商品 URL。"""
    from datetime import timedelta, timezone
    p(logger, '===== R1.6 ASIN 与商品链接只读审计 =====')
    started = time.monotonic()
    registry = fc.inspect_weekly_registry(
        cfg['weekly_registry_url'], cfg.get('weekly_registry_sheet_id', ''))
    selection = select_current_registry_row(
        registry['records'], datetime.now(timezone(timedelta(hours=8))),
        cfg['feishu_allowed_hosts'])
    store = WeeklyAssetStore(OUTPUT_DIR / 'weekly_runs')
    manifest = store.load(selection.period_id)
    if not manifest or not manifest.get('mapping_ready'):
        raise RuntimeError('当前周期尚未完成 R1.5 子表映射')
    report = audit_manifest_links(fc, manifest)
    path = save_link_audit(
        report, OUTPUT_DIR / 'discovery' / f'{selection.period_id}_product_links.json')
    manifest['link_audit'] = {
        'report_path': str(path.relative_to(OUTPUT_DIR.parent)),
        'valid_count': report['valid_count'], 'invalid_count': report['invalid_count'],
        'audited_at': datetime.now().isoformat(),
    }
    manifest['link_rules_ready'] = report['invalid_count'] == 0
    manifest['currency_model'] = {
        'US': 'USD', 'CA': 'CAD',
        'cross_currency_comparison': 'blocked',
        'ready': True,
    }
    manifest['business_ready'] = False
    store.save(selection.period_id, manifest)
    p(logger, f'有效标准链接: {report["valid_count"]} | 无效: {report["invalid_count"]} '
              f'| 非商品标签跳过: {report["skipped_non_product_count"]}')
    for sheet in report['sheets']:
        if sheet['valid_count'] or sheet['invalid_count'] or sheet['skipped_non_product_count']:
            p(logger, f'  {sheet["sheet"]} {sheet["marketplace"]}: '
                      f'valid={sheet["valid_count"]}, invalid={sheet["invalid_count"]}, '
                      f'skipped={sheet["skipped_non_product_count"]}')
            reasons = {}
            for item in sheet['invalid']:
                reasons[item['reason']] = reasons.get(item['reason'], 0) + 1
            if reasons:
                p(logger, f'    invalid reasons: {reasons}')
    p(logger, f'审计报告: {path.relative_to(OUTPUT_DIR.parent)}')
    p(logger, f'总耗时: {time.monotonic() - started:.3f}s')
    p(logger, '===== R1.6 审计完成：未启动 Amazon，未写飞书 =====')


def sync_weekly_result_base_flow(fc: FeishuClient, cfg: dict, logger) -> None:
    """R1.13：从本周固定快照初始化独立结果表 A:G。"""
    from datetime import timedelta, timezone
    p(logger, '===== R1.13 独立结果表 A:G 初始化 =====')
    started = time.monotonic()
    registry = fc.inspect_weekly_registry(
        cfg['weekly_registry_url'], cfg.get('weekly_registry_sheet_id', ''))
    selection = select_current_registry_row(
        registry['records'], datetime.now(timezone(timedelta(hours=8))),
        cfg['feishu_allowed_hosts'])
    store = WeeklyAssetStore(OUTPUT_DIR / 'weekly_runs')
    run_id = 'base-' + datetime.now().strftime('%Y%m%d-%H%M%S')
    result = sync_weekly_result_base(fc, store, selection.period_id, cfg, run_id)
    for item in result['sheets']:
        p(logger, f'  {item["sheet"]}: rows={item["rows"]}, '
                  f'invalid_retained={item["invalid_rows_retained"]}, '
                  f'sheet_id={item["sheet_id"]}')
    p(logger, f'周期: {result["period_id"]} | 子表: {len(result["sheets"])} '
              f'| A:G 数据行: {result["row_count"]}')
    p(logger, f'总耗时: {time.monotonic() - started:.3f}s')
    p(logger, '===== R1.13 完成：business_ready=true =====')


def _rename_run_result(fc, store, manifest, run_id):
    from result_notification import result_title
    from weekly_assets import assert_result_write_target
    token = manifest['result']['spreadsheet_token']
    assert_result_write_target(manifest, token)
    fixed = store.fixed_result()
    assert_latest_run(store, manifest, run_id)
    if fixed and fixed['spreadsheet_token'] != token:
        raise RuntimeError('改名目标与固定结果表不一致')
    title = result_title(manifest['period_id'], run_id)
    fc.rename_spreadsheet(token, title)
    manifest['result']['name'] = title
    manifest['result']['last_run_id'] = run_id
    store.save(manifest['period_id'], manifest)
    if fixed:
        atomic_json(store.root / 'fixed_result.json', {**fixed, 'name': title})


def _notify_run_collaborators(fc, cfg, logger, run_id, text, out,
                              manager_only: bool = False):
    from result_notification import send_to_recipients
    discovery_error = ''
    if manager_only:
        recipients = [cfg.get('feishu_manager_open_id', '')]
    else:
        try:
            recipients = fc.application_collaborators()
        except Exception as exc:
            recipients = []
            discovery_error = str(exc)
        recipients.append(cfg.get('feishu_manager_open_id', ''))
    # Changing timestamps alone must not resend the same business result on recovery.
    semantic = '\n'.join(line for line in text.splitlines() if not line.startswith(
        ('开始：', '结束：', '完整耗时：', '本地数据：')))
    message_key = hashlib.sha256(semantic.encode('utf-8')).hexdigest()
    receipt_root = out.parent if out.parent.name == 'daily_runs' else out
    ledger_path = receipt_root / 'notification_receipts' / f'{run_id}.json'
    ledger = json.loads(ledger_path.read_text(encoding='utf-8')) if ledger_path.is_file() else {}
    previous = (ledger.get(message_key) or {}).get('sent', [])
    delivered = {item['open_id'] for item in previous}
    path = out / f'{run_id}_notifications.json'
    def save_receipts(partial):
        report = {**partial, 'sent': previous + partial['sent'], 'run_id': run_id,
                  'message_key': message_key, 'discovery_error': discovery_error,
                  'sent_at': datetime.now().isoformat()}
        ledger[message_key] = report
        atomic_json(ledger_path, ledger)
        atomic_json(path, report)
    report = send_to_recipients(fc, [r for r in recipients if r not in delivered], text,
                                local_data_open_id=cfg.get('feishu_manager_open_id', ''),
                                checkpoint=save_receipts)
    save_receipts(report)
    report['sent'] = previous + report['sent']
    report.update(run_id=run_id, discovery_error=discovery_error,
                  sent_at=datetime.now().isoformat())
    atomic_json(path, report)
    scope_label = '仅管理员' if manager_only else '应用协作者'
    p(logger, f'[通知] {scope_label}成功 {len(report["sent"])} 人，失败 {len(report["failed"])} 人；记录 {path}')
    if discovery_error or report['failed']:
        p(logger, '[通知未完全送达] ' + json.dumps(report['failed'], ensure_ascii=False) + discovery_error)
        # Delivery failures are recorded separately from the completed data write.
        send_to_recipients(fc, [cfg.get('feishu_manager_open_id', '')],
                          f'Amazon通知存在未送达成员，请检查应用可用范围。\nrun_id: {run_id}\n本地数据: {path}',
                          local_data_open_id=cfg.get('feishu_manager_open_id', ''))


def _feedback_report_base(status: str, *, reason: str = '') -> dict:
    report = {
        'feedback_status': status,
        'feedback_rows_seen': 0,
        'feedback_rows_eligible': 0,
        'feedback_rows_detail_complete': 0,
        'feedback_rows_written': 0,
        'feedback_rows_expired_deleted': 0,
        'feedback_rows_invalid_date_dropped': 0,
        'feedback_store_status': {},
        'feedback_store_elapsed_seconds': {},
        'feedback_window_type': '',
        'feedback_sheet_readback': {'status': 'not_configured'},
        'feedback_elapsed_seconds': 0.0,
    }
    if reason:
        report['feedback_error'] = str(reason)[:1000]
    return report


def _run_feedback_stage(fc, cfg: dict, run_id: str, args, logger, out: Path) -> dict:
    """Run independent Feedback collection only in the weekday 07:30 slot."""
    feedback_cfg = cfg.get('feedback') or {}
    slot = getattr(args, 'scheduled_slot', 'manual') or 'manual'
    if slot not in ('monday_0730', 'weekday_0730'):
        return _feedback_report_base('skipped_schedule', reason=f'仅07:30槽位运行，当前={slot}')
    if not feedback_cfg.get('enabled'):
        return _feedback_report_base('not_configured', reason='feedback.enabled=false')

    started = time.monotonic()
    evidence_base = Path(str(feedback_cfg.get('evidence_root') or OUTPUT_DIR / 'feedback'))
    if not evidence_base.is_absolute():
        evidence_base = PROJECT_ROOT / evidence_base
    evidence_root = evidence_base / run_id
    state_path = Path(str(feedback_cfg.get('state_path') or evidence_base / 'feedback_state.json'))
    if not state_path.is_absolute():
        state_path = PROJECT_ROOT / state_path
    evidence_root.mkdir(parents=True, exist_ok=True)
    try:
        from seller_feedback import run_feedback_pipeline
        from seller_feedback_browser import build_feedback_collectors
        collectors = build_feedback_collectors(feedback_cfg)
        should_write = not args.dry_run and not args.fetch_only
        raw = run_feedback_pipeline(
            fc=fc,
            run_id=run_id,
            collectors=collectors,
            evidence_dir=evidence_root,
            state_path=state_path,
            spreadsheet_token=str(feedback_cfg.get('target_spreadsheet_token') or ''),
            sheet_id=str(feedback_cfg.get('target_sheet_id') or ''),
            initial_days=int(feedback_cfg.get('initial_window_days', 7)),
            incremental_days=int(feedback_cfg.get('incremental_window_days', 3)),
            retention_days=int(feedback_cfg.get('retention_days', 10)),
            max_rating=int(feedback_cfg.get('max_rating', 3)),
            store_order=feedback_cfg.get('store_order') or ('store_a', 'store_b'),
            store_display_names={
                str(item.get('key')): str(item.get('display_name')).strip()
                for item in (feedback_cfg.get('stores') or [])
                if isinstance(item, dict) and str(item.get('key') or '').strip()
                and str(item.get('display_name') or '').strip()
            },
            write=should_write,
        )
        sheet = raw.get('sheet') or {}
        result = _feedback_report_base(raw.get('status') or 'blocked')
        result.update({
            'feedback_rows_seen': raw.get('feedback_rows_seen', 0),
            'feedback_rows_eligible': raw.get('feedback_rows_eligible', 0),
            'feedback_rows_detail_complete': raw.get('feedback_rows_detail_complete', 0),
            'feedback_rows_written': raw.get('feedback_rows_written', 0),
            'feedback_rows_expired_deleted': sheet.get('feedback_rows_expired_deleted', 0),
            'feedback_rows_invalid_date_dropped': sheet.get('feedback_rows_invalid_date_dropped', 0),
            'feedback_store_status': {
                key: item.get('status') for key, item in (raw.get('stores') or {}).items()
            },
            'feedback_store_elapsed_seconds': {
                key: item.get('elapsed_seconds', 0.0)
                for key, item in (raw.get('stores') or {}).items()
            },
            'feedback_window_type': (raw.get('window') or {}).get('mode', ''),
            'feedback_sheet_readback': sheet.get('readback') or {'status': sheet.get('status')},
            'feedback_window': raw.get('window') or {},
            'feedback_state_advanced': bool(raw.get('state_advanced')),
            'feedback_elapsed_seconds': raw.get('elapsed_seconds', 0.0),
            'feedback_evidence_root': str(evidence_root),
        })
        p(logger, '[Feedback] status=' + str(result['feedback_status'])
          + ' stores=' + json.dumps(result['feedback_store_status'], ensure_ascii=False)
          + ' rows_seen=' + str(result['feedback_rows_seen'])
          + ' rows_written=' + str(result['feedback_rows_written'])
          + ' elapsed=' + str(result['feedback_elapsed_seconds']) + 's')
        return result
    except Exception as exc:
        elapsed = round(time.monotonic() - started, 3)
        result = _feedback_report_base(
            'auth_error' if any(term in str(exc).lower()
                                for term in (
                                    'auth', 'login', 'cookie', 'signin', 'apikey',
                                    'api key', 'keychain', 'credential', '验证码'))
            else 'blocked',
            reason=f'{type(exc).__name__}: {exc}',
        )
        result.update({
            'feedback_elapsed_seconds': elapsed,
            'feedback_evidence_root': str(evidence_root),
        })
        p(logger, '[Feedback] 已停止：' + json.dumps({
            'status': result['feedback_status'],
            'error': result.get('feedback_error', ''),
            'elapsed_seconds': elapsed,
        }, ensure_ascii=False))
        return result


def weekly_daily_flow(fc: FeishuClient, cfg: dict, sheets: list[str], args, logger) -> None:
    """Price-only daily flow; HTML services are never a prerequisite."""
    from datetime import timedelta, timezone
    started = time.monotonic()
    started_at = datetime.now()
    cfg = price_only_config(cfg)
    store = WeeklyAssetStore(OUTPUT_DIR / 'weekly_runs')
    registry = fc.inspect_weekly_registry(
        cfg['weekly_registry_url'], cfg.get('weekly_registry_sheet_id', ''))
    scheduled_slot = getattr(args, 'scheduled_slot', 'manual') or 'manual'
    previous_period_id = ''
    if scheduled_slot != 'manual':
        fixed = store.fixed_result() or {}
        previous_period_id = str(fixed.get('period_id') or '')
    selection = select_for_scheduled_slot(
        registry['records'], datetime.now(timezone(timedelta(hours=8))),
        cfg['feishu_allowed_hosts'], scheduled_slot, previous_period_id)
    p(logger, f'[来源选择] slot={selection.scheduled_slot} '
      f'mode={selection.selection_mode} period={selection.period_id} '
      f'登记行={selection.row_number}')
    if selection.pending_period_change:
        p(logger, '[来源选择] pending_period_change=' +
          json.dumps(selection.pending_period_change, ensure_ascii=False))
    if not args.dry_run and not args.fetch_only:
        assert_registry_fresh(
            store, selection, datetime.now(timezone(timedelta(hours=8))),
            cfg.get('weekly_registry_max_age_days', 8.0))
    manifest = ensure_price_week(fc, store, selection, registry, cfg,
                                 allow_create=not args.dry_run and not args.fetch_only,
                                 run_id=(run_id := _price_run_id(store, selection.period_id, args)),
                                 resume=bool(getattr(args, 'resume', False) or getattr(args, 'run_id', None)))
    manifest.update(
        source_period_id=selection.period_id,
        scheduled_slot=selection.scheduled_slot,
        selection_mode=selection.selection_mode,
        selection_row_number=selection.row_number,
        pending_period_change=selection.pending_period_change,
        feedback_status='not_configured',
        feedback_rows_seen=0,
        feedback_rows_written=0,
        feedback_store_status={},
        feedback_sheet_readback={'status': 'not_configured'},
    )
    if not args.dry_run and not args.fetch_only:
        store.save(selection.period_id, manifest)
    selected = [item for item in manifest['sheet_mappings']
                if not getattr(args, 'sheets', '') or item['result_sheet'] in set(sheets)]
    cfg['sheet_profiles'] = {item['result_sheet']: item['marketplace'] for item in selected}
    if not selected:
        raise RuntimeError('请求的子表不在本周 manifest 映射中')
    plans = _read_source_plan(fc, manifest['snapshot']['spreadsheet_token'], selected, cfg)
    fingerprints = {p['mapping']['result_sheet']: base_fingerprint(p) for p in plans}
    if manifest.get('source_fingerprints') is not None and manifest['source_fingerprints'] != fingerprints:
        raise RuntimeError('本批快照基础字段已改变，禁止继续复用旧价格')
    manifest['source_fingerprints'] = fingerprints
    rows_by_sheet = {plan['mapping']['result_sheet']: list(plan['valid_rows'])
                     for plan in plans if plan['valid_rows']}
    invalid_by_sheet = {plan['mapping']['result_sheet']: list(plan['invalid'])
                        for plan in plans if plan['invalid']}
    for plan in plans:
        marketplace = plan['mapping']['marketplace']
        from product_links import MARKETPLACES
        profile = MARKETPLACES[marketplace]
        for row in plan['rows']:
            row.marketplace = marketplace
            # 保留周报中经过校验的原始链接（含必要的变体查询参数）；
            # 只有纯 ASIN 或无效/缺失链接时才回退标准 /dp/ASIN。
            row.product_url = row.product_url or profile.product_url(row.asin)
    if args.asins:
        wanted = {item.strip() for item in args.asins.split(',') if item.strip()}
        rows_by_sheet = {sheet: [row for row in rows if row.asin in wanted]
                         for sheet, rows in rows_by_sheet.items()}
        rows_by_sheet = {sheet: rows for sheet, rows in rows_by_sheet.items() if rows}
        invalid_by_sheet = {
            sheet: [item for item in items if item['asin'] in wanted]
            for sheet, items in invalid_by_sheet.items()
        }
    if args.limit:
        # Limit the first N source rows, not N valid rows plus all invalid
        # rows.  Otherwise a field-matching sample can silently expand when
        # source_data_invalid records are present after the valid subset.
        limited_rows = {}
        limited_invalid = {}
        for sheet in set(rows_by_sheet) | set(invalid_by_sheet):
            candidates = [
                (row.row_num, 'valid', row)
                for row in rows_by_sheet.get(sheet, [])
            ] + [
                (item['report_row'].row_num, 'invalid', item)
                for item in invalid_by_sheet.get(sheet, [])
            ]
            chosen = sorted(candidates, key=lambda item: item[0])[:args.limit]
            limited_rows[sheet] = [item[2] for item in chosen if item[1] == 'valid']
            limited_invalid[sheet] = [item[2] for item in chosen if item[1] == 'invalid']
        rows_by_sheet = {sheet: rows for sheet, rows in limited_rows.items() if rows}
        invalid_by_sheet = {sheet: items for sheet, items in limited_invalid.items() if items}
    if not rows_by_sheet and not any(invalid_by_sheet.values()) and (args.asins or args.limit):
        raise RuntimeError('本次筛选没有可处理的 ASIN')
    if not args.dry_run and not args.fetch_only:
        scope = [item['result_sheet'] for item in selected]
        if manifest.get('delivery_scope') and manifest['delivery_scope'] != scope:
            raise RuntimeError('恢复批次子表范围不能改变，请使用原始范围')
        manifest['delivery_scope'] = scope
        store.save(selection.period_id, manifest)
    save_snapshot(run_id, {
        'period_id': selection.period_id,
        'snapshot_spreadsheet_token': manifest['snapshot']['spreadsheet_token'],
        'result_spreadsheet_token': manifest['result']['spreadsheet_token'],
    }, rows_by_sheet)
    p(logger, f'===== weekly-run {run_id} | period={selection.period_id} =====')
    results_by_sheet = {}
    out = OUTPUT_DIR / 'daily_runs' / datetime.now().strftime('%Y-%m-%d')
    bundle_path = out / f'{run_id}_weekly_bundle.json'
    # Intermediate price checkpoints are written before the independent
    # Feedback stage; keep the interim state explicit rather than calling it a
    # successful Feedback result.
    feedback_report = _feedback_report_base('not_configured')
    # Shared across all business sub-tables in this run.  A site-wide
    # incomplete-page response must stop the next sub-table too; otherwise
    # each sheet would independently spend minutes repeating the same failure.
    cfg['_run_incomplete_count'] = [0]
    cfg['_run_circuit_open'] = threading.Event()
    def save_bundle():
        frontend_counts = frontend_status_counts(
            [cr for values in results_by_sheet.values() for cr in values])
        atomic_json(bundle_path, {
            'schema_version': 3, 'run_id': run_id, 'period_id': selection.period_id,
            'source_period_id': selection.period_id,
            'scheduled_slot': selection.scheduled_slot,
            'selection_mode': selection.selection_mode,
            'selection_row_number': selection.row_number,
            'pending_period_change': selection.pending_period_change,
            'parser_rule_version': cfg['parser_rule_version'],
            'frontend_check_rule_version': FRONTEND_CHECK_RULE_VERSION,
            'frontend_checks_written': sum(len(values) for values in results_by_sheet.values()),
            'frontend_check_status_counts': frontend_counts,
            **feedback_report,
            'price_tolerance': str(cfg['price_tolerance']),
            'source_fingerprints': fingerprints,
            'snapshot_spreadsheet_token': manifest['snapshot']['spreadsheet_token'],
            'result_spreadsheet_token': manifest['result']['spreadsheet_token'],
            'created_at': datetime.now().isoformat(),
            'sheets': {s: [cr.as_dict() for cr in values] for s, values in results_by_sheet.items()},
        })
    order_by_sheet = {item['result_sheet']: item['source_order'] for item in selected}
    for sheet, rows in rows_by_sheet.items():
        try:
            crawls = run_fetch(run_id, sheet, rows, cfg,
                               headless=not args.no_headless,
                               force_fetch=args.force_fetch, logger=logger,
                               sheet_order=order_by_sheet[sheet])
        except Exception as exc:
            crawls = [CrawlResult(asin=row.asin, run_id=run_id, status=PageStatus.CRAWL_ERROR,
                                 error=f'子表抓取失败: {exc}', marketplace=row.marketplace,
                                 product_url=row.product_url,
                                 source_product_url=getattr(row, 'source_product_url', ''))
                      for row in rows]
        results_by_sheet[sheet] = crawls
        save_bundle()
        try:
            export_results(sheet, rows, crawls, run_id)
        except Exception as exc:
            p(logger, f'[CSV导出失败，bundle已保存] {sheet}: {exc}')
    for sheet, items in invalid_by_sheet.items():
        for item in items:
            row = item['report_row']
            cr = CrawlResult(asin=row.asin, run_id=run_id,
                             status=PageStatus.SOURCE_INVALID,
                             error=f"源数据无效: {item['reason']}", match='-',
                             discount_type='-', marketplace=row.marketplace,
                             currency_code=('CAD' if row.marketplace == 'CA' else 'USD'),
                             product_url=row.product_url,
                             source_product_url=getattr(row, 'source_product_url', ''),
                             timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
            results_by_sheet.setdefault(sheet, []).append(cr)
    if not args.asins and not args.limit:
        for item in selected:
            results_by_sheet.setdefault(item['result_sheet'], [])
    feedback_report = _run_feedback_stage(fc, cfg, run_id, args, logger, out)
    manifest['feedback_report'] = feedback_report
    manifest.update(feedback_report)
    if not args.dry_run and not args.fetch_only:
        store.save(selection.period_id, manifest)
    save_bundle()
    cfg.pop('_run_incomplete_count', None)
    cfg.pop('_run_circuit_open', None)
    error_ratio = summarize(results_by_sheet, cfg, logger)
    should_write = not args.dry_run and not args.fetch_only
    if error_ratio > cfg['max_error_ratio_for_push'] and not args.force_push:
        # 长期全量任务按行收口：技术异常/归档失败行由写入器逐行阻断，
        # 已验证安全的行仍应交付，避免少量或成组异常让整批永久零写入。
        p(logger, f'[weekly-run] 技术异常比例 {error_ratio:.1%}，批次标记 degraded；'
                  '继续写入逐行门禁通过的结果，异常行进入恢复清单')
    report = {'run_id': run_id, 'written_rows': 0, 'blocked': [], 'failures': []}
    if should_write:
        report = _deliver_weekly_results(fc, store, manifest, run_id, results_by_sheet, cfg, out)
        report.update(feedback_report)
        # _deliver_weekly_results checkpoints price progress itself; append the
        # independent Feedback state to the final delivery evidence as well.
        atomic_json(out / f'{run_id}_delivery.json', report)
        p(logger, f'[weekly-run] H:V 写入 {report["written_rows"]} 行，'
              f'阻断 {len(report["blocked"])} 行')
    else:
        p(logger, '[weekly-run] dry/fetch-only：未写飞书')
    (out / f'{run_id}_weekly_summary.json').write_text(json.dumps({
        **report, 'period_id': selection.period_id,
        'source_period_id': selection.period_id,
        'scheduled_slot': selection.scheduled_slot,
        'selection_mode': selection.selection_mode,
        'frontend_check_rule_version': FRONTEND_CHECK_RULE_VERSION,
        'frontend_check_status_counts': frontend_status_counts(
            [cr for values in results_by_sheet.values() for cr in values]),
        **feedback_report,
        'elapsed_seconds': round(time.monotonic() - started, 3),
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    if not args.dry_run and not args.fetch_only:
        from result_notification import completion_text
        elapsed = time.monotonic() - started
        text = completion_text(
            period_id=selection.period_id, run_id=run_id,
            source_period_id=selection.period_id,
            selection_mode=selection.selection_mode,
            scheduled_slot=selection.scheduled_slot,
            started_at=started_at.isoformat(timespec='seconds'),
            finished_at=datetime.now().isoformat(timespec='seconds'),
            elapsed_seconds=elapsed, sheet_count=len(results_by_sheet),
            written_rows=report['written_rows'], blocked_count=len(report['blocked']),
            error_ratio=error_ratio, result_name=manifest['result']['name'],
            result_url=manifest['result']['url'], local_data=str(bundle_path),
            frontend_status_counts=frontend_status_counts(
                [cr for values in results_by_sheet.values() for cr in values]),
            feedback_status=feedback_report['feedback_status'])
        text = _delivery_notice(text, report)
        _notify_run_collaborators(fc, cfg, logger, run_id, text, out,
                                  manager_only=bool(getattr(args, 'notify_manager_only', False)))


def _price_run_id(store, period_id, args):
    if getattr(args, 'run_id', None):
        return args.run_id
    if getattr(args, 'resume', False):
        old = store.load(period_id) or {}
        if not old.get('snapshot_run_id'):
            raise RuntimeError('没有可恢复的当前批次，请新建正式运行')
        return old['snapshot_run_id']
    candidate = make_run_id()
    current = store.load(period_id) or {}
    if current.get('snapshot_run_id') == candidate or (OUTPUT_DIR / 'snapshots' / candidate).exists():
        candidate += '_' + datetime.now().strftime('%f')
    return candidate


def _delivery_notice(text, report):
    if report.get('failures'):
        text += '\n注意：本次存在写入、校验或表名更新异常；仅回读验证通过的行计入写入数。'
    if report.get('blocked'):
        text += '\n阻断行不代表本次有效价格；写入失败的范围可能保留旧数据，请结合时间戳查看。'
    return text


def _deliver_weekly_results(fc, store, manifest, run_id, results, cfg, out):
    path = out / f'{run_id}_delivery.json'
    last_verified = {}
    def checkpoint(report):
        # Keep verified progress even when the checkpoint or final manifest write fails.
        from copy import deepcopy
        last_verified.clear()
        last_verified.update(deepcopy(report))
        atomic_json(path, report)
    checkpoint({'run_id': run_id, 'status': 'pending', 'written_rows': 0})
    try:
        assert_latest_run(store, manifest, run_id)
        if manifest.get('base_sync_pending') or manifest.get('snapshot_run_id'):
            fixed = store.fixed_result()
            if not fixed or fixed['spreadsheet_token'] != manifest['result']['spreadsheet_token']:
                raise RuntimeError('换周发布目标不是固定结果表')
            if manifest.get('snapshot_run_id') not in (None, '', run_id):
                raise RuntimeError('批次快照身份不匹配')
            report = sync_weekly_result_base(
                fc, store, manifest['period_id'], cfg, run_id, staged_results=results,
                selected_sheets=manifest.get('delivery_scope'), checkpoint=checkpoint)
            manifest.update(store.load(manifest['period_id']))
        else:
            report = write_weekly_result_columns(fc, manifest, run_id, results, cfg, checkpoint=checkpoint)
    except Exception as exc:
        report = dict(last_verified)
        verified = report.get('verified', {})
        blocked = report.setdefault('blocked', [])
        existing_blocked = {(item['sheet'], item['asin']) for item in blocked}
        blocked.extend({'sheet': sheet, 'asin': cr.asin, 'reason': str(exc)}
                       for sheet, crawls in results.items() for cr in crawls
                       if not verified.get(f'{sheet}:{cr.asin}')
                       and (sheet, cr.asin) not in existing_blocked)
        report.setdefault('failures', []).append({'stage': 'delivery', 'error': str(exc)})
    if (report['written_rows'] or report.get('base_rows_written')
            or ('base_rows_written' in report and not report['failures'] and not report['blocked'])):
        try:
            assert_latest_run(store, manifest, run_id)
            fixed = store.fixed_result()
            atomic_json(store.root / 'fixed_result.json', {
                **fixed, 'period_id': manifest['period_id'], 'run_id': run_id})
            _rename_run_result(fc, store, manifest, run_id)
        except Exception as exc:
            report['failures'].append({'stage': 'rename', 'error': str(exc)})
    report['status'] = 'partial' if report['failures'] or report['blocked'] else 'complete'
    checkpoint(report)
    # Publish the new result pointer only after the entire batch verifies.
    all_sheets = {item['result_sheet'] for item in manifest.get('sheet_mappings', [])}
    if report['status'] == 'complete' and set(results) == all_sheets:
        atomic_json(store.root / 'active_result.json', {
            'period_id': manifest['period_id'], 'run_id': run_id, 'result': manifest['result'],
            'updated_at': datetime.now().isoformat()})
    return report


def _load_weekly_push_results(run_id: str, sheets: list[str], manifest: dict,
                              cfg: dict | None = None) -> dict:
    snapshot_path = OUTPUT_DIR / 'snapshots' / run_id / 'source.json'
    if not snapshot_path.is_file():
        raise RuntimeError(f'weekly-run 快照不存在: {run_id}')
    snapshot = json.loads(snapshot_path.read_text(encoding='utf-8'))
    source_meta = snapshot.get('source_meta') or {}
    if source_meta.get('period_id') != manifest.get('period_id'):
        raise RuntimeError('缓存周期与当前 manifest 不一致')
    if source_meta.get('snapshot_spreadsheet_token') != manifest['snapshot']['spreadsheet_token']:
        raise RuntimeError('缓存不是来自当前 manifest 固定快照')
    if source_meta.get('result_spreadsheet_token') != manifest['result']['spreadsheet_token']:
        raise RuntimeError('缓存登记的独立结果表已变化')
    results = {}
    bundle_matches = list((OUTPUT_DIR / 'daily_runs').glob(
        f'*/{run_id}_weekly_bundle.json'))
    if len(bundle_matches) > 1:
        raise RuntimeError(f'发现多个同 run_id weekly bundle: {run_id}')
    if bundle_matches:
        bundle = json.loads(bundle_matches[0].read_text(encoding='utf-8'))
        # schema 2 is retained for previously validated recovery bundles;
        # schema 3 is the current A:V/frontend/Feedback-aware bundle.  Old
        # records restore missing frontend evidence as explicit unknown rather
        # than being reinterpreted as current checks.
        if bundle.get('schema_version') not in (2, 3) or bundle.get('run_id') != run_id:
            raise RuntimeError('weekly bundle schema 或 run_id 不匹配')
        if cfg is not None:
            validate_recovery_metadata(
                bundle, cfg, frontend_rule_version=FRONTEND_CHECK_RULE_VERSION)
            validate_record_ages([r for sheet in sheets for r in (bundle.get('sheets') or {}).get(sheet, [])], cfg)
            if manifest.get('source_fingerprints') is not None and bundle.get('source_fingerprints') != manifest['source_fingerprints']:
                raise RuntimeError('恢复bundle源字段指纹不一致，禁止混用旧价格')
        if bundle.get('period_id') != manifest.get('period_id'):
            raise RuntimeError('weekly bundle 周期不匹配')
        if bundle.get('snapshot_spreadsheet_token') != manifest['snapshot']['spreadsheet_token']:
            raise RuntimeError('weekly bundle 固定快照不匹配')
        if bundle.get('result_spreadsheet_token') != manifest['result']['spreadsheet_token']:
            raise RuntimeError('weekly bundle 独立结果表不匹配')
        from cache import _crawl_from_dict
        for sheet in sheets:
            crawls = [_crawl_from_dict(item) for item in
                      ((bundle.get('sheets') or {}).get(sheet) or [])]
            crawls = [item for item in crawls if item is not None]
            if crawls or sheet in (bundle.get('sheets') or {}):
                results[sheet] = crawls
    else:
        # 兼容 bundle 引入前已经完成并验证的单行 PoC 缓存。
        for sheet in sheets:
            meta = load_sheet_cache(run_id, sheet)
            if not meta:
                continue
            if meta.get('schema_version') != SCHEMA_VERSION or meta.get('snapshot_id') != run_id:
                raise RuntimeError(f'[{sheet}] 缓存 schema 或 snapshot_id 不匹配')
            if cfg is not None:
                validate_recovery_metadata(
                    meta, cfg, frontend_rule_version=FRONTEND_CHECK_RULE_VERSION)
                validate_record_ages((meta.get('records') or {}).values(), cfg)
            crawls = records_to_crawls(meta)
            if crawls:
                results[sheet] = crawls
    for sheet, crawls in results.items():
        for cr in crawls:
            if cr.run_id != run_id:
                raise RuntimeError(f'[{sheet}] {cr.asin} 缓存 run_id 不匹配')
            if cfg is not None and not cfg.get('html_archive_enabled', False):
                continue
            if cr.status in (PageStatus.OK, PageStatus.SOLD_OUT,
                             PageStatus.PAGE_NOT_FOUND):
                path = Path(cr.html_path)
                if cr.archive_status != 'ok' or not path.is_file() or not cr.html_url:
                    cr.archive_status = 'failed'
                    cr.archive_error = 'html_archive_missing_or_unavailable'
                    cr.html_url = ''
                    continue
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                if digest != cr.html_sha256:
                    cr.archive_status = 'failed'
                    cr.archive_error = 'html_sha256_mismatch'
                    cr.html_url = ''
                    continue
                if cfg and cfg.get('html_server_enabled', False):
                    from html_server import archive_http_url, server_status
                    cr.html_url = archive_http_url(path, cfg, server_status(cfg))
    if not results:
        raise RuntimeError('指定批次没有可写入的 weekly-run 缓存')
    return results


def weekly_push_only_flow(fc: FeishuClient, cfg: dict, sheets: list[str], args, logger) -> None:
    """Price/frontend recovery of the same snapshot and run, verified H:V writes."""
    from datetime import timedelta, timezone
    started = time.monotonic()
    started_at = datetime.now()
    cfg = price_only_config(cfg)
    registry = fc.inspect_weekly_registry(
        cfg['weekly_registry_url'], cfg.get('weekly_registry_sheet_id', ''))
    selection = select_current_registry_row(
        registry['records'], datetime.now(timezone(timedelta(hours=8))),
        cfg['feishu_allowed_hosts'])
    store = WeeklyAssetStore(OUTPUT_DIR / 'weekly_runs')
    manifest = store.load(selection.period_id)
    if manifest and (manifest.get('source') or {}).get('url') != selection.source_url:
        raise RuntimeError('同一周期源链接变化，禁止恢复旧批次')
    if manifest and manifest.get('snapshot_run_id') not in (None, '', args.run_id):
        raise RuntimeError('只能恢复当前批次，禁止旧快照覆盖最新A:G')
    if not manifest or not manifest.get('base_sync_pending'):
        manifest = require_business_ready(store, selection.period_id)
    fixed = store.fixed_result()
    if not fixed:
        raise RuntimeError('缺少固定结果表登记，禁止恢复写入')
    if (fixed['spreadsheet_token'] != manifest['result']['spreadsheet_token']
                  or (not manifest.get('base_sync_pending') and fixed.get('period_id') != manifest['period_id'])):
        raise RuntimeError('缓存周期不属于当前固定结果表，禁止覆盖')
    if not getattr(args, 'sheets', ''):
        sheets = manifest.get('delivery_scope') or [item['result_sheet'] for item in manifest['sheet_mappings']]
    results = _load_weekly_push_results(args.run_id, sheets, manifest, cfg)
    out = OUTPUT_DIR / 'daily_runs' / datetime.now().strftime('%Y-%m-%d')
    out.mkdir(parents=True, exist_ok=True)
    report = _deliver_weekly_results(fc, store, manifest, args.run_id, results, cfg, out)
    (out / f'{args.run_id}_weekly_push.json').write_text(json.dumps({
        **report, 'period_id': selection.period_id,
        'result_spreadsheet_token': manifest['result']['spreadsheet_token'],
        'verified_at': datetime.now().isoformat(),
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    p(logger, f'[weekly-push-only] 写入并回读 {report["written_rows"]} 行；'
              f'阻断 {len(report["blocked"])} 行')
    from result_notification import completion_text
    text = completion_text(
        period_id=selection.period_id, run_id=args.run_id,
        started_at=started_at.isoformat(timespec='seconds'),
        finished_at=datetime.now().isoformat(timespec='seconds'),
        elapsed_seconds=time.monotonic() - started, sheet_count=len(results),
        written_rows=report['written_rows'], blocked_count=len(report['blocked']),
        result_name=manifest['result']['name'], result_url=manifest['result']['url'],
        local_data=str(out / (args.run_id + '_weekly_push.json')))
    text = _delivery_notice(text, report)
    _notify_run_collaborators(fc, cfg, logger, args.run_id, text, out)


def _result_for_verify(cr: CrawlResult) -> list:
    from weekly_result import result_values
    return result_values(cr)


def _cells_equal(actual, expected) -> bool:
    if isinstance(actual, list) and len(actual) == 1 and isinstance(actual[0], dict):
        rich = actual[0]
        actual = rich.get('link') or rich.get('text') or ''
    if actual in (None, '') and expected in (None, ''):
        return True
    if isinstance(actual, (int, float)) or isinstance(expected, (int, float)):
        try:
            return dec(actual) == dec(expected)
        except Exception:
            pass
    return str(actual) == str(expected)


def amazon_marketplace_poc_flow(cfg: dict, logger, args) -> None:
    """R1.7：单 Marketplace/单 ASIN 页面与区域上下文 PoC。"""
    from product_links import MARKETPLACES, normalize_product
    p(logger, f'===== R1.7 {args.amazon_poc_marketplace} 单 ASIN PoC =====')
    started = time.monotonic()
    marketplace = args.amazon_poc_marketplace
    asin, product_url = normalize_product(args.amazon_poc_asin, marketplace)
    profile = MARKETPLACES[marketplace]
    location_mode = (cfg.get('ca_location_mode') if marketplace == 'CA'
                     else cfg.get('us_location_mode')) or (
                         'postal' if marketplace == 'CA' else 'proxy')
    postal = (cfg['ca_postal'] if marketplace == 'CA'
              and location_mode == 'postal' else None)
    row = ReportRow(row_num=0, asin=asin, marketplace=marketplace,
                    product_url=product_url)
    browser = AmazonBrowser(
        headless=not args.no_headless, us_zip=cfg['us_zip'],
        proxy=cfg.get('proxy') or None, tabs=1,
        marketplace=marketplace, postal_code=postal,
        location_mode=location_mode,
        browser_auto_port=bool(cfg.get('browser_auto_port', True)),
        browser_no_sandbox=bool(cfg.get('browser_no_sandbox', True)),
        browser_disable_gpu=bool(cfg.get('browser_disable_gpu', True)),
        logger=logger)
    tab = None
    try:
        if not browser.setup(strict_location=True):
            p(logger, f'区域设置失败阶段: {browser.location_error} | '
                      f'page={getattr(browser.page, "url", "")} | '
                      f'title={str(getattr(browser.page, "title", ""))[:120]}')
            raise RuntimeError(
                f'{marketplace} 区域上下文初始化未通过: '
                f'{browser.location_error or "unknown"}；禁止继续商品抓取')
        tab = browser.acquire()
        result, tab = browser.fetch_with_retry(tab, row, cfg)
        html = tab.html or ''
        currency_marker = f'"priceCurrency":"{profile.currency_code}"'
        currency_evidence = (currency_marker in html.replace(' ', '')
                             or profile.currency_code in html)
        report = result.as_dict()
        report.update({
            'postal_code': postal, 'domain': profile.domain,
            'location_verification_method': browser.location_verification_method,
            'currency_evidence_in_html': currency_evidence,
            'html_bytes_in_memory': len(html.encode('utf-8')),
            'poc_elapsed_seconds': round(time.monotonic() - started, 3),
            'archive_attempted': False,
            'post_archive_delay_applied': False,
        })
        out_dir = OUTPUT_DIR / 'poc_resources'
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f'r1_7_{marketplace.lower()}_{asin}.json'
        out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        p(logger, f'URL: {product_url} | postal_verified={result.location_verified} '
                  f'| method={browser.location_verification_method}')
        p(logger, f'status={result.status.value} | currency={result.currency_code} '
                  f'| currency_evidence={currency_evidence} | duration={result.duration_ms}ms')
        p(logger, f'报告: {out_path.relative_to(OUTPUT_DIR.parent)}')
        if result.status not in (PageStatus.OK, PageStatus.SOLD_OUT, PageStatus.PAGE_NOT_FOUND):
            raise RuntimeError(f'Amazon PoC 未通过: {result.error}')
        p(logger, '===== R1.7 单 ASIN PoC 通过 =====')
    finally:
        if tab is not None:
            browser.release(tab)
        browser.quit()


def migrate_flow(fc: FeishuClient, cfg: dict, sheets: list[str], args, logger) -> None:
    p(logger, '===== 一次性列迁移开始 =====')
    target_ss = fc.resolve_wiki(cfg['feishu_target_wiki'])
    res = fc.migrate_columns(target_ss, cfg, sheets, confirm=args.confirm)
    p(logger, f'迁移结果: {json.dumps(res, ensure_ascii=False)}')
    p(logger, '列迁移完成：旧追加列已清理，六列表头已写入。之后每日任务只覆盖六列。')


# ============================ 入口 ============================
def _main_unlocked():
    process_started = time.monotonic()
    args = parse_args()
    cfg = load_config()
    ensure_dirs()
    cleanup_debug_dirs(DEBUG_DIR, keep_days=7)
    logger = setup_logger(cfg)
    sheets = [s.strip() for s in args.sheets.split(',')] if args.sheets else list(cfg['sheets'])

    fc = FeishuClient(cfg)
    try:
        if args.inspect_weekly_registry:
            inspect_weekly_registry_flow(fc, cfg, logger)
        elif args.create_snapshot_poc:
            create_snapshot_poc_flow(fc, cfg, logger)
        elif args.create_result_poc:
            create_result_poc_flow(fc, cfg, logger)
        elif args.new_week or args.recreate_weekly_assets:
            initialize_week_flow(fc, cfg, logger,
                                 recreate=args.recreate_weekly_assets)
        elif args.discover_weekly_mapping:
            discover_weekly_mapping_flow(fc, cfg, logger)
        elif args.audit_product_links:
            audit_product_links_flow(fc, cfg, logger)
        elif args.sync_weekly_result_base:
            sync_weekly_result_base_flow(fc, cfg, logger)
        elif args.weekly_run:
            weekly_daily_flow(fc, cfg, sheets, args, logger)
        elif args.weekly_push_only:
            weekly_push_only_flow(fc, cfg, sheets, args, logger)
        elif args.amazon_poc_marketplace:
            amazon_marketplace_poc_flow(cfg, logger, args)
        elif args.inspect_feishu_layout:
            inspect_flow(fc, cfg, sheets, logger)
        elif args.migrate_feishu_columns:
            migrate_flow(fc, cfg, sheets, args, logger)
        elif args.push_only:
            raise RuntimeError('旧推送入口已停用，请使用 --weekly-push-only --run-id 批次 --confirm')
        else:
            raise RuntimeError('请显式使用 --weekly-run --confirm；最小验证使用 --weekly-run --dry-run --limit 1')
    except Exception as exc:
        # Always persist the full traceback before attempting an error notification.
        # Previously a successful manager notification could leave only one log line,
        # making a failed new-week compatibility path impossible to diagnose.
        logger.exception('[致命异常] %s: %s', type(exc).__name__, exc)
        formal = ((args.weekly_run and not args.dry_run and not args.fetch_only)
                  or args.weekly_push_only)
        if formal:
            try:
                _notify_manager(
                    fc, cfg, logger,
                    f'Amazon 周报前端价格捕捉任务异常\n'
                    f'运行耗时: {time.monotonic() - process_started:.3f} 秒\n'
                    f'错误: {type(exc).__name__}: {str(exc)[:500]}')
            except Exception as notify_exc:
                p(logger, f'[通知失败] {type(notify_exc).__name__}: {notify_exc}')
        raise
    finally:
        fc.close()


def main():
    try:
        with exclusive_run(OUTPUT_DIR / 'weekly_scheduler.lock'):
            _main_unlocked()
    except RunBusy as exc:
        print(str(exc), flush=True)
        raise SystemExit(75)


if __name__ == '__main__':
    main()
