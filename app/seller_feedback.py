# -*- coding: utf-8 -*-
"""Seller Central Feedback core pipeline.

The browser/session layer is injected by the caller. This module owns the
business contract that can be tested without Amazon or Feishu:

* keep ratings 1, 2 and 3;
* use an initial seven-day window, then overlapping three-day windows;
* retain only the latest ten calendar days in the visible result sheet;
* merge the two stores into one exact nine-column matrix;
* preserve primary feedback when a secondary order-detail lookup is partial;
* never persist credentials, cookies or authorization headers.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import re
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable
from zoneinfo import ZoneInfo

from runtime_state import atomic_json


SH_TZ = ZoneInfo('Asia/Shanghai')
FEEDBACK_HEADERS = (
    '店铺', '日期', '评级', '订单编号', '评论',
    '订单商品编号', 'ASIN', 'SKU', '获取时间戳',
)
FEEDBACK_STATUSES = ('ok', 'partial', 'blocked', 'auth_error', 'skipped_lock')
DEFAULT_STORE_ORDER = ('store_a', 'store_b')

_SECRET_RE = re.compile(
    r'(?i)(authorization|cookie|password|passwd|secret|api[_-]?key|token)'
    r'\s*[:=]\s*(?:bearer\s+)?[^\s,;]+'
)
_DATE_RE = re.compile(r'^(\d{4})[年/\-.](\d{1,2})[月/\-.](\d{1,2})日?(?:\s+.*)?$')


class FeedbackDataError(RuntimeError):
    """A source or target violates the fixed Feedback contract."""


class FeedbackSafetyStop(FeedbackDataError):
    """A login, CAPTCHA, risk or identity signal stopped browser work."""


def redact_secrets(value):
    """Redact likely credentials recursively without hiding review text."""
    if isinstance(value, dict):
        return {
            str(key): ('[REDACTED]' if re.search(
                r'(?i)authorization|cookie|password|passwd|secret|api[_-]?key|token',
                str(key)) else redact_secrets(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    if isinstance(value, tuple):
        return [redact_secrets(item) for item in value]
    if isinstance(value, str):
        return _SECRET_RE.sub(lambda match: f'{match.group(1)}=[REDACTED]', value)
    return value


def local_now(value: datetime | None = None) -> datetime:
    """Return a timezone-aware Asia/Shanghai timestamp."""
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=SH_TZ)
    return current.astimezone(SH_TZ)


def parse_feedback_date(value) -> date | None:
    """Normalize common Seller Central date representations to a local date."""
    if value in (None, ''):
        return None
    if isinstance(value, datetime):
        return local_now(value).date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    match = _DATE_RE.match(text)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None
    iso_text = text.replace('Z', '+00:00')
    try:
        parsed = datetime.fromisoformat(iso_text)
        return local_now(parsed).date()
    except ValueError:
        pass
    for fmt in ('%Y-%m-%d', '%Y/%m/%d', '%m/%d/%Y', '%d/%m/%Y'):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    return None


def normalize_rating(value):
    """Return an integer rating when the source contains one to five stars."""
    if value in (None, ''):
        return None
    text = str(value).strip().replace(',', '.')
    match = re.search(r'\d+(?:\.\d+)?', text)
    if not match:
        return None
    try:
        number = float(match.group(0))
    except ValueError:
        return None
    if not number.is_integer():
        return None
    number = int(number)
    return number if 1 <= number <= 5 else None


def _first(record: dict, *names, default=''):
    for name in names:
        value = record.get(name)
        if value not in (None, ''):
            return value
    return default


def _hash_content(value: str) -> str:
    return hashlib.sha256(str(value or '').encode('utf-8')).hexdigest()


def feedback_key(store: str, *, feedback_id: str = '', feedback_date: str = '',
                 rating='', order_id: str = '', content: str = '') -> tuple[str, bool]:
    """Build the non-visible idempotency key and indicate fallback use."""
    store = str(store or '').strip()
    feedback_id = str(feedback_id or '').strip()
    if feedback_id:
        return f'{store}|{feedback_id}', False
    fallback = '|'.join((
        store, str(feedback_date or '').strip(), str(rating or '').strip(),
        str(order_id or '').strip(), _hash_content(content),
    ))
    return fallback, True


def feedback_window(now: datetime | None = None, state: dict | None = None,
                    *, initial_days: int = 7, incremental_days: int = 3) -> dict:
    """Return an inclusive local-date window and its explicit mode."""
    if initial_days <= 0 or incremental_days <= 0:
        raise ValueError('Feedback窗口天数必须为正整数')
    end = local_now(now).date()
    completed = bool((state or {}).get('initial_window_complete'))
    days = incremental_days if completed else initial_days
    return {
        'mode': 'incremental_3d' if completed else 'initial_7d',
        'start': (end - timedelta(days=days - 1)).isoformat(),
        'end': end.isoformat(),
        'days': days,
        'timezone': 'Asia/Shanghai',
    }


def load_feedback_state(path: Path) -> dict:
    """Load a non-sensitive state ledger; malformed state fails closed."""
    path = Path(path)
    if not path.exists():
        return {
            'version': 1,
            'initial_window_complete': False,
            'last_successful_run_id': '',
            'last_successful_window': None,
            'rule_version': 'feedback-2026-09-09-v1',
        }
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise FeedbackDataError(f'Feedback状态账本不可读，停止缩短窗口: {path}') from exc
    if not isinstance(value, dict) or value.get('version') != 1:
        raise FeedbackDataError('Feedback状态账本版本未知，停止缩短窗口')
    return value


def save_feedback_state(path: Path, state: dict) -> None:
    """Persist only redacted state metadata atomically."""
    atomic_json(Path(path), redact_secrets(state))


def normalize_feedback_record(raw: dict, store: str, run_id: str = '', *,
                              source_url: str = '', fetched_at: str = '',
                              max_rating: int = 3,
                              detail: dict | None = None,
                              page_number: int | None = None,
                              display_store: str | None = None) -> dict | None:
    """Normalize one source row; return None for missing/invalid/non-eligible data."""
    if not isinstance(raw, dict):
        return None
    rating = normalize_rating(_first(raw, 'rating', 'star', 'stars', 'score'))
    if rating is None or rating > max_rating:
        return None
    feedback_date = parse_feedback_date(
        _first(raw, 'feedback_date', 'feedback_time', 'feedbackTime', 'date', 'created_at'))
    if feedback_date is None:
        return None
    order_id = str(_first(
        raw, 'order_id', 'orderId', 'order_number', 'orderNumber',
        'transaction_id', 'transactionId')).strip()
    content = str(_first(raw, 'content', 'comment', 'review', 'text', 'feedback')).strip()
    feedback_id = str(_first(
        raw, 'feedback_id', 'feedbackId', 'id', 'feedbackCode')).strip()
    details = detail or raw
    order_item = str(_first(
        details, 'order_item_number', 'order_item_no', 'orderItemId',
        'order_item_id', 'orderItemNumber')).strip()
    asin = str(_first(details, 'asin', 'ASIN')).strip()
    sku = str(_first(details, 'sku', 'SKU', 'seller_sku', 'sellerSku')).strip()
    captured = fetched_at or local_now().isoformat(timespec='seconds')
    visible_store = str(display_store or store or '').strip()
    key, degraded = feedback_key(
        visible_store, feedback_id=feedback_id, feedback_date=feedback_date.isoformat(),
        rating=rating, order_id=order_id, content=content)
    return {
        # The visible sheet uses the configured human-readable shop name.  The
        # internal key remains available for ordering/audit and is never
        # exposed by feedback_row_values().
        'store': visible_store,
        'date': feedback_date.isoformat(),
        'rating': rating,
        'order_id': order_id,
        'content': content,
        'order_item_number': order_item,
        'asin': asin,
        'sku': sku,
        'fetched_at': captured,
        '_feedback_key': key,
        '_store_key': str(store or '').strip(),
        '_degraded_key': degraded,
        '_feedback_id': feedback_id,
        '_detail_status': 'ok' if order_item and asin and sku else 'partial',
        '_status': 'ok' if order_item and asin and sku else 'partial',
        '_run_id': str(run_id or ''),
        '_source_url': str(source_url or _first(raw, 'source_url', 'url')).strip(),
        '_page_number': page_number,
    }


def _page_items(page):
    if isinstance(page, dict):
        return list(page.get('items') or page.get('records') or [])
    return list(page or [])


def normalize_feedback_pages(store: str, pages: Iterable, run_id: str, *,
                             window: dict | None = None, source_url: str = '',
                             fetched_at: str = '', max_rating: int = 3,
                             store_display_names: dict[str, str] | None = None) -> tuple[list[dict], dict]:
    """Filter and deduplicate pages from one store while retaining page stats."""
    start = parse_feedback_date((window or {}).get('start')) if window else None
    end = parse_feedback_date((window or {}).get('end')) if window else None
    rows: list[dict] = []
    seen: set[str] = set()
    page_stats = []
    raw_count = eligible_count = out_of_window = invalid_date = 0
    for page_number, page in enumerate(pages, start=1):
        items = _page_items(page)
        page_url = source_url
        if isinstance(page, dict):
            page_url = str(page.get('source_url') or page.get('url') or source_url)
        raw_count += len(items)
        page_rows = 0
        page_out = 0
        page_invalid_date = 0
        for raw in items:
            if not isinstance(raw, dict):
                continue
            raw_date = parse_feedback_date(
                _first(raw, 'feedback_date', 'feedback_time', 'feedbackTime', 'date', 'created_at'))
            row = normalize_feedback_record(
                raw, store, run_id, source_url=page_url, fetched_at=fetched_at,
                max_rating=max_rating, page_number=page_number,
                display_store=(store_display_names or {}).get(store, store))
            if raw_date is None:
                page_invalid_date += 1
                invalid_date += 1
                continue
            if row is None:
                continue
            eligible_count += 1
            if (start and raw_date < start) or (end and raw_date > end):
                page_out += 1
                out_of_window += 1
                continue
            if row['_feedback_key'] in seen:
                continue
            seen.add(row['_feedback_key'])
            rows.append(row)
            page_rows += 1
        page_stats.append({
            'page': page_number,
            'raw_rows': len(items),
            'eligible_rows': page_rows,
            'out_of_window_rows': page_out,
            'invalid_date_rows': page_invalid_date,
        })
    return rows, {
        'pages': page_stats,
        'page_count': len(page_stats),
        'raw_rows': raw_count,
        'eligible_rows': eligible_count,
        'rows_in_window': len(rows),
        'out_of_window_rows': out_of_window,
        'invalid_date_rows': invalid_date,
        'deduped_rows': len(rows),
    }


def _row_key(row: dict) -> str:
    key = str(row.get('_feedback_key') or '').strip()
    if key:
        return key
    key, _ = feedback_key(
        str(row.get('store') or ''), feedback_date=str(row.get('date') or ''),
        rating=row.get('rating'), order_id=str(row.get('order_id') or ''),
        content=str(row.get('content') or ''))
    return key


def _merge_one(old: dict, new: dict) -> dict:
    merged = dict(old)
    for key, value in new.items():
        if key.startswith('_'):
            if value not in (None, ''):
                merged[key] = value
            continue
        # A failed detail lookup must not erase a previously complete detail.
        if value not in (None, '') or key not in {'order_item_number', 'asin', 'sku'}:
            merged[key] = value
    if all(merged.get(key) for key in ('order_item_number', 'asin', 'sku')):
        merged['_detail_status'] = 'ok'
        merged['_status'] = 'ok'
    else:
        merged['_detail_status'] = 'partial'
        merged['_status'] = 'partial'
    merged['_feedback_key'] = _row_key(new) or _row_key(old)
    return merged


def merge_feedback_rows(existing: Iterable[dict], incoming: Iterable[dict]) -> list[dict]:
    """Upsert rows by internal key and keep existing rows until retention pruning."""
    existing_items = [row for row in existing if isinstance(row, dict)]
    incoming_items = [row for row in incoming if isinstance(row, dict)]
    legacy_alias_counts = Counter(
        '|'.join((
            str(row.get('store') or '').strip(), str(row.get('date') or '').strip(),
            str(row.get('rating') or '').strip(), str(row.get('order_id') or '').strip(),
        ))
        for row in existing_items if row.get('_legacy_store_migrated')
    )
    merged: dict[str, dict] = {}
    order: list[str] = []
    # A legacy visible row cannot carry its hidden feedback ID.  During the
    # one-time store-name migration, retain a conservative alias by
    # store/date/rating/order so the first upgraded run can update that row even
    # when the comment text changed.  The alias is enabled only for rows
    # explicitly marked by publish_feedback_sheet; normal rows continue to use
    # the strict ID-or-content-hash key from the business contract.
    legacy_aliases: dict[str, str] = {}
    for row in existing_items + incoming_items:
        key = _row_key(row)
        if not key:
            continue
        alias = '|'.join((
            str(row.get('store') or '').strip(), str(row.get('date') or '').strip(),
            str(row.get('rating') or '').strip(), str(row.get('order_id') or '').strip(),
        ))
        alias_allowed = legacy_alias_counts.get(alias, 0) == 1
        effective_key = legacy_aliases.get(alias, key) if alias_allowed else key
        if effective_key not in merged:
            order.append(effective_key)
            merged[effective_key] = dict(row)
        else:
            merged[effective_key] = _merge_one(merged[effective_key], row)
        if row.get('_legacy_store_migrated') and alias_allowed:
            legacy_aliases[alias] = effective_key
    return [merged[key] for key in order]


def recent_feedback_rows(rows: Iterable[dict], now: datetime | None = None,
                         *, retention_days: int = 10) -> tuple[list[dict], int, int]:
    """Keep the latest inclusive calendar window; return rows, expired, invalid."""
    if retention_days <= 0:
        raise ValueError('Feedback留存天数必须为正整数')
    end = local_now(now).date()
    cutoff = end - timedelta(days=retention_days - 1)
    kept: list[dict] = []
    expired = invalid = 0
    for row in rows:
        parsed = parse_feedback_date(row.get('date')) if isinstance(row, dict) else None
        if parsed is None or parsed > end:
            invalid += 1
        elif parsed < cutoff:
            expired += 1
        else:
            kept.append(row)
    return kept, expired, invalid


def sort_feedback_rows(rows: Iterable[dict], store_order: Iterable[str] = DEFAULT_STORE_ORDER,
                       store_display_names: dict[str, str] | None = None) -> list[dict]:
    display_names = {str(key): str(value or key) for key, value in (store_display_names or {}).items()}
    order = {}
    for index, store in enumerate(store_order):
        key = str(store)
        order[key] = index
        order[display_names.get(key, key)] = index
    return sorted(
        rows,
        key=lambda row: (
            order.get(str(row.get('_store_key') or row.get('store') or ''),
                      order.get(str(row.get('store') or ''), len(order))),
            -(parse_feedback_date(row.get('date')) or date.min).toordinal(),
            _row_key(row),
        ),
    )


def feedback_row_values(row: dict) -> list:
    """Return the exact visible A:I schema; internal evidence never leaks here."""
    return [row.get(key, '') for key in (
        'store', 'date', 'rating', 'order_id', 'content',
        'order_item_number', 'asin', 'sku', 'fetched_at',
    )]


def feedback_matrix(existing_rows: Iterable[dict], incoming_rows: Iterable[dict], *,
                    now: datetime | None = None, retention_days: int = 10,
                    store_order: Iterable[str] = DEFAULT_STORE_ORDER,
                    store_display_names: dict[str, str] | None = None) -> tuple[list[str], list[list], dict]:
    merged = merge_feedback_rows(existing_rows, incoming_rows)
    kept, expired, invalid = recent_feedback_rows(
        merged, now, retention_days=retention_days)
    ordered = sort_feedback_rows(kept, store_order, store_display_names)
    return list(FEEDBACK_HEADERS), [feedback_row_values(row) for row in ordered], {
        'feedback_rows_total': len(ordered),
        'feedback_rows_expired_deleted': expired,
        'feedback_rows_invalid_date_dropped': invalid,
    }


def parse_feedback_matrix(values: Iterable[list]) -> list[dict]:
    """Parse only the exact nine-column target; reject legacy A:L sheets."""
    rows = list(values or [])
    if not rows or not any(str(cell or '').strip() for cell in rows[0]):
        return []
    header = [str(value or '').strip() for value in rows[0]]
    if header[:len(FEEDBACK_HEADERS)] != list(FEEDBACK_HEADERS) or len(header) != len(FEEDBACK_HEADERS):
        raise FeedbackDataError('Feedback差评汇总表头不是固定9列，禁止覆盖')
    output = []
    for raw in rows[1:]:
        padded = list(raw) + [''] * (len(FEEDBACK_HEADERS) - len(raw))
        padded = padded[:len(FEEDBACK_HEADERS)]
        if not any(str(value or '').strip() for value in padded):
            continue
        row = dict(zip((
            'store', 'date', 'rating', 'order_id', 'content',
            'order_item_number', 'asin', 'sku', 'fetched_at',
        ), padded))
        row['rating'] = normalize_rating(row.get('rating')) or row.get('rating', '')
        row['_feedback_key'], row['_degraded_key'] = feedback_key(
            str(row.get('store') or ''), feedback_date=str(row.get('date') or ''),
            rating=row.get('rating'), order_id=str(row.get('order_id') or ''),
            content=str(row.get('content') or ''))
        row['_detail_status'] = 'ok' if all(row.get(key) for key in (
            'order_item_number', 'asin', 'sku')) else 'partial'
        row['_status'] = row['_detail_status']
        output.append(row)
    return output


def _matrix_equal(actual: list[list], expected: list[list]) -> bool:
    def clean(matrix):
        return [[str(cell or '') for cell in row[:len(FEEDBACK_HEADERS)]] for row in matrix]
    return clean(actual) == clean(expected)


def publish_feedback_sheet(fc, spreadsheet_token: str, sheet_id: str,
                           incoming_rows: Iterable[dict], run_id: str,
                           evidence_dir: Path, *, now: datetime | None = None,
                           retention_days: int = 10,
                           store_order: Iterable[str] = DEFAULT_STORE_ORDER,
                           store_display_names: dict[str, str] | None = None,
                           existing_range: str = 'A1:I10000') -> dict:
    """Write/read-back a caller-registered exact nine-column Feedback sheet."""
    if not spreadsheet_token or not sheet_id:
        raise FeedbackDataError('Feedback目标Spreadsheet/Sheet ID未登记，禁止写入')
    started = time.monotonic()
    evidence_dir = Path(evidence_dir)
    incoming_rows = list(incoming_rows)
    values = fc.read_values(spreadsheet_token, sheet_id, existing_range)
    existing_rows = parse_feedback_matrix(values) if values else []
    # Migrate rows written by pre-v12 code in memory before merging.  This
    # keeps the visible value human-readable and rebuilds the same stable key,
    # so the first post-upgrade run updates rows instead of duplicating them.
    if store_display_names:
        configured_display_values = {
            str(value).strip() for value in store_display_names.values() if str(value).strip()
        }
        for row in existing_rows:
            legacy_store = str(row.get('store') or '').strip()
            display_store = store_display_names.get(legacy_store)
            if display_store:
                row['store'] = str(display_store).strip()
                row['_store_key'] = legacy_store
                row['_legacy_store_migrated'] = True
                row['_feedback_key'], row['_degraded_key'] = feedback_key(
                    row['store'], feedback_date=str(row.get('date') or ''),
                    rating=row.get('rating'), order_id=str(row.get('order_id') or ''),
                    content=str(row.get('content') or ''))
            elif legacy_store in configured_display_values:
                # Rows already migrated by an earlier run have no hidden
                # feedback ID in the visible nine-column sheet.  Keep the
                # conservative alias enabled so a changed comment still
                # updates the same order row on the next run.
                row['_legacy_store_migrated'] = True
    headers, data, stats = feedback_matrix(
        existing_rows, incoming_rows, now=now,
        retention_days=retention_days, store_order=store_order,
        store_display_names=store_display_names)
    matrix = [headers] + data
    # Keep the fixed Feedback tab last on every real publish.  Offline fakes
    # need not implement sheet metadata mutation, so the optional method is
    # intentionally feature-detected here.
    move_to_end = getattr(fc, 'move_sheet_to_end', None)
    if callable(move_to_end):
        move_to_end(spreadsheet_token, sheet_id)
    fc.backup_target_sheet(spreadsheet_token, 'Feedback差评汇总', sheet_id, run_id)

    old_data_rows = max(0, len(values) - 1) if values else 0
    clear_end = max(old_data_rows + 1, len(data) + 1)
    fc.write_values(spreadsheet_token, sheet_id, 'A1:I1', [headers])
    for start in range(2, clear_end + 1, 200):
        end = min(start + 199, clear_end)
        fc.write_values(
            spreadsheet_token, sheet_id, f'A{start}:I{end}',
            [[''] * len(FEEDBACK_HEADERS) for _ in range(end - start + 1)],
        )
    for offset in range(0, len(data), 200):
        chunk = data[offset:offset + 200]
        fc.write_values(
            spreadsheet_token, sheet_id,
            f'A{2 + offset}:I{1 + offset + len(chunk)}', chunk,
        )
    last_row = max(1, len(data) + 1)
    actual = fc.read_values(spreadsheet_token, sheet_id, f'A1:I{last_row}')
    if not _matrix_equal(actual, matrix):
        raise FeedbackDataError('Feedback差评汇总写后回读与固定9列表格不一致')
    report = {
        'run_id': run_id,
        'status': 'ok',
        'feedback_rows_seen': len(incoming_rows),
        'feedback_rows_written': len(data),
        'feedback_rows_total': len(data),
        'feedback_rows_expired_deleted': stats['feedback_rows_expired_deleted'],
        'feedback_rows_invalid_date_dropped': stats['feedback_rows_invalid_date_dropped'],
        'sheet_id': sheet_id,
        'write_range': f'A1:I{last_row}',
        'readback': {'status': 'ok', 'rows': len(data), 'columns': len(FEEDBACK_HEADERS)},
        'elapsed_seconds': round(time.monotonic() - started, 3),
    }
    atomic_json(evidence_dir / 'feedback_sheet_write.json', redact_secrets(report))
    return report


def _status_for_exception(exc: Exception) -> str:
    text = str(exc).lower()
    if any(term in text for term in (
            'auth', 'login', 'permission', 'cookie', 'signin', 'apikey',
            'api key', 'keychain', 'credential', '验证码')):
        return 'auth_error'
    return 'blocked'


def collect_feedback(run_id: str, collectors: dict[str, Callable[[], object]],
                     evidence_dir: Path, *, window: dict | None = None,
                     fetched_at: str = '', max_rating: int = 3,
                     store_order: Iterable[str] = DEFAULT_STORE_ORDER,
                     store_display_names: dict[str, str] | None = None) -> dict:
    """Run injected store collectors serially and persist redacted evidence."""
    started = time.monotonic()
    started_at = local_now()
    evidence_dir = Path(evidence_dir)
    stores = {}
    all_rows = []
    ordered_stores = [store for store in store_order if store in collectors]
    ordered_stores.extend(store for store in collectors if store not in ordered_stores)
    for store in ordered_stores:
        store_started = time.monotonic()
        store_report = {
            'store': store, 'status': 'ok', 'source_url': '', 'pages': [],
            'rows': [], 'error': '', 'started_at': local_now().isoformat(timespec='seconds'),
        }
        try:
            collector = collectors[store]
            parameters = inspect.signature(collector).parameters
            accepts_window = ('window' in parameters or any(
                parameter.kind is inspect.Parameter.VAR_KEYWORD
                for parameter in parameters.values()))
            response = collector(window=window) if accepts_window else collector()
            source_url = ''
            pages = response
            if isinstance(response, dict):
                pages = response.get('pages') or []
                source_url = str(response.get('source_url') or '')
                store_report.update({
                    key: response.get(key)
                    for key in (
                        'detail_attempted', 'detail_complete', 'next_clicks',
                        'boundary_reached', 'boundary_page', 'risk_stopped',
                    )
                    if key in response
                })
            rows, stats = normalize_feedback_pages(
                store, pages, run_id, window=window, source_url=source_url,
                fetched_at=fetched_at, max_rating=max_rating,
                store_display_names=store_display_names)
            store_report.update(stats, rows=rows, source_url=source_url)
            all_rows.extend(rows)
        except Exception as exc:
            store_report.update(
                status=_status_for_exception(exc),
                error=redact_secrets(f'{type(exc).__name__}: {exc}'))
            if isinstance(exc, FeedbackSafetyStop):
                # The store collector's finally block closes the current
                # context; the next store is a fresh, independent context as
                # required by the SPEC.  The partial report blocks state
                # advancement even if the other store completes.
                store_report['risk_stopped'] = True
        store_report['finished_at'] = local_now().isoformat(timespec='seconds')
        store_report['elapsed_seconds'] = round(time.monotonic() - store_started, 3)
        stores[store] = store_report

    failed = [item for item in stores.values() if item['status'] != 'ok']
    status = 'ok' if not failed else ('partial' if len(failed) < len(stores) else failed[0]['status'])
    report = {
        'run_id': run_id,
        'status': status,
        'window': window or {},
        'started_at': started_at.isoformat(timespec='seconds'),
        'finished_at': local_now().isoformat(timespec='seconds'),
        'elapsed_seconds': round(time.monotonic() - started, 3),
        'feedback_rows_seen': sum(item.get('raw_rows', 0) for item in stores.values()),
        'feedback_rows_eligible': sum(item.get('rows_in_window', 0) for item in stores.values()),
        'feedback_rows_detail_complete': sum(
            1 for row in all_rows if row.get('_detail_status') == 'ok'),
        'feedback_rows_written': 0,
        'stores': redact_secrets(stores),
        'rows': redact_secrets(all_rows),
        'created_at': fetched_at or local_now().isoformat(timespec='seconds'),
    }
    atomic_json(evidence_dir / 'feedback_collection.json', report)
    return report


def can_advance_feedback_state(report: dict, sheet_report: dict | None = None) -> bool:
    """Only a complete two-store read and target read-back advances the window."""
    stores = report.get('stores') or {}
    if len(stores) < 2 or any(item.get('status') != 'ok' for item in stores.values()):
        return False
    if any(item.get('invalid_date_rows', 0) for item in stores.values()):
        return False
    if sheet_report is None or sheet_report.get('status') != 'ok':
        return False
    return (sheet_report.get('readback') or {}).get('status') == 'ok'


def advance_feedback_state(state: dict, report: dict, sheet_report: dict | None) -> dict:
    """Advance only after complete collection and nine-column read-back."""
    updated = dict(state)
    if can_advance_feedback_state(report, sheet_report):
        updated.update({
            'version': 1,
            'initial_window_complete': True,
            'last_successful_run_id': report.get('run_id', ''),
            'last_successful_window': report.get('window') or {},
            'last_successful_at': report.get('finished_at', ''),
            'rule_version': 'feedback-2026-09-09-v1',
        })
    return updated


def run_feedback_pipeline(*, fc, run_id: str, collectors: dict[str, Callable[[], object]],
                          evidence_dir: Path, state_path: Path,
                          spreadsheet_token: str = '', sheet_id: str = '',
                          now: datetime | None = None,
                          initial_days: int = 7, incremental_days: int = 3,
                          retention_days: int = 10, max_rating: int = 3,
                          store_order: Iterable[str] = DEFAULT_STORE_ORDER,
                          store_display_names: dict[str, str] | None = None,
                          write: bool = False) -> dict:
    """Run collection, optional fixed-sheet publication and state advancement.

    ``write=False`` is the safe default for offline or selector-validation runs.
    The caller must explicitly provide the registered Spreadsheet/Sheet identity
    and ``write=True`` before any Feishu mutation is attempted.
    """
    evidence_dir = Path(evidence_dir)
    state_path = Path(state_path)
    state = load_feedback_state(state_path)
    window = feedback_window(
        now, state, initial_days=initial_days, incremental_days=incremental_days)
    report = collect_feedback(
        run_id, collectors, evidence_dir, window=window,
        fetched_at=local_now(now).isoformat(timespec='seconds'),
        max_rating=max_rating, store_order=store_order,
        store_display_names=store_display_names)
    sheet_report = {
        'status': 'not_configured',
        'readback': {'status': 'not_configured'},
        'feedback_rows_written': 0,
    }
    if write:
        sheet_report = publish_feedback_sheet(
            fc, spreadsheet_token, sheet_id, report.get('rows') or [], run_id,
            evidence_dir, now=now, retention_days=retention_days,
            store_order=store_order, store_display_names=store_display_names)
        report['feedback_rows_written'] = sheet_report.get('feedback_rows_written', 0)
    updated_state = advance_feedback_state(state, report, sheet_report)
    if write and can_advance_feedback_state(report, sheet_report):
        save_feedback_state(state_path, updated_state)
    report['sheet'] = sheet_report
    report['state_advanced'] = updated_state != state
    atomic_json(evidence_dir / 'feedback_summary.json', redact_secrets(report))
    return report
