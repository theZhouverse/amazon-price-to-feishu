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


def find_asin_header(values: list[list]) -> tuple[int, int] | None:
    """返回 (1-based 表头行, 1-based ASIN 列)。"""
    for row_index, row in enumerate(values[:10], start=1):
        for col_index, cell in enumerate(row, start=1):
            # 周报有时在 ASIN 表头后附加说明，例如
            # ``ASIN\n(颜色...)``。这仍是 ASIN 列，不能因为注释文本而
            # 把整个已知 PD/CPD 子表误判成未知 Marketplace；但不接受
            # ``ASIN_CODE`` 等普通字符串，避免误识别辅助列。
            header = cell_text(cell)
            if re.match(r'^ASIN(?:$|[\s(（])', header, re.IGNORECASE):
                return row_index, col_index
    return None


def col_letter(number: int) -> str:
    out = ''
    while number:
        number, rem = divmod(number - 1, 26)
        out = chr(65 + rem) + out
    return out


def classify_sheet(title: str, has_asin: bool,
                   has_content: bool = True,
                   headers: list[str] | None = None) -> tuple[str, str]:
    """Classify a source tab without mistaking generic auxiliary ASIN tabs for price tabs.

    Weekly workbooks commonly contain a generic ``Sheet20`` sales/export tab with an
    ASIN column.  It is not a price-capture business tab unless it also has the
    price/target headers used by PD/CPD tabs.  Unknown named ASIN tabs remain a hard
    failure so a genuinely new business tab cannot be silently skipped.
    """
    normalized_headers = {str(item or '').strip() for item in (headers or [])}
    price_headers = {'正常售价', '目标成交价'}
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
        grid = sheet.get('grid_properties') or {}
        preliminary.append({
            'source_order': order, 'source_sheet': sheet.get('title') or '',
            'source_sheet_id': sid, 'marketplace': marketplace,
            'route_reason': reason, 'header_row': header_row,
            'asin_col': asin_col, 'headers': header_values,
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
