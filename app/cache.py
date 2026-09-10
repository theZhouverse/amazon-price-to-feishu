# -*- coding: utf-8 -*-
"""cache.py — 运行快照 + 抓取缓存（原子保存、有效性校验、断点续跑）"""
from __future__ import annotations

import hashlib
import io
import json
import os
import time
from datetime import datetime
from pathlib import Path

from config import SNAPSHOT_DIR, CACHE_ROOT
from frontend_checks import FRONTEND_CHECK_RULE_VERSION
from models import CrawlResult, PageStatus, ReportRow
from runtime_state import atomic_json

SCHEMA_VERSION = 7  # invalidates caches captured under older frontend rules

def validate_recovery_metadata(meta, cfg, *, frontend_rule_version: str | None = None):
    """Do not publish stale calculations after a parser or tolerance change."""
    if meta.get('parser_rule_version') != cfg.get('parser_rule_version'):
        raise RuntimeError('恢复数据解析规则版本不一致，请重新抓取')
    if str(meta.get('price_tolerance')) != str(cfg.get('price_tolerance')):
        raise RuntimeError('恢复数据价格容差不一致，请重新抓取')
    # Schema 3 weekly bundles contain frontend evidence.  Once a selector or
    # business rule changes, a prior bundle must not be silently reinterpreted
    # as if it had been produced by the new rule version.  Schema 2 remains a
    # legacy price-only bundle and is restored without inventing frontend data.
    if frontend_rule_version and meta.get('schema_version') == 3:
        if meta.get('frontend_check_rule_version') != frontend_rule_version:
            raise RuntimeError('恢复数据前端检查规则版本不一致，请重新抓取')
    try:
        created = datetime.fromisoformat(meta['created_at'])
        age = (datetime.now(created.tzinfo) - created).total_seconds() / 3600
        if not 0 <= age <= float(cfg['cache_max_age_hours']):
            raise ValueError('expired')
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError('恢复数据已过期或时间无效，请重新抓取') from exc

def validate_record_ages(records, cfg):
    for record in records:
        if record.get('status') not in ('ok', 'sold_out', 'page_not_found'):
            continue
        try:
            captured = datetime.fromisoformat(record['timestamp'])
            hours = (datetime.now(captured.tzinfo) - captured).total_seconds() / 3600
            if not 0 <= hours <= float(cfg['cache_max_age_hours']):
                raise ValueError('expired')
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError('商品采集时间过期或无效，不能通过重存缓存续期') from exc


def make_run_id() -> str:
    return datetime.now().strftime('%Y%m%d_%H%M%S')


def data_signature(rows: list[ReportRow]) -> str:
    """影响同步/计算的源字段签名。"""
    h = hashlib.sha256()
    for r in rows:
        h.update(f'{r.asin}|{r.sku}|{r.size}|{r.normal_price}|{r.h_type}|'
                 f'{r.i_value}|{r.target_price}|{r.marketplace}|{r.product_url};'.encode())
    return h.hexdigest()[:16]


# ---------------- 原始周报快照 ----------------
def save_snapshot(run_id: str, source_meta: dict,
                  rows_by_sheet: dict[str, list[ReportRow]]) -> Path:
    dir_ = SNAPSHOT_DIR / run_id
    dir_.mkdir(parents=True, exist_ok=True)
    data = {
        'source_meta': source_meta,
        'captured_at': datetime.now().isoformat(timespec='seconds'),
        'sheets': {sheet: [r.as_dict() for r in rows] for sheet, rows in rows_by_sheet.items()},
    }
    path = dir_ / 'source.json'
    _atomic_write(path, data)
    return path


def load_latest_snapshot() -> tuple[str, dict] | None:
    """返回最新的 (run_id, 快照 dict)；无快照返回 None"""
    if not SNAPSHOT_DIR.exists():
        return None
    runs = sorted([p for p in SNAPSHOT_DIR.iterdir() if p.is_dir()])
    if not runs:
        return None
    run_id = runs[-1].name
    path = runs[-1] / 'source.json'
    if not path.exists():
        return None
    try:
        with io.open(path, encoding='utf-8') as f:
            return run_id, json.load(f)
    except Exception:
        return None


# ---------------- 抓取缓存 ----------------
def _atomic_write(path: Path, data) -> None:
    atomic_json(path, data)


def cache_dir(run_id: str) -> Path:
    return CACHE_ROOT / run_id


def save_sheet_cache(run_id: str, sheet: str, rows: list[ReportRow],
                     crawls: list[CrawlResult], cfg: dict) -> None:
    path = cache_dir(run_id) / f'{sheet}.json'
    data = {
        'schema_version': SCHEMA_VERSION,
        'frontend_check_rule_version': FRONTEND_CHECK_RULE_VERSION,
        'parser_rule_version': cfg['parser_rule_version'],
        'snapshot_id': run_id,
        'sheet': sheet,
        'asin_signature': data_signature(rows),
        'price_tolerance': str(cfg['price_tolerance']),
        'marketplaces': sorted({r.marketplace for r in rows if r.marketplace}),
        'currency_codes': sorted({c.currency_code for c in crawls if c.currency_code}),
        'html_archive_enabled': bool(cfg.get('html_archive_enabled', False)),
        'html_archive_required': bool(cfg.get('html_archive_required', True)),
        'created_at': datetime.now().isoformat(timespec='seconds'),
        'records': {c.asin: c.as_dict() for c in crawls},
    }
    _atomic_write(path, data)


def load_sheet_cache(run_id: str, sheet: str) -> dict | None:
    path = cache_dir(run_id) / f'{sheet}.json'
    if not path.exists():
        return None
    try:
        with io.open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def is_cache_valid(meta: dict | None, cfg: dict, sheet: str,
                   rows: list[ReportRow]) -> bool:
    """方案 15.3：全部相符才可复用"""
    if not meta:
        return False
    if meta.get('schema_version') != SCHEMA_VERSION:
        return False
    if meta.get('frontend_check_rule_version') != FRONTEND_CHECK_RULE_VERSION:
        return False
    if meta.get('parser_rule_version') != cfg['parser_rule_version']:
        return False
    if meta.get('sheet') != sheet:
        return False
    if meta.get('asin_signature') != data_signature(rows):
        return False
    if str(meta.get('price_tolerance')) != str(cfg['price_tolerance']):
        return False
    if sorted(meta.get('marketplaces') or []) != sorted({r.marketplace for r in rows if r.marketplace}):
        return False
    archive_enabled = bool(cfg.get('html_archive_enabled', False))
    if bool(meta.get('html_archive_enabled')) != archive_enabled:
        return False
    if archive_enabled:
        for record in (meta.get('records') or {}).values():
            if record.get('status') not in ('ok', 'page_not_found', 'sold_out'):
                continue
            path = Path(str(record.get('html_path') or ''))
            if record.get('archive_status') != 'ok' or not path.is_file():
                return False
    try:
        created = datetime.fromisoformat(meta['created_at'])
        age_h = (datetime.now() - created).total_seconds() / 3600
    except Exception:
        return False
    if not 0 <= age_h <= cfg['cache_max_age_hours']:
        return False
    try:
        validate_record_ages((meta.get('records') or {}).values(), cfg)
    except RuntimeError:
        return False
    return True


# ---------------- 断点续跑 ----------------
def _crawl_from_dict(d: dict) -> CrawlResult | None:
    """缓存记录 dict → CrawlResult（通用还原，包括异常记录）"""
    if not d:
        return None
    cr = CrawlResult(asin=d.get('asin') or '')
    cr.run_id = d.get('run_id') or ''
    try:
        cr.status = PageStatus(d.get('status') or 'ok')
    except ValueError:
        cr.status = PageStatus.CRAWL_ERROR
    cr.error = d.get('error') or ''
    cr.display_price = _dec(d.get('display_price'))
    cr.discount_type = d.get('discount_type') or ''
    cr.discount_value = d.get('discount_value') or ''
    cr.final_price = _dec(d.get('final_price'))
    cr.match = d.get('match') or ''
    cr.timestamp = d.get('timestamp') or ''
    cr.target_price = _dec(d.get('target_price'))
    cr.price_diff = _dec(d.get('price_diff'))
    cr.price_rule = d.get('price_rule') or ''
    # 促销证据（3.4：恢复后重新计算不得改变折扣类型）
    cr.promotion_raw = d.get('promotion_raw') or ''
    cr.coupon_pct = _dec(d.get('coupon_pct'))
    cr.coupon_amount = _dec(d.get('coupon_amount'))
    cr.coupon_final = _dec(d.get('coupon_final'))
    cr.code_pct = _dec(d.get('code_pct'))
    cr.save_pct = _dec(d.get('save_pct'))
    cr.expected_type = d.get('expected_type') or ''
    cr.expected_type_mismatch = bool(d.get('expected_type_mismatch'))
    for pc in d.get('price_candidates') or []:
        from models import PriceCandidate
        cr.price_candidates.append(PriceCandidate(
            rule=pc.get('rule') or '',
            raw_text=pc.get('raw_text') or '',
            value=_dec(pc.get('value')),
            visible=bool(pc.get('visible', True)),
        ))
    cr.attempt_count = int(d.get('attempt_count') or 0)
    cr.page_url = d.get('page_url') or ''
    cr.page_title = d.get('page_title') or ''
    cr.duration_ms = int(d.get('duration_ms') or 0)
    cr.marketplace = d.get('marketplace') or ''
    cr.currency_code = (d.get('currency_code') or '').upper()
    cr.product_url = d.get('product_url') or ''
    cr.source_product_url = d.get('source_product_url') or ''
    cr.location_verified = bool(d.get('location_verified'))
    cr.risk_cooldown_seconds = float(d.get('risk_cooldown_seconds') or 0)
    cr.html_path = d.get('html_path') or ''
    cr.html_url = d.get('html_url') or ''
    cr.html_sha256 = d.get('html_sha256') or ''
    cr.html_size_bytes = int(d.get('html_size_bytes') or 0)
    cr.archive_ms = int(d.get('archive_ms') or 0)
    cr.archive_status = d.get('archive_status') or ''
    cr.archive_error = d.get('archive_error') or ''
    cr.post_archive_delay_seconds = float(d.get('post_archive_delay_seconds') or 0)
    cr.stripped_noncore_css_resources = int(
        d.get('stripped_noncore_css_resources') or 0)
    cr.frontend_checks = d.get('frontend_checks') or {}
    cr.frontend_check_rule_version = d.get('frontend_check_rule_version') or ''
    return cr


def restore_from_cache(meta: dict, rows: list[ReportRow]) -> dict[str, CrawlResult]:
    """把可复用的缓存记录还原为 CrawlResult（按 asin 键）。"""
    out: dict[str, CrawlResult] = {}
    records = meta.get('records') or {}
    for r in rows:
        d = records.get(r.asin)
        if not d:
            continue
        st = d.get('status')
        if st not in ('ok', 'page_not_found', 'sold_out'):
            continue
        cr = _crawl_from_dict(d)
        if cr is None:
            continue
        if not cr.expected_type:
            cr.expected_type = r.h_type
        out[r.asin] = cr
    return out


def records_to_crawls(meta: dict) -> list[CrawlResult]:
    """还原全部缓存记录（push-only 使用，保留异常记录）"""
    out: list[CrawlResult] = []
    for d in (meta.get('records') or {}).values():
        cr = _crawl_from_dict(d)
        if cr is not None:
            out.append(cr)
    return out


def _dec(s) -> object:
    from decimal import Decimal, InvalidOperation
    if s in (None, ''):
        return None
    try:
        return Decimal(str(s))
    except InvalidOperation:
        return None


def cleanup_debug_dirs(debug_root: Path, keep_days: int = 7) -> None:
    """只保留最近 N 天的异常证据目录"""
    if not debug_root.exists():
        return
    now = time.time()
    for p in debug_root.iterdir():
        if not p.is_dir():
            continue
        try:
            age = (now - p.stat().st_mtime) / 86400
        except OSError:
            continue
        if age > keep_days:
            import shutil
            shutil.rmtree(p, ignore_errors=True)
