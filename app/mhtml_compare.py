# -*- coding: utf-8 -*-
"""R1.10：同一已加载 tab 的 SingleFile HTML 与临时 MHTML 对照。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from amazon.crawler import AmazonBrowser
from config import OUTPUT_DIR, PROJECT_ROOT, load_config
from html_archive import SingleFileArchiver, write_manifest
from models import PageStatus, ReportRow
from product_links import MARKETPLACES


@dataclass
class MhtmlResult:
    path: str
    sha256: str
    size_bytes: int
    duration_ms: int
    asin: str
    validation: str = 'ok'


def capture_mhtml(tab, asin: str, destination: Path, timeout: float = 120) -> MhtmlResult:
    """通过 CDP 捕获当前文档；不导航，使用临时文件原子替换。"""
    started = time.monotonic()
    response = tab.run_cdp('Page.captureSnapshot', format='mhtml', _timeout=timeout)
    data = str((response or {}).get('data') or '').encode('utf-8')
    if len(data) < 10_000:
        raise RuntimeError(f'MHTML 体积异常: {len(data)} bytes')
    head = data[:4096].lower()
    if b'mime-version:' not in head or b'multipart/related' not in head:
        raise RuntimeError('MHTML 缺少 multipart/related MIME 结构')
    if asin.encode('ascii') not in data:
        raise RuntimeError('MHTML 中未找到目标 ASIN')
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_suffix(destination.suffix + '.tmp')
    try:
        tmp.write_bytes(data)
        os.replace(tmp, destination)
    finally:
        tmp.unlink(missing_ok=True)
    return MhtmlResult(
        path=str(destination), sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data), duration_ms=int((time.monotonic() - started) * 1000),
        asin=asin,
    )


def comparison(html_result, mhtml_result: MhtmlResult, crawl, navigations: int) -> dict:
    return {
        'asin': crawl.asin, 'page_status': crawl.status.value,
        'marketplace': crawl.marketplace, 'currency_code': crawl.currency_code,
        'same_loaded_tab': True, 'navigation_count': navigations,
        'html': asdict(html_result), 'mhtml': asdict(mhtml_result),
        'mhtml_to_html_size_ratio': round(
            mhtml_result.size_bytes / html_result.size_bytes, 4),
        'default_delivery_format': 'single_self_contained_html',
        'mhtml_retention': 'poc_only_delete_after_review',
        'failure_policy': (
            'html_archive_required=true: any HTML capture, atomic write, identity, hash, '
            'external-resource, or offline validation failure blocks H:P for the row; '
            'MHTML is diagnostic evidence only and never substitutes the N-column URL'
        ),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--marketplace', required=True, choices=('US', 'CA'))
    ap.add_argument('--asin', required=True)
    ap.add_argument('--keep-mhtml', action='store_true')
    ap.add_argument('--no-headless', action='store_true')
    args = ap.parse_args()
    cfg = load_config()
    profile = MARKETPLACES[args.marketplace]
    location_mode = (cfg.get('ca_location_mode') if args.marketplace == 'CA'
                     else cfg.get('us_location_mode')) or (
                         'direct_no_postal' if args.marketplace == 'CA' else 'proxy')
    postal = (cfg['ca_postal'] if args.marketplace == 'CA'
              and location_mode == 'postal' else None)
    browser = AmazonBrowser(headless=not args.no_headless, marketplace=args.marketplace,
                            postal_code=postal, proxy=cfg.get('proxy') or None, tabs=1,
                            location_mode=location_mode,
                            browser_auto_port=bool(cfg.get('browser_auto_port', True)),
                            browser_no_sandbox=bool(cfg.get('browser_no_sandbox', True)),
                            browser_disable_gpu=bool(cfg.get('browser_disable_gpu', True)))
    tab = None
    mhtml_path = None
    try:
        if not browser.setup(strict_location=True):
            raise RuntimeError(f'{args.marketplace} 区域初始化失败: {browser.location_error}')
        tab = browser.acquire()
        work = OUTPUT_DIR / 'poc_resources' / 'r1_10_work' / args.marketplace.lower()
        archiver = SingleFileArchiver(PROJECT_ROOT, work)
        archiver.prepare_tab(tab)
        row = ReportRow(row_num=1, asin=args.asin, marketplace=args.marketplace,
                        product_url=profile.product_url(args.asin))
        crawl, tab = browser.fetch_with_retry(tab, row, cfg)
        if crawl.status not in (PageStatus.OK, PageStatus.SOLD_OUT, PageStatus.PAGE_NOT_FOUND):
            raise RuntimeError(f'页面抓取失败: {crawl.status.value} {crawl.error}')
        prefix = f'r1_10_{args.marketplace.lower()}_{args.asin}'
        html_path = OUTPUT_DIR / 'poc_resources' / f'{prefix}.html'
        html_result = archiver.capture(tab, args.asin, html_path, timeout=180,
                                       page_status=crawl.status.value)
        write_manifest(html_result, html_path.with_suffix('.manifest.json'))
        mhtml_path = OUTPUT_DIR / 'poc_resources' / f'{prefix}.mhtml'
        mhtml_result = capture_mhtml(tab, args.asin, mhtml_path, timeout=120)
        report = comparison(html_result, mhtml_result, crawl, navigations=1)
        report_path = OUTPUT_DIR / 'poc_resources' / f'{prefix}.comparison.json'
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        browser.wait_after_archive(cfg, archive_validated=True)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        if mhtml_path is not None and not args.keep_mhtml:
            mhtml_path.unlink(missing_ok=True)
        if tab is not None:
            browser.release(tab)
        browser.quit()


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'R1.10 PoC 失败: {type(exc).__name__}: {exc}', file=sys.stderr)
        raise
