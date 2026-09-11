# -*- coding: utf-8 -*-
"""异常页轻量证据：截图和JSON；不保存页面HTML。"""
from __future__ import annotations

import io
import json
from pathlib import Path

from config import DEBUG_DIR
from models import CrawlResult


def save_evidence(run_id: str, sheet: str, cr: CrawlResult, tab, cfg: dict) -> Path | None:
    """保存截图和诊断JSON；既有page.html保留但不再新增。"""
    if cr.status.value not in ('crawl_error', 'identity_mismatch', 'parse_error'):
        return None
    d = DEBUG_DIR / run_id / sheet / cr.asin
    d.mkdir(parents=True, exist_ok=True)
    try:
        tab.get_screenshot(path=str(d / 'screenshot.png'))
    except Exception:
        pass
    diag = {
        'asin': cr.asin, 'status': cr.status.value, 'error': cr.error,
        'url': cr.page_url, 'title': cr.page_title,
        'candidates': [{'rule': c.rule, 'raw': c.raw_text,
                        'value': str(c.value) if c.value is not None else None}
                       for c in cr.price_candidates],
        'promotion_raw': cr.promotion_raw,
        'attempt_count': cr.attempt_count,
        'diagnostic_class': ('incomplete_product_page'
                             if (cr.error or '').startswith('incomplete_product_page')
                             else ''),
        'saved_at': cr.timestamp,
    }
    with io.open(d / 'diagnostic.json', 'w', encoding='utf-8') as f:
        json.dump(diag, f, ensure_ascii=False, indent=2)
    return d
