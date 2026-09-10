# -*- coding: utf-8 -*-
"""本周独立结果表初始化：只读正式快照，权威同步 A:G。"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import hashlib
import json

from feishu import COMPACT_BASE_HEADERS, read_source_rows, col_letter
from models import PageStatus
from weekly_assets import WeeklyAssetStore, assert_result_write_target
from publication_guard import assert_latest_run
from sheet_io import read_rows


RESULT_HEADERS = COMPACT_BASE_HEADERS + [
    '展示价格', '折扣类型', '折扣值', '最终价格', '一致性检查', '时间戳',
    '币种', 'Amazon链接',
]
LEGACY_RESULT_HEADERS = RESULT_HEADERS[:13] + ['HTML链接', '币种', 'Amazon链接']


def _display_discount(value):
    if isinstance(value, Decimal) and 0 < value < 1:
        text = format(value * 100, 'f').rstrip('0').rstrip('.')
        return f'{text}%'
    return value if value is not None else ''


def _base_values(row) -> list:
    return [
        row.asin, row.sku, row.size,
        row.normal_price if row.normal_price is not None else '',
        row.h_type, _display_discount(row.i_value),
        row.target_price if row.target_price is not None else '',
    ]

def base_fingerprint(plan):
    return hashlib.sha256(json.dumps([_base_values(row) for row in plan['rows']],
        ensure_ascii=False, default=str, separators=(',', ':')).encode('utf-8')).hexdigest()


def _read_source_plan(fc, snapshot_token: str, mappings: list[dict], cfg: dict) -> list[dict]:
    """在产生任何写入前读取并验证全部源子表。"""
    metadata = fc.query_sheets(snapshot_token)
    source_sheets = {item.get('title'): item.get('sheet_id')
                     for item in metadata
                     if item.get('title') and item.get('sheet_id')}
    plans = []
    for mapping in sorted(mappings, key=lambda item: item['source_order']):
        title = mapping['source_sheet']
        expected_id = mapping.get('source_sheet_id')
        actual_id = source_sheets.get(title)
        if not actual_id or actual_id != expected_id:
            raise RuntimeError(f'正式快照子表映射已漂移: {title}')

        info = next(item for item in metadata if item['sheet_id'] == actual_id)
        last_column = col_letter(max(15, int((info.get('grid_properties') or {}).get('column_count') or mapping.get('column_capacity') or 15)))
        if mapping.get('status') == 'mapped_empty':
            from weekly_mapping import find_asin_header
            probe = fc.read_values(snapshot_token, actual_id, f'A1:{last_column}10')
            if find_asin_header(probe):
                raise RuntimeError(f'[{title}] 空表映射已变化，请重新创建批次快照')
            rows, invalid = [], []
        else:
            values = read_rows(fc, snapshot_token, actual_id,
                last=last_column,
                row_count=(info.get('grid_properties') or {}).get('row_count') or mapping.get('row_capacity'))
            rows, invalid = read_source_rows(values, {**cfg, 'source_marketplace': mapping.get('marketplace') or 'US'})

        all_rows = list(rows) + [item['report_row'] for item in invalid]
        all_rows.sort(key=lambda row: row.row_num)
        seen = {}
        duplicates = []
        for row in all_rows:
            if row.asin in seen:
                duplicates.append(f'{row.asin}(rows {seen[row.asin]},{row.row_num})')
            else:
                seen[row.asin] = row.row_num
        if duplicates:
            raise RuntimeError(f'[{title}] 重复 ASIN，禁止初始化结果表: {", ".join(duplicates)}')
        plans.append({
            'mapping': mapping, 'rows': all_rows, 'valid_rows': rows,
            'invalid': invalid, 'invalid_count': len(invalid),
            'values': [_base_values(row) for row in all_rows],
        })
    return plans


def sync_weekly_result_base(fc, store: WeeklyAssetStore, period_id: str,
                            cfg: dict, run_id: str, staged_results=None,
                            selected_sheets=None, checkpoint=None) -> dict:
    """创建/复用结果子表，并从 manifest 固定快照权威同步 A:G。"""
    manifest = store.load(period_id)
    if not manifest or manifest.get('status') != 'ready' or not manifest.get('mapping_ready'):
        raise RuntimeError(f'周期 {period_id} 尚未完成每周资源和子表映射')
    if manifest.setdefault('period_id', period_id) != period_id:
        raise RuntimeError('manifest周期与存储路径不一致')
    if staged_results is not None and manifest.get('snapshot_run_id') not in (None, '', run_id):
        raise RuntimeError('当前快照已被新批次替换，禁止旧批次发布')
    snapshot_token = str((manifest.get('snapshot') or {}).get('spreadsheet_token') or '')
    result_token = str((manifest.get('result') or {}).get('spreadsheet_token') or '')
    assert_result_write_target(manifest, result_token)
    fixed = store.fixed_result()
    if staged_results is not None:
        assert_latest_run(store, manifest, run_id)
    if fixed and (fixed['spreadsheet_token'] != result_token or
                  (staged_results is None and fixed.get('period_id') != period_id)):
        raise RuntimeError('基础数据同步周期或目标与固定结果表不一致')
    if not snapshot_token or snapshot_token == result_token:
        raise RuntimeError('正式快照或独立结果表 Token 非法')

    # 预检先于任何飞书写入；失败时不会留下半套结果子表。
    plans = _read_source_plan(fc, snapshot_token, manifest['sheet_mappings'], cfg)
    if selected_sheets is not None:
        wanted = set(selected_sheets)
        if not wanted or not wanted.issubset({p['mapping']['result_sheet'] for p in plans}):
            raise RuntimeError('本批子表范围不在快照映射中')
        plans = [p for p in plans if p['mapping']['result_sheet'] in wanted]
    if staged_results is not None:
        return _publish_price_rows(fc, store, manifest, plans, staged_results, run_id, checkpoint)
    manifest['business_ready'] = False
    store.save(period_id, manifest)

    target_metadata = fc.query_sheets(result_token)
    existing = {item.get('title'): item.get('sheet_id')
                for item in target_metadata
                if item.get('title') and item.get('sheet_id')}
    capacities = {item.get('sheet_id'): (item.get('grid_properties') or {}).get('row_count')
                  for item in target_metadata}
    summaries = []
    for index, plan in enumerate(plans):
        mapping = plan['mapping']
        title = mapping['result_sheet']
        sheet_id = existing.get(title)
        if not sheet_id:
            sheet_id = fc.add_sheet(result_token, title, index)
            existing[title] = sheet_id

        backup = fc.backup_target_sheet(result_token, title, sheet_id, run_id)
        old = read_rows(fc, result_token, sheet_id, last='A', start=3,
                        row_count=capacities.get(sheet_id))
        old_last = 2 + len(old)
        clear_last = max(old_last, 2 + len(plan['values']))
        fc.write_values(result_token, sheet_id, 'A2:P2', [RESULT_HEADERS + ['']])
        if clear_last >= 3:
            for start in range(3, clear_last + 1, 200):
                end = min(start + 199, clear_last)
                fc.write_values(result_token, sheet_id, f'A{start}:P{end}',
                                [[''] * 16 for _ in range(end - start + 1)])
        if plan['values']:
            for offset in range(0, len(plan['values']), 200):
                chunk = plan['values'][offset:offset + 200]
                fc.write_values(result_token, sheet_id, f'A{3 + offset}:G{2 + offset + len(chunk)}', chunk)

        verified_header = fc.read_values(result_token, sheet_id, 'A2:P2')
        verified_asins = fc.read_values(
            result_token, sheet_id, f'A3:A{2 + len(plan["values"])}') if plan['values'] else []
        actual_asins = [str(row[0]) for row in verified_asins if row]
        expected_asins = [str(row[0]) for row in plan['values']]
        if not verified_header or verified_header[0][:15] != RESULT_HEADERS or any(verified_header[0][15:]):
            raise RuntimeError(f'[{title}] A:O 表头回读校验失败')
        if actual_asins != expected_asins:
            raise RuntimeError(f'[{title}] A:G 数据回读校验失败')
        if plan['values']:
            verify_matrix(fc.read_values(result_token, sheet_id, f'A3:G{2 + len(plan["values"])}'),
                          plan['values'])

        mapping['result_sheet_id'] = sheet_id
        summaries.append({
            'sheet': title, 'sheet_id': sheet_id, 'rows': len(plan['values']),
            'invalid_rows_retained': plan['invalid_count'], 'backup': str(backup),
        })

    manifest['result_base_sync'] = {
        'run_id': run_id, 'synced_at': datetime.now().isoformat(),
        'snapshot_token': snapshot_token, 'result_token': result_token,
        'sheets': summaries,
    }
    manifest['business_ready'] = True
    manifest['base_sync_pending'] = False
    store.save(period_id, manifest)
    return {'period_id': period_id, 'sheets': summaries,
            'row_count': sum(item['rows'] for item in summaries)}


def _publish_price_rows(fc, store, manifest, plans, results, run_id, checkpoint):
    """Publish this snapshot's A:G and this run's prices as verified complete rows."""
    expected_sheets = {p['mapping']['result_sheet'] for p in plans}
    if set(results) != expected_sheets:
        raise RuntimeError('本批必须具备全部选定子表数据，禁止漏表发布')
    token = manifest['result']['spreadsheet_token']
    target_metadata = fc.query_sheets(token)
    existing = {s['title']: s['sheet_id'] for s in target_metadata}
    capacities = {s['sheet_id']: (s.get('grid_properties') or {}).get('row_count') for s in target_metadata}
    blocked, grids, safe = [], {}, {}
    # Validate all inputs and existing layouts before the first cloud mutation.
    for plan in plans:
        title = plan['mapping']['result_sheet']
        if manifest.get('source_fingerprints') is not None and manifest['source_fingerprints'].get(title) != base_fingerprint(plan):
            raise RuntimeError(f'[{title}] 快照基础字段发生变化，禁止混用旧价格')
        by_asin = {}
        for cr in results[title]:
            if cr.run_id != run_id or cr.asin in by_asin:
                raise RuntimeError(f'[{title}] 重复ASIN或run_id不匹配')
            by_asin[cr.asin] = cr
        if set(by_asin) != {r.asin for r in plan['rows']}:
            raise RuntimeError(f'[{title}] ASIN集合与本批快照不一致，禁止遗漏商品')
        if title in existing:
            header = fc.read_values(token, existing[title], 'A2:P2')
            h = header[0] if header else []
            owned_empty = (manifest.get('pending_result_sheets', {}).get(title) == existing[title]
                           and not any(h))
            if not (owned_empty or h[:16] == LEGACY_RESULT_HEADERS or
                    (h[:15] == RESULT_HEADERS and not any(h[15:]))):
                raise RuntimeError(f'[{title}] 未知结果表布局，禁止覆盖')
        safe[title] = {}
        grid = [RESULT_HEADERS + ['']]
        for index, (row, base) in enumerate(zip(plan['rows'], plan['values']), start=3):
            cr = by_asin[row.asin]
            if cr.status in (PageStatus.CRAWL_ERROR, PageStatus.IDENTITY_MISMATCH,
                             PageStatus.PARSE_ERROR, PageStatus.CURRENCY_ERROR):
                blocked.append({'sheet': title, 'asin': row.asin, 'reason': cr.error or cr.status.value})
                # Never carry old prices onto new or reordered source rows.
                price = ['', '-', '', '', '-', cr.timestamp,
                         cr.currency_code if cr.currency_code in ('USD', 'CAD') else '',
                         cr.product_url or '']
            else:
                price = result_values(cr)
                safe[title][index] = row.asin
            grid.append(base + price + [''])
        grids[title] = grid
    report = {'run_id': run_id, 'written_rows': 0, 'base_rows_written': 0,
              'blocked': blocked, 'failures': [], 'verified': {}, 'sheet_count': len(plans)}
    manifest.update(business_ready=False, base_sync_pending=True)
    store.save(manifest['period_id'], manifest)
    for plan in plans:
        title = plan['mapping']['result_sheet']
        try:
            sid = existing.get(title)
            if not sid:
                sid = fc.add_sheet(token, title, plan['mapping']['source_order'] - 1)
                manifest.setdefault('pending_result_sheets', {})[title] = sid
                store.save(manifest['period_id'], manifest)
            plan['mapping']['result_sheet_id'] = sid
            fc.backup_target_sheet(token, title, sid, run_id)
            old = read_rows(fc, token, sid, last='A', start=3, row_count=capacities.get(sid))
            grid = list(grids[title])
            grid.extend([[''] * 16 for _ in range(max(0, 1 + len(old) - len(grid)))])
            for offset in range(0, len(grid), 200):
                assert_latest_run(store, manifest, run_id)
                chunk = grid[offset:offset + 200]
                rng = f'A{2 + offset}:P{1 + offset + len(chunk)}'
                fc.write_values(token, sid, rng, chunk)
                verify_matrix(fc.read_values(token, sid, rng), chunk)
                report['base_rows_written'] += sum(
                    3 <= n <= 2 + len(plan['rows']) for n in range(2 + offset, 2 + offset + len(chunk)))
                for n in range(2 + offset, 2 + offset + len(chunk)):
                    if n in safe[title]:
                        report['written_rows'] += 1
                        report['verified'][f'{title}:{safe[title][n]}'] = True
                if checkpoint:
                    checkpoint(report)
            manifest.get('pending_result_sheets', {}).pop(title, None)
            store.save(manifest['period_id'], manifest)
        except Exception as exc:
            report['failures'].append({'sheet': title, 'stage': 'base_publish', 'error': str(exc)})
            report['blocked'].extend(
                {'sheet': title, 'asin': asin, 'reason': str(exc)}
                for asin in safe[title].values() if f'{title}:{asin}' not in report['verified'])
            if checkpoint:
                checkpoint(report)
            continue
    manifest['business_ready'] = not report['failures']
    manifest['base_sync_pending'] = bool(report['failures'])
    manifest['result_base_sync'] = {'run_id': run_id, 'synced_at': datetime.now().isoformat(),
                                    'selected_sheets': sorted(expected_sheets), 'report': report}
    store.save(manifest['period_id'], manifest)
    return report


def result_values(cr) -> list:
    if (cr.currency_code or '') not in ('', 'USD', 'CAD'):
        raise RuntimeError(f'{cr.asin} 币种不允许写入: {cr.currency_code!r}')
    return cr.six_columns() + [cr.currency_code or '', cr.product_url or '']


def scalar_cell(value):
    """Convert Feishu read-side rich links to safe scalar write values."""
    if isinstance(value, list):
        if not value:
            return ''
        if len(value) != 1:
            raise RuntimeError('单元格包含多段富文本，禁止有损迁移')
        return scalar_cell(value[0])
    if isinstance(value, dict):
        return value.get('link') or value.get('text') or value.get('value') or ''
    return '' if value is None else value


def cells_equal(actual, expected):
    actual, expected = scalar_cell(actual), scalar_cell(expected)
    if actual == expected or (actual in ('', None) and expected in ('', None)):
        return True
    try:
        if isinstance(actual, (int, float)) and str(expected).endswith('%'):
            return Decimal(str(actual)) == Decimal(str(expected)[:-1]) / 100
        return Decimal(str(actual)) == Decimal(str(expected))
    except Exception:
        return str(actual) == str(expected)


def verify_matrix(actual, expected):
    for index, row in enumerate(expected):
        got = actual[index] if index < len(actual) else []
        padded = list(got) + [''] * max(0, len(row) - len(got))
        if len(padded) != len(row) or any(not cells_equal(a, b) for a, b in zip(padded, row)):
            raise RuntimeError(f'回读校验失败，范围内第{index + 1}行')


def _contiguous(rows: dict[int, list]):
    keys = sorted(rows)
    if not keys:
        return
    start = previous = keys[0]
    values = [rows[start]]
    for row in keys[1:]:
        if row == previous + 1 and len(values) < 200:
            values.append(rows[row])
        else:
            yield start, previous, values
            start, values = row, [rows[row]]
        previous = row
    yield start, previous, values


def write_weekly_result_columns(fc, manifest: dict, run_id: str,
                                results_by_sheet: dict[str, list], cfg: dict,
                                checkpoint=None) -> dict:
    """预检后把同一 run_id 的 H:O 写入独立结果表；旧布局备份后迁移。"""
    result_token = str((manifest.get('result') or {}).get('spreadsheet_token') or '')
    assert_result_write_target(manifest, result_token)
    if not manifest.get('business_ready'):
        raise RuntimeError('本周结果表尚未完成 A:G 初始化')
    mappings = {item['result_sheet']: item for item in manifest.get('sheet_mappings') or []}
    target_metadata = fc.query_sheets(result_token)
    capacities = {item.get('sheet_id'): (item.get('grid_properties') or {}).get('row_count')
                  for item in target_metadata}
    plans = []
    blocked = []
    migrations = []
    required = bool(cfg.get('html_archive_required', True))

    # 所有布局、ASIN和run_id校验必须先于第一笔写入。
    for sheet, crawls in results_by_sheet.items():
        mapping = mappings.get(sheet)
        sheet_id = (mapping or {}).get('result_sheet_id')
        if not sheet_id:
            raise RuntimeError(f'[{sheet}] manifest 缺少结果 Sheet ID')
        header = fc.read_values(result_token, sheet_id, 'A2:P2')
        if header and header[0][:16] == LEGACY_RESULT_HEADERS:
            migrations.append((sheet, sheet_id))
        elif not header or header[0][:15] != RESULT_HEADERS or any(header[0][15:]):
            raise RuntimeError(f'[{sheet}] A:O 布局预检失败')
        asin_values = read_rows(fc, result_token, sheet_id, last='A', start=3,
                                row_count=capacities.get(sheet_id))
        asin_map = {}
        for offset, value in enumerate(asin_values, start=3):
            asin = str(value[0] if value else '').strip()
            if not asin:
                continue
            if asin in asin_map:
                raise RuntimeError(f'[{sheet}] 结果表重复 ASIN: {asin}')
            asin_map[asin] = offset
        rows = {}
        seen_crawls = set()
        for cr in crawls:
            if cr.asin in seen_crawls:
                raise RuntimeError(f'[{sheet}] 当前抓取结果重复 ASIN: {cr.asin}')
            seen_crawls.add(cr.asin)
            if cr.run_id != run_id:
                raise RuntimeError(f'[{sheet}] {cr.asin} 不属于当前 run_id {run_id}')
            row_number = asin_map.get(cr.asin)
            if not row_number:
                raise RuntimeError(f'[{sheet}] 结果表找不到 ASIN: {cr.asin}')
            if cr.status == PageStatus.CURRENCY_ERROR:
                blocked.append({'sheet': sheet, 'asin': cr.asin,
                                'reason': 'currency_error'})
                continue
            if cr.status in (PageStatus.CRAWL_ERROR, PageStatus.IDENTITY_MISMATCH,
                             PageStatus.PARSE_ERROR):
                blocked.append({'sheet': sheet, 'asin': cr.asin,
                                'reason': cr.error or cr.status.value})
                continue
            if required and cr.status in (PageStatus.OK, PageStatus.SOLD_OUT,
                                          PageStatus.PAGE_NOT_FOUND):
                if cr.archive_status != 'ok' or not cr.html_url:
                    blocked.append({'sheet': sheet, 'asin': cr.asin,
                                    'reason': cr.archive_error or 'html_archive_missing'})
                    continue
            rows[row_number] = result_values(cr)
        plans.append((sheet, sheet_id, rows, {v: k for k, v in asin_map.items()}))

    # Only the known N:P result columns are touched. A:G and source/snapshot
    # stay unchanged. Header and values move in ONE request per sheet, making
    # retries idempotent if a preceding request succeeded but its response was lost.
    report = {'run_id': run_id, 'written_rows': 0, 'blocked': blocked,
              'sheet_count': len(plans), 'failures': [], 'verified': {}}
    migration_sheets = {sheet for sheet, _ in migrations}
    for sheet, sheet_id, rows, asins in plans:
        try:
            fc.backup_target_sheet(result_token, sheet, sheet_id, run_id)
            if sheet in migration_sheets:
                old = read_rows(fc, result_token, sheet_id, first='N', last='P', start=2,
                                row_count=capacities.get(sheet_id))
                if not old or old[0][:3] != ['HTML链接', '币种', 'Amazon链接']:
                    raise RuntimeError(f'[{sheet}] 迁移前N:P表头已变化')
                shifted = []
                for row in old:
                    padded = list(row) + [''] * (3 - len(row))
                    shifted.append([scalar_cell(padded[1]), scalar_cell(padded[2]), ''])
                rng = f'N2:P{1 + len(shifted)}'
                fc.write_values(result_token, sheet_id, rng, shifted)
                verify_matrix(fc.read_values(result_token, sheet_id, rng), shifted)
                check = fc.read_values(result_token, sheet_id, 'A2:P2')
                if not check or check[0][:15] != RESULT_HEADERS or any(check[0][15:]):
                    raise RuntimeError(f'[{sheet}] 无HTML列迁移回读失败')
        except Exception as exc:
            report['failures'].append({'sheet': sheet, 'stage': 'backup_or_migration', 'error': str(exc)})
            blocked.extend({'sheet': sheet, 'asin': asins[r], 'reason': str(exc)} for r in rows)
            if checkpoint:
                checkpoint(report)
            continue
        for start, end, values in _contiguous(rows):
            try:
                expected_asins = [[asins[r]] for r in range(start, end + 1)]
                verify_matrix(fc.read_values(result_token, sheet_id, f'A{start}:A{end}'), expected_asins)
                rng = f'H{start}:O{end}'
                fc.write_values(result_token, sheet_id, rng, values)
                actual = fc.read_values(result_token, sheet_id, f'A{start}:O{end}')
                verify_matrix([[row[0]] if row else [] for row in actual], expected_asins)
                verify_matrix([row[7:15] for row in actual], values)
                report['written_rows'] += len(values)
                for row in range(start, end + 1):
                    report['verified'][f'{sheet}:{asins[row]}'] = True
            except Exception as exc:
                report['failures'].append({'sheet': sheet, 'range': f'H{start}:O{end}',
                                           'stage': 'write_or_verify', 'error': str(exc)})
                blocked.extend({'sheet': sheet, 'asin': asins[r], 'reason': str(exc)}
                               for r in range(start, end + 1))
            if checkpoint:
                checkpoint(report)
    return report
