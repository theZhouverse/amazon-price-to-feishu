# -*- coding: utf-8 -*-
"""R1.9：同一已加载 Amazon tab 的 SingleFile 最小 PoC。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from amazon.crawler import AmazonBrowser
from config import OUTPUT_DIR, PROJECT_ROOT, load_config
from html_archive import SingleFileArchiver, write_manifest
from models import PageStatus, ReportRow
from product_links import MARKETPLACES


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--marketplace', required=True, choices=('US', 'CA'))
    ap.add_argument('--asin', required=True)
    ap.add_argument('--no-headless', action='store_true')
    args = ap.parse_args()
    cfg = load_config()
    profile = MARKETPLACES[args.marketplace]
    location_mode = (cfg.get('ca_location_mode') if args.marketplace == 'CA'
                     else cfg.get('us_location_mode')) or (
                         'postal' if args.marketplace == 'CA' else 'proxy')
    postal = (cfg['ca_postal'] if args.marketplace == 'CA'
              and location_mode == 'postal' else None)
    browser = AmazonBrowser(headless=not args.no_headless, marketplace=args.marketplace,
                            postal_code=postal, proxy=cfg.get('proxy') or None, tabs=1,
                            location_mode=location_mode,
                            browser_auto_port=bool(cfg.get('browser_auto_port', True)),
                            browser_no_sandbox=bool(cfg.get('browser_no_sandbox', True)),
                            browser_disable_gpu=bool(cfg.get('browser_disable_gpu', True)))
    tab = None
    try:
        if not browser.setup(strict_location=True):
            raise RuntimeError(f'{args.marketplace} 区域初始化失败: {browser.location_error}')
        tab = browser.acquire()
        work = OUTPUT_DIR / 'poc_resources' / 'r1_9_work' / args.marketplace.lower()
        archiver = SingleFileArchiver(PROJECT_ROOT, work)
        archiver.prepare_tab(tab)
        row = ReportRow(row_num=1, asin=args.asin, marketplace=args.marketplace,
                        product_url=profile.product_url(args.asin))
        crawl, tab = browser.fetch_with_retry(tab, row, cfg)
        if crawl.status not in (PageStatus.OK, PageStatus.SOLD_OUT, PageStatus.PAGE_NOT_FOUND):
            raise RuntimeError(f'页面抓取失败: {crawl.status.value} {crawl.error}')
        out = OUTPUT_DIR / 'poc_resources' / f'r1_9_{args.marketplace.lower()}_{args.asin}.html'
        result = archiver.capture(tab, args.asin, out, timeout=180,
                                  page_status=crawl.status.value)
        manifest = out.with_suffix('.manifest.json')
        write_manifest(result, manifest)
        delay = browser.wait_after_archive(cfg, archive_validated=True)
        print(json.dumps({**result.__dict__, 'status': crawl.status.value,
                          'currency_code': crawl.currency_code,
                          'display_price': (str(crawl.display_price)
                                            if crawl.display_price is not None else None),
                          'promotion_raw': crawl.promotion_raw,
                          'post_archive_delay_seconds': delay}, ensure_ascii=False, indent=2))
    finally:
        if tab is not None:
            browser.release(tab)
        browser.quit()


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'R1.9 PoC 失败: {type(exc).__name__}: {exc}', file=sys.stderr)
        raise
