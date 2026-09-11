# -*- coding: utf-8 -*-
"""R1.5：正式快照子表发现、Marketplace 路由与结果表映射。"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

ASIN_RE = re.compile(r'\b(B0[A-Z0-9]{8})\b', re.IGNORECASE)
US_SHEET_RE = re.compile(r'^(?:PD|XD|PDF)', re.IGNORECASE)
CA_SHEET_RE = re.compile(r'^CPD', re.IGNORECASE)

# Source-weekly fields are a contract by business name, not by physical
# column.  The weekly report is allowed to insert helper/strategy columns in
# the middle of a tab; keeping this map in one module prevents discovery and
# row parsing from silently developing different rules.
SOURCE_FIELD_ALIASES = {
    'asin': ('ASIN',),
    'sku': ('SKU',),
    'size': ('尺寸', '商品尺寸'),
    'normal_price': ('正常售价', '正常价格'),
    'h_type': ('本周折扣形式', '本周折扣类型'),
    'i_value': ('本周折扣%', '本周折扣％'),
    'target_price': ('目标成交价', '目标价格'),
}
SOURCE_REQUIRED_FIELDS = tuple(SOURCE_FIELD_ALIASES)


def cell_text(value) -> str:
    if value is None:
        return ''
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return str(value.get('text') or value.get('link') or value.get('value') or '').strip()
    if isinstance(value, list):
        return ' '.join(filter(None, (cell_text(item) for item in value))).strip()
    return str(value).strip()


def normalize_header(value) -> str:
    """Normalize a source header for matching without changing its display value.

    Feishu may return line breaks, non-breaking/full-width spaces, or full-width
    punctuation.  These are presentation differences, not schema changes.  We
    deliberately do not remove arbitrary letters/numbers so fields such as
    ``ASIN_CODE`` cannot be mistaken for ``ASIN``.
    """
    text = cell_text(value).replace('\u00a0', ' ').replace('\u3000', ' ')
    text = re.sub(r'\s+', '', text)
    return (text.replace('％', '%').replace('（', '(').replace('）', ')')
                .casefold())


def _is_asin_header(value) -> bool:
    normalized = normalize_header(value)
    if normalized == 'asin':
        return True
    # ``ASIN(颜色/变体说明)`` is used by some weekly tabs.  Only a parenthesis
    # annotation is accepted; ASIN_CODE and other similarly prefixed fields are
    # intentionally rejected.
    return bool(re.fullmatch(r'asin\([^()]*\)', normalized))


def resolve_source_columns(header_row: list, *, require_all: bool = False) -> dict:
    """Return canonical source field -> 1-based column indexes.

    The left-most occurrence is the primary business column.  Real weekly
    tabs (notably PD03/PD05) legitimately repeat ``ASIN``/``SKU``/``尺寸`` in
    later helper blocks; those repeats are retained as discovery warnings and
    never replace the primary occurrence.  Missing fields are reported
    separately; callers that parse rows should set ``require_all`` so a schema
    that cannot supply the business fields fails closed before any price is
    fetched.
    """
    aliases = {
        key: {normalize_header(name) for name in names}
        for key, names in SOURCE_FIELD_ALIASES.items()
    }
    found: dict[str, list[int]] = {key: [] for key in SOURCE_REQUIRED_FIELDS}
    for index, cell in enumerate(header_row, start=1):
        normalized = normalize_header(cell)
        if not normalized:
            continue
        if _is_asin_header(cell):
            found['asin'].append(index)
        for key, names in aliases.items():
            if key != 'asin' and normalized in names:
                found[key].append(index)
    duplicate = {key: cols for key, cols in found.items() if len(cols) > 1}
    missing = [key for key in SOURCE_REQUIRED_FIELDS if not found[key]]
    if require_all and missing:
        details = []
        details.append('缺少字段=' + '、'.join(missing))
        raise RuntimeError('源表字段结构不兼容：' + '；'.join(details))
    # Always expose the left-most candidate to callers.  ``source_schema``
    # separately records later duplicates so they remain visible to reviewers.
    return {key: cols[0] for key, cols in found.items() if cols}


def source_schema(header_row: list) -> dict:
    """Return an auditable, non-throwing schema summary for discovery."""
    columns = resolve_source_columns(header_row, require_all=False)
    duplicate = {}
    normalized = [normalize_header(value) for value in header_row]
    for key, names in SOURCE_FIELD_ALIASES.items():
        accepted = {normalize_header(name) for name in names}
        if key == 'asin':
            count = sum(_is_asin_header(value) for value in header_row)
        else:
            count = sum(item in accepted for item in normalized)
        if count > 1:
            duplicate[key] = count
    missing = [key for key in SOURCE_REQUIRED_FIELDS if key not in columns]
    return {
        'columns': columns,
        'missing': missing,
        'duplicate': duplicate,
        # Duplicate helper headers are expected in some existing reports and
        # are safe because ``columns`` always points to the left-most primary
        # occurrence.  Keep the duplicate map for audit rather than treating a
        # known-valid tab as structurally incomplete.
        'complete': not missing,
    }


def find_asin_header(values: list[list]) -> tuple[int, int] | None:
    """返回 (1-based 表头行, 1-based ASIN 列)。"""
    for row_index, row in enumerate(values[:10], start=1):
        for col_index, cell in enumerate(row, start=1):
            # 周报有时在 ASIN 表头后附加说明，例如
            # ``ASIN\n(颜色...)``。这仍是 ASIN 列，不能因为注释文本而
            # 把整个已知 PD/CPD 子表误判成未知 Marketplace；但不接受
            # ``ASIN_CODE`` 等普通字符串，避免误识别辅助列。
            if _is_asin_header(cell):
                return row_index, col_index
    return None


def col_letter(number: int) -> str:
    out = ''
    while number:
        number, rem = divmod(number - 1, 26)
        out = chr(65 + rem) + out
    return out


def infer_marketplace_from_cells(values: list) -> tuple[str | None, str]:
    """Infer a route only when explicit Amazon URL evidence is unambiguous.

    New business tabs normally keep a ``PD``/``CPD`` prefix, but a future
    workbook may use a descriptive title.  In that case an explicit URL in the
    ASIN cells is safe evidence; pure ASIN cells do not contain enough
    information to choose US versus CA and therefore remain a blocking unknown.
    """
    from urllib.parse import urlparse
    from product_links import URL_RE

    hosts = set()
    for value in values:
        # Reuse product_links' recursive flattening so Feishu rich links retain
        # their link target instead of only their display text.
        from product_links import _flatten
        for part in _flatten(value):
            for raw_url in URL_RE.findall(part):
                host = (urlparse(raw_url.rstrip(').,;')).hostname or '').lower().rstrip('.')
                if host in {'amazon.com', 'www.amazon.com', 'smile.amazon.com'}:
                    hosts.add('US')
                elif host in {'amazon.ca', 'www.amazon.ca', 'smile.amazon.ca'}:
                    hosts.add('CA')
                elif host:
                    return None, 'unsupported_url_host'
    if hosts == {'US'}:
        return 'US', 'asin_url_host_us'
    if hosts == {'CA'}:
        return 'CA', 'asin_url_host_ca'
    if len(hosts) > 1:
        return None, 'mixed_marketplace_url_hosts'
    return None, 'no_marketplace_url_evidence'


def infer_marketplace_from_title(title: str) -> tuple[str | None, str]:
    """Infer a route from an explicit country token in a descriptive title."""
    text = str(title or '').strip().casefold()
    ca = bool('加拿大' in text or re.search(r'(?<![a-z])(?:ca|canada)(?![a-z])', text))
    us = bool('美国' in text or re.search(r'(?<![a-z])(?:us|usa|unitedstates)(?![a-z])', text))
    if ca and not us:
        return 'CA', 'title_country_ca'
    if us and not ca:
        return 'US', 'title_country_us'
    if ca and us:
        return None, 'mixed_marketplace_title_tokens'
    return None, 'no_marketplace_title_evidence'


def classify_sheet(title: str, has_asin: bool,
                   has_content: bool = True,
                   headers: list[str] | None = None) -> tuple[str, str]:
    """Classify a source tab without mistaking generic auxiliary ASIN tabs for price tabs.

    Weekly workbooks commonly contain a generic ``Sheet20`` sales/export tab with an
    ASIN column.  It is not a price-capture business tab unless it also has the
    price/target headers used by PD/CPD tabs.  Unknown named ASIN tabs remain a hard
    failure so a genuinely new business tab cannot be silently skipped.  A
    descriptive title with an explicit country token is accepted only when the
    complete business schema is present; URL-based routing is applied later
    for similarly complete tabs.
    """
    schema = source_schema(headers or [])
    normalized_headers = {normalize_header(item) for item in (headers or [])}
    price_headers = {normalize_header('正常售价'), normalize_header('目标成交价')}
    generic_auxiliary = bool(re.fullmatch(r'Sheet\d+', title.strip(), re.IGNORECASE))
    if generic_auxiliary and has_asin and not price_headers.issubset(normalized_headers):
        return 'excluded', 'generic_auxiliary_asin_sheet'
    if title.strip().upper().startswith('BI'):
        return 'excluded', 'explicit_auxiliary_bi_source'
    if CA_SHEET_RE.match(title):
        if has_asin:
            return 'CA', 'title_prefix_cpd'
        return (('unknown', 'cpd_missing_asin_header') if has_content
                else ('CA', 'title_prefix_cpd_empty'))
    if US_SHEET_RE.match(title):
        if has_asin:
            return 'US', 'title_prefix_us_business'
        return (('unknown', 'us_missing_asin_header') if has_content
                else ('US', 'title_prefix_us_business_empty'))
    if not has_asin:
        return 'excluded', 'no_asin_header_auxiliary'
    if schema['complete']:
        hinted, hint_reason = infer_marketplace_from_title(title)
        if hinted:
            return hinted, hint_reason
    return 'unknown', 'asin_sheet_with_unknown_title'


def build_discovery(fc, snapshot_token: str) -> dict:
    from sheet_io import read_rows
    metadata = fc.query_sheets(snapshot_token)
    headers_by_sheet = {}
    for sheet in metadata:
        sid = sheet.get('sheet_id')
        if not sid:
            continue
        grid = sheet.get('grid_properties') or {}
        capacity = int(grid.get('column_count') or 26)
        last_col = col_letter(max(capacity, 26))
        # 飞书 batch API 可能把返回 range 的前缀规范化为 Sheet 标题，而非
        # 请求时的 sheet_id，导致标题和 ID 不同的子表无法归属。表头仅需前
        # 10 行，逐 Sheet 读取既确定归属，也避免把整张表载入内存。
        headers_by_sheet[sid] = fc.read_values(
            snapshot_token, sid, f'A1:{last_col}10')
    preliminary = []
    for order, sheet in enumerate(metadata, start=1):
        sid = sheet.get('sheet_id') or ''
        values = headers_by_sheet.get(sid, [])
        located = find_asin_header(values)
        has_content = any(cell_text(cell) for row in values for cell in row)
        marketplace, reason = classify_sheet(
            sheet.get('title') or '', bool(located), has_content,
            ([cell_text(v) for v in values[located[0] - 1]] if located else []))
        header_row = located[0] if located else None
        asin_col = located[1] if located else None
        header_values = []
        if located and len(values) >= header_row:
            header_values = [cell_text(v) for v in values[header_row - 1]]
        schema = source_schema(header_values)
        grid = sheet.get('grid_properties') or {}
        preliminary.append({
            'source_order': order, 'source_sheet': sheet.get('title') or '',
            'source_sheet_id': sid, 'marketplace': marketplace,
            'route_reason': reason, 'header_row': header_row,
            'asin_col': asin_col, 'headers': header_values,
            'source_schema': schema,
            'row_capacity': grid.get('row_count'),
            'column_capacity': grid.get('column_count'),
            'sheet_has_content': has_content,
            'header_probe': ([
                {'row': row_no,
                 'cells': [cell_text(cell)[:120] for cell in row if cell_text(cell)][:12]}
                for row_no, row in enumerate(values, start=1)
                if any(cell_text(cell) for cell in row)
            ][:12] if not located else []),
        })

    # 飞书 batch API 对多个同列 range 的返回 range 标识不稳定；逐 Sheet 读取保证归属准确。
    asin_by_sheet = {}
    for item in preliminary:
        if not item['header_row'] or not item['asin_col']:
            continue
        letter = col_letter(int(item['asin_col']))
        asin_by_sheet[item['source_sheet_id']] = read_rows(fc,
            snapshot_token, item['source_sheet_id'], first=letter, last=letter,
            start=int(item['header_row']) + 1, row_count=item.get('row_capacity'))
    for item in preliminary:
        cells = asin_by_sheet.get(item['source_sheet_id'], [])
        texts = [cell_text(row[0] if isinstance(row, list) and row else row)
                 for row in cells]
        item['nonempty_asin_cells'] = sum(bool(text) for text in texts)
        item['preliminary_valid_asins'] = sum(bool(ASIN_RE.search(text)) for text in texts)
        # A descriptive new tab title has no inherent US/CA route.  Promote it
        # only when the ASIN cells themselves contain one unambiguous Amazon
        # host; a pure-ASIN or mixed-host tab remains unknown and blocks rather
        # than guessing a marketplace.
        if item['marketplace'] == 'unknown' and item.get('source_schema', {}).get('complete'):
            inferred, infer_reason = infer_marketplace_from_cells(
                [row[0] if isinstance(row, list) and row else row for row in cells])
            if inferred:
                item['marketplace'] = inferred
                item['route_reason'] = infer_reason
            else:
                item['route_reason'] = f'{item.get("route_reason")};{infer_reason}'
        item['result_sheet'] = (item['source_sheet']
                                if item['marketplace'] in ('US', 'CA') else None)
        if item['result_sheet']:
            item['status'] = ('mapped' if item['header_row'] else 'mapped_empty')
        else:
            item['status'] = item['marketplace']

    mapped_names = [item['result_sheet'] for item in preliminary if item['result_sheet']]
    duplicates = sorted({name for name in mapped_names if mapped_names.count(name) > 1})
    unknown = [item['source_sheet'] for item in preliminary if item['marketplace'] == 'unknown']
    return {
        'discovered_at': datetime.now().isoformat(),
        'snapshot_token_masked': (f'{snapshot_token[:5]}...{snapshot_token[-4:]}'
                                  if len(snapshot_token) > 10 else '<masked>'),
        'sheet_count': len(preliminary),
        'mapped_count': len(mapped_names),
        'excluded_count': sum(item['marketplace'] == 'excluded' for item in preliminary),
        'unknown_count': len(unknown), 'unknown_sheets': unknown,
        'duplicate_result_sheets': duplicates, 'sheets': preliminary,
    }


def save_discovery(report: dict, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp = output_path.with_suffix('.json.tmp')
    temp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(output_path)
    return output_path


def validate_discovery(report: dict) -> None:
    if report['duplicate_result_sheets']:
        raise RuntimeError('结果 Sheet 映射重名: ' + ', '.join(report['duplicate_result_sheets']))
    if report['unknown_sheets']:
        raise RuntimeError('存在未知 Marketplace 的含 ASIN 子表: ' + ', '.join(report['unknown_sheets']))
    if report['mapped_count'] == 0:
        raise RuntimeError('快照没有发现任何可映射业务子表')
