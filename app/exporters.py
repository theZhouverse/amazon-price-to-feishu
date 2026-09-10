# -*- coding: utf-8 -*-
"""exporters.py — 本地 CSV 导出（完整诊断字段，不影响飞书前端列）"""
from __future__ import annotations

import io
import csv
from pathlib import Path

from config import CSV_DIR
from models import CrawlResult, ReportRow

CSV_FIELDS = [
    'sheet', 'row_num', 'asin', 'sku', 'marketplace', 'currency_code',
    'product_url', 'source_product_url', 'page_status',
    'display_price', 'discount_type', 'discount_value', 'discount_unit',
    'final_price', 'target_price', 'price_diff', 'match',
    'price_rule', 'promotion_raw', 'attempt_count', 'timestamp', 'error',
    'frontend_product_image', 'frontend_brand_story_image',
    'frontend_size_consistent',
    'frontend_bsr_badge', 'frontend_parent_child_asin',
    'frontend_eco_badge', 'frontend_amazon_choice_badge',
]


def export_results(sheet: str, rows: list[ReportRow], crawls: list[CrawlResult],
                   run_id: str) -> Path:
    CSV_DIR.mkdir(parents=True, exist_ok=True)
    path = CSV_DIR / f'{sheet}_{run_id}_校验结果.csv'
    row_map = {r.asin: r for r in rows}
    with io.open(path, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(CSV_FIELDS)
        for cr in crawls:
            r = row_map.get(cr.asin)
            def _num(d):
                return round(float(d), 2) if d is not None else ''
            w.writerow([
                sheet, r.row_num if r else '', cr.asin, r.sku if r else '',
                cr.marketplace, cr.currency_code, cr.product_url,
                cr.source_product_url,
                cr.status.value,
                _num(cr.display_price), cr.discount_type, cr.discount_value,
                '%' if cr.discount_value.endswith('%') else (cr.currency_code or ''),
                _num(cr.final_price), _num(cr.target_price), _num(cr.price_diff), cr.match,
                cr.price_rule, cr.promotion_raw, cr.attempt_count, cr.timestamp, cr.error,
                *cr.frontend_columns(),
            ])
    return path
