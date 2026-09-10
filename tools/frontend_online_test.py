"""Run the original weekly frontend flow, then publish an isolated copy.

This tool deliberately invokes the application's original ``app/run.py``
weekly-run entry point. It does not create a second browser/CDP path and does
not read archived HTML for runtime decisions. The weekly-run is always
``--dry-run --force-fetch``; its resulting local bundle is then copied into a
new isolated Feishu test workbook and read back.

The tool writes only to a newly-created ``TEST_前端真实页面验证_*`` Feishu
workbook and reads every written range back. It never writes the production
result spreadsheet.

Examples::

    python tools/frontend_online_test.py --phase single --sheet PD03

    python tools/frontend_online_test.py --phase all
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / 'app'))

from config import OUTPUT_DIR, load_config  # noqa: E402
from feishu import FeishuClient  # noqa: E402
from frontend_checks import (  # noqa: E402
    FRONTEND_HEADERS,
    FRONTEND_CHECK_RULE_VERSION,
    frontend_status_values,
    unknown_checks,
)
from cache import _crawl_from_dict  # noqa: E402
from main import row_from_dict  # noqa: E402
from weekly_result import result_values_with_frontend  # noqa: E402


RESULT_HEADERS = [
    'ASIN', 'SKU', '尺寸', '正常售价', '本周折扣形式', '本周折扣%', '目标成交价',
    '展示价格', '折扣类型', '折扣值', '最终价格', '一致性检查', '币种',
    *FRONTEND_HEADERS, '时间戳', 'Amazon链接',
]

SUMMARY_HEADERS = [
    '测试阶段', '子表', '源行数', '实时抓取行数', '技术异常行数',
    '写入行数', 'pass检查数', 'fail检查数', 'unknown检查数',
    '页面状态汇总', '写入回读状态', 'run_id', '测试时间', '本地证据目录',
]

FORMULA_SIZE_PREFIXES = ('=', 'IF(', '=IF(', 'IFNA(', '=IFNA(')


def _col_letter(number: int) -> str:
    value = ''
    while number:
        number, rem = divmod(number - 1, 26)
        value = chr(65 + rem) + value
    return value


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')


def _safe_size(value) -> str:
    """Do not compare a formula string as if it were a displayed size."""
    text = str(value or '').strip()
    if text.upper().startswith(FORMULA_SIZE_PREFIXES):
        return ''
    return text


def _same_cell(left, right) -> bool:
    """Compare Feishu scalar cells and rich-text URL cells safely."""
    if isinstance(left, list) and len(left) == 1:
        return _same_cell(left[0], right)
    if isinstance(left, dict) and isinstance(right, str):
        return str(left.get('link') or left.get('text') or '') == right
    if left is None and right in (None, ''):
        return True
    if right is None and left in (None, ''):
        return True
    return str(left) == str(right)


def _same_matrix(actual: list[list], expected: list[list]) -> bool:
    if len(actual) != len(expected):
        return False
    for actual_row, expected_row in zip(actual, expected):
        if len(actual_row) != len(expected_row):
            return False
        if not all(_same_cell(a, e) for a, e in zip(actual_row, expected_row)):
            return False
    return True


def _protected_tokens() -> set[str]:
    protected: set[str] = set()
    try:
        cfg = load_config()
    except Exception:
        cfg = {}
    feedback = cfg.get('feedback') or {}
    if feedback.get('target_spreadsheet_token'):
        protected.add(str(feedback['target_spreadsheet_token']))
    for path in (
        OUTPUT_DIR / 'weekly_runs' / 'fixed_result.json',
        OUTPUT_DIR / 'weekly_runs' / 'active_result.json',
    ):
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        result = data.get('result') or {}
        if result.get('spreadsheet_token'):
            protected.add(str(result['spreadsheet_token']))
    return protected


WEEKLY_RUN_RE = re.compile(r'===== weekly-run\s+(\S+)')


def _run_original_weekly(phase: str, sheet: str | None = None) -> tuple[str, Path, float, str]:
    """Invoke the same app entry point used by the normal weekly runner."""
    command = [
        sys.executable, str(PROJECT_ROOT / 'app' / 'run.py'),
        '--weekly-run', '--dry-run', '--force-fetch',
    ]
    if phase == 'single':
        command.extend(['--sheets', sheet or 'PD03', '--limit', '1'])
    started = time.monotonic()
    completed = subprocess.run(
        command,
        cwd=str(PROJECT_ROOT),
        env=dict(os.environ),
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        check=False,
    )
    elapsed = time.monotonic() - started
    output = (completed.stdout or '') + (completed.stderr or '')
    match = WEEKLY_RUN_RE.search(output)
    if not match:
        # The logger is file-backed in some Windows launch modes; use the
        # newest bundle as a fallback only when it was created by this call.
        candidates = sorted(
            OUTPUT_DIR.glob(f'daily_runs/*/*_weekly_bundle.json'),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if candidates and time.time() - candidates[0].stat().st_mtime <= elapsed + 30:
            run_id = candidates[0].name.removesuffix('_weekly_bundle.json')
        else:
            raise RuntimeError(
                f'原 weekly-run 没有返回 run_id，退出码={completed.returncode}: '
                f'{output[-2000:]}')
    else:
        run_id = match.group(1)
    bundles = list(OUTPUT_DIR.glob(f'daily_runs/*/{run_id}_weekly_bundle.json'))
    if not bundles:
        raise RuntimeError(
            f'原 weekly-run 未生成 bundle: {run_id}，退出码={completed.returncode}: '
            f'{output[-2000:]}')
    if completed.returncode != 0:
        raise RuntimeError(
            f'原 weekly-run 退出码={completed.returncode}: {output[-2000:]}')
    evidence_log = OUTPUT_DIR / 'frontend_online_tests' / 'original_runs' / f'{run_id}.log'
    evidence_log.parent.mkdir(parents=True, exist_ok=True)
    evidence_log.write_text(output, encoding='utf-8')
    return run_id, bundles[0], elapsed, str(evidence_log)


def _load_source_rows(source_path: Path | None) -> dict[str, dict[str, object]]:
    """Load optional source metadata; it never supplies runtime page HTML."""
    if source_path is None or not source_path.is_file():
        return {}
    data = json.loads(source_path.read_text(encoding='utf-8'))
    rows: dict[str, dict[str, object]] = {}
    for values in (data.get('sheets') or {}).values():
        for item in values or []:
            asin = str(item.get('asin') or '').strip().upper()
            if asin:
                rows[asin] = item
    return rows


def _load_bundle(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def _save_manifest(run_dir: Path, manifest: dict) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / 'manifest.json'
    tmp = path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


def _ensure_sheet(fc: FeishuClient, token: str, title: str, index: int) -> str:
    sheets = fc.query_sheets(token)
    matches = [item for item in sheets if item.get('title') == title]
    if len(matches) > 1:
        raise RuntimeError(f'测试表子表重名，停止写入: {title}')
    if matches:
        return str(matches[0]['sheet_id'])
    return fc.add_sheet(token, title, index)


def _write_verified(fc: FeishuClient, token: str, sheet_id: str,
                    start_row: int, start_col: int, matrix: list[list]) -> float:
    if not matrix:
        return 0.0
    started = time.monotonic()
    width = len(matrix[0])
    end_col = _col_letter(start_col + width - 1)
    for offset in range(0, len(matrix), 200):
        chunk = matrix[offset:offset + 200]
        first = start_row + offset
        last = first + len(chunk) - 1
        rng = f'{_col_letter(start_col)}{first}:{end_col}{last}'
        fc.write_values(token, sheet_id, rng, chunk)
        actual = fc.read_values(token, sheet_id, rng)
        if not _same_matrix(actual, chunk):
            raise RuntimeError(f'写后回读不一致: {sheet_id}!{rng}')
    return time.monotonic() - started


def _ensure_header(fc: FeishuClient, token: str, sheet_id: str,
                   headers: list[str], header_row: int = 2) -> float:
    end_col = _col_letter(len(headers))
    rng = f'A{header_row}:{end_col}{header_row}'
    current = fc.read_values(token, sheet_id, rng)
    if current and any(str(cell or '').strip() for cell in current[0]):
        if not _same_matrix(current[:1], [headers]):
            raise RuntimeError(f'测试子表已有不同表头，停止覆盖: {sheet_id}!{rng}')
        return 0.0
    return _write_verified(fc, token, sheet_id, header_row, 1, [headers])


def _create_target(fc: FeishuClient, run_id: str) -> tuple[str, str, str]:
    title = f'TEST_前端真实页面验证_{run_id}'
    created = fc.create_spreadsheet(title, '')
    token = str(created.get('spreadsheet_token') or '')
    if not token:
        raise RuntimeError('测试 Spreadsheet 创建成功但缺少 token')
    if token in _protected_tokens():
        raise RuntimeError('新建测试 Spreadsheet 与受保护生产 Token 重合，停止写入')
    url = str(created.get('url') or f'https://wit0jhu6kvu.feishu.cn/sheets/{token}')
    return token, title, url


def _build_output_rows(records: list[dict], source_by_asin: dict[str, dict]) -> tuple[list[list], Counter, Counter]:
    output = []
    stats = Counter()
    page_statuses = Counter()
    for record in records:
        asin = str(record.get('asin') or '').strip().upper()
        source = source_by_asin.get(asin) or {}
        row = row_from_dict({
            'row_num': source.get('row_num') or 0,
            'asin': asin,
            'sku': source.get('sku') or '',
            'size': source.get('size') or '',
            'normal_price': source.get('normal_price'),
            'h_type': source.get('h_type') or '',
            'i_value': source.get('i_value'),
            'target_price': source.get('target_price'),
            'target_price_source': source.get('target_price_source') or '',
            'marketplace': record.get('marketplace') or source.get('marketplace') or 'US',
            'product_url': record.get('product_url') or source.get('product_url') or '',
            'source_product_url': record.get('source_product_url') or source.get('source_product_url') or '',
        })
        crawl = _crawl_from_dict(record)
        if crawl is None:
            raise RuntimeError(f'weekly bundle 包含无法还原的记录: {asin}')
        checks = crawl.frontend_checks or unknown_checks(
            f'实时页面未产生前端证据: {crawl.status.value}',
            page_url=crawl.page_url or crawl.product_url,
            captured_at=crawl.timestamp,
        )
        statuses = frontend_status_values(
            checks, page_status=getattr(crawl.status, 'value', str(crawl.status or '')))
        stats.update(statuses)
        page_status = getattr(crawl.status, 'value', str(crawl.status or ''))
        page_statuses[page_status] += 1
        if page_status in {
                'crawl_error', 'identity_mismatch', 'currency_error',
                'parse_error', 'page_not_found'}:
            stats['technical_errors'] += 1
        values = result_values_with_frontend(crawl)
        output.append([
            row.asin,
            row.sku,
            _safe_size(row.size),
            str(row.normal_price or ''),
            row.h_type or '',
            '' if row.i_value is None else str(row.i_value),
            str(row.target_price or ''),
            *values,
        ])
    return output, stats, page_statuses


def _summary_row(stage: str, title: str, source_count: int, live_count: int,
                 stats: Counter,
                 page_statuses: Counter, written_count: int, run_id: str,
                 tested_at: str, evidence_dir: Path) -> list:
    status = 'complete' if not stats.get('technical_errors') else 'partial_review'
    return [
        stage, title, source_count, live_count, int(stats.get('technical_errors', 0)),
        written_count, int(stats.get('pass', 0)), int(stats.get('fail', 0)),
        int(stats.get('unknown', 0)) + int(stats.get('not_applicable', 0)),
        json.dumps(dict(page_statuses), ensure_ascii=False, sort_keys=True),
        status, run_id, tested_at, str(evidence_dir),
    ]


def run(args) -> dict:
    started = time.monotonic()
    requested_sheet = args.sheet if args.phase == 'single' else None
    run_id, bundle_path, original_elapsed, original_log = _run_original_weekly(
        args.phase, requested_sheet)
    bundle = _load_bundle(bundle_path)
    bundle_sheets = bundle.get('sheets') or {}
    if args.phase == 'single':
        requested = [requested_sheet or 'PD03']
        if requested[0] not in bundle_sheets:
            raise RuntimeError(f'原 weekly-run bundle 没有指定子表: {requested[0]}')
    else:
        requested = list(bundle_sheets)
    if not requested:
        raise RuntimeError(f'原 weekly-run bundle 没有任何结果行: {bundle_path}')

    source_path = Path(args.source).resolve() if args.source else None
    source_by_asin = _load_source_rows(source_path)
    generated_source = OUTPUT_DIR / 'snapshots' / run_id / 'source.json'
    source_by_asin.update(_load_source_rows(generated_source))
    run_dir = (OUTPUT_DIR / 'frontend_online_tests' / run_id).resolve()
    manifest = {
        'schema_version': 3,
        'run_id': run_id,
        'created_at': _now(),
        'original_entrypoint': 'app/run.py --weekly-run --dry-run --force-fetch',
        'original_bundle': str(bundle_path),
        'original_log': original_log,
        'original_elapsed_seconds': round(original_elapsed, 3),
        'source_snapshot': str(generated_source),
        'source_mode': 'original_weekly_run_live_bundle',
        'offline_html_role': 'fixture_only_not_runtime_input',
        'frontend_check_rule_version': FRONTEND_CHECK_RULE_VERSION,
        'target_token': '',
        'target_title': '',
        'target_url': '',
        'summary_rows': [],
        'phases': [],
    }

    records = [record for title in requested for record in (bundle_sheets.get(title) or [])]
    live_statuses = {str(record.get('status') or '') for record in records}
    if records and not live_statuses.intersection({'ok', 'sold_out', 'page_not_found',
                                                    'identity_mismatch', 'parse_error',
                                                    'currency_error', 'source_data_invalid'}):
        raise RuntimeError(
            f'原 weekly-run 全部为技术异常，拒绝创建测试表: {dict(Counter(live_statuses))}')

    cfg = load_config()
    fc = FeishuClient(cfg)
    token, title, url = _create_target(fc, run_id)
    manifest.update(target_token=token, target_title=title, target_url=url)
    _save_manifest(run_dir, manifest)

    tested_at = _now()
    stage = 'single_original' if args.phase == 'single' else 'all_original'
    phase_report = {
        'stage': stage,
        'sheets': [],
        'started_at': tested_at,
        'source_mode': manifest['source_mode'],
        'offline_html_role': manifest['offline_html_role'],
        'original_entrypoint': manifest['original_entrypoint'],
        'original_bundle': str(bundle_path),
    }
    summary_id = _ensure_sheet(fc, token, 'TEST_SUMMARY', 0)
    for sheet_index, sheet_title in enumerate(requested, start=1):
        sheet_started = time.monotonic()
        sheet_id = _ensure_sheet(fc, token, sheet_title, sheet_index)
        _ensure_header(fc, token, sheet_id, RESULT_HEADERS)
        records = list(bundle_sheets.get(sheet_title) or [])
        rows, stats, page_statuses = _build_output_rows(records, source_by_asin)
        write_elapsed = _write_verified(fc, token, sheet_id, 3, 1, rows)
        live_count = sum(1 for record in records
                         if str(record.get('status') or '') != 'source_data_invalid')
        summary = _summary_row(
            stage, sheet_title, len(records), live_count, stats, page_statuses,
            len(rows), run_id, tested_at, run_dir)
        manifest['summary_rows'].append(summary)
        phase_report['sheets'].append({
            'sheet': sheet_title,
            'sheet_id': sheet_id,
            'source_rows': len(records),
            'live_crawled': live_count,
            'written_rows': len(rows),
            'technical_errors': int(stats.get('technical_errors', 0)),
            'pass_checks': int(stats.get('pass', 0)),
            'fail_checks': int(stats.get('fail', 0)),
            'unknown_checks': int(stats.get('unknown', 0)) + int(stats.get('not_applicable', 0)),
            'page_statuses': dict(page_statuses),
            'write_seconds': round(write_elapsed, 3),
            'elapsed_seconds': round(time.monotonic() - sheet_started, 3),
        })
        _save_manifest(run_dir, manifest)

    _ensure_header(fc, token, summary_id, SUMMARY_HEADERS)
    _write_verified(fc, token, summary_id, 3, 1, manifest['summary_rows'])
    phase_report['finished_at'] = _now()
    phase_report['elapsed_seconds'] = round(time.monotonic() - started, 3)
    manifest['phases'].append(phase_report)
    manifest['last_phase'] = stage
    manifest['updated_at'] = phase_report['finished_at']
    manifest['target_sheet_ids'] = {
        item['sheet']: item['sheet_id'] for item in phase_report['sheets']
    }
    _save_manifest(run_dir, manifest)
    report_path = run_dir / f'{stage}_report.json'
    report_path.write_text(json.dumps(phase_report, ensure_ascii=False, indent=2), encoding='utf-8')
    return {
        'run_id': run_id,
        'run_dir': str(run_dir),
        'target_token': token,
        'target_url': manifest['target_url'],
        'stage': stage,
        'report_path': str(report_path),
        'original_bundle': str(bundle_path),
        'original_log': original_log,
        'sheet_count': len(requested),
        'written_rows': sum(item['written_rows'] for item in phase_report['sheets']),
        'elapsed_seconds': phase_report['elapsed_seconds'],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description='真实 Amazon 页面前端七项结果在线测试')
    parser.add_argument('--phase', choices=('single', 'all'), required=True)
    parser.add_argument('--sheet', default='PD03', help='single 阶段的子表名')
    parser.add_argument('--source', help='可选旧源快照，仅补充显示字段；不参与实时页面导航')
    args = parser.parse_args()
    result = run(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
