"""Write frontend-only validation results to an isolated Feishu test workbook.

This tool deliberately does not use the production weekly manifest or result
token.  It reads a local source snapshot plus archived product HTML, evaluates
the pure frontend checks, writes A:V-shaped test rows to a newly-created
``TEST_前端规则验证_*`` workbook, and reads every written range back.

Run in two phases:

    python tools/frontend_online_test.py --phase single --sheet PD03 \
        --source outputs/snapshots/20260908_155743/source.json \
        --html-root D:\\projects\\amazon_daily\\htmls

    python tools/frontend_online_test.py --phase all --run-dir <first-run-dir> \
        --source outputs/snapshots/20260908_155743/source.json \
        --html-root D:\\projects\\amazon_daily\\htmls

The online writes are intentionally outside the normal production entry point.
"""
from __future__ import annotations

import argparse
import json
import re
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
    CHECK_KEYS,
    FRONTEND_HEADERS,
    FRONTEND_CHECK_RULE_VERSION,
    frontend_columns,
    inspect_frontend,
    unknown_checks,
)


RESULT_HEADERS = [
    'ASIN', 'SKU', '尺寸', '正常售价', '本周折扣形式', '本周折扣%', '目标成交价',
    '展示价格', '折扣类型', '折扣值', '最终价格', '一致性检查', '币种',
    *FRONTEND_HEADERS, '时间戳', 'Amazon链接',
]

SUMMARY_HEADERS = [
    '测试阶段', '子表', '源行数', 'HTML匹配行数', 'HTML缺失行数',
    '写入行数', 'pass检查数', 'fail检查数', 'unknown检查数',
    '写入回读状态', 'run_id', '测试时间', '本地证据目录',
]

ASIN_FILE_RE = re.compile(r'_([A-Z0-9]{10})\.html$', re.I)
FORMULA_SIZE_RE = re.compile(r'^\s*(?:=)?IF\s*\(', re.I)


def _col_letter(number: int) -> str:
    value = ''
    while number:
        number, rem = divmod(number - 1, 26)
        value = chr(65 + rem) + value
    return value


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')


def _safe_size(value) -> str:
    text = str(value or '').strip()
    # Formula text in the cached source snapshot is not an expected dimension.
    return '' if FORMULA_SIZE_RE.match(text) else text


def _same_cell(left, right) -> bool:
    # Feishu returns URL cells as rich-text objects on read-back even when the
    # write payload used a plain URL string.  Compare the canonical link/text
    # value while keeping all other scalar comparisons strict.
    if isinstance(left, list) and len(left) == 1:
        return _same_cell(left[0], right)
    if isinstance(left, dict) and isinstance(right, str):
        link = str(left.get('link') or left.get('text') or '')
        return link == right
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
    for key in ('feedback',):
        item = cfg.get(key) or {}
        for token_key in ('target_spreadsheet_token',):
            if item.get(token_key):
                protected.add(str(item[token_key]))
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


def _source_sheets(source_path: Path) -> dict[str, list[dict]]:
    data = json.loads(source_path.read_text(encoding='utf-8'))
    sheets = data.get('sheets')
    if not isinstance(sheets, dict) or not sheets:
        raise RuntimeError(f'源快照缺少 sheets 对象: {source_path}')
    return {str(title): list(rows or []) for title, rows in sheets.items()}


def _html_index(html_root: Path) -> dict[str, Path]:
    if not html_root.is_dir():
        raise RuntimeError(f'HTML根目录不存在: {html_root}')
    index: dict[str, Path] = {}
    for path in sorted(html_root.rglob('*.html')):
        match = ASIN_FILE_RE.search(path.name)
        if match:
            # Lexicographically latest archived batch wins deterministically.
            index[match.group(1).upper()] = path
    if not index:
        raise RuntimeError(f'HTML根目录没有发现带ASIN文件名的HTML: {html_root}')
    return index


def _load_manifest(run_dir: Path) -> dict:
    path = run_dir / 'manifest.json'
    if not path.is_file():
        raise RuntimeError(f'测试运行目录缺少 manifest.json: {run_dir}')
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


def _build_rows(rows: list[dict], html_by_asin: dict[str, Path], captured_at: str):
    output = []
    stats = Counter()
    for source_row in rows:
        asin = str(source_row.get('asin') or '').strip().upper()
        product_url = str(source_row.get('product_url') or '').strip()
        expected_size = _safe_size(source_row.get('size'))
        html_path = html_by_asin.get(asin)
        if html_path is None:
            checks = unknown_checks(
                f'未找到请求 ASIN 对应的离线HTML快照: {asin}',
                page_url=product_url, captured_at=captured_at)
            stats['html_missing'] += 1
        else:
            try:
                html = html_path.read_text(encoding='utf-8', errors='ignore')
            except OSError as exc:
                checks = unknown_checks(
                    f'读取离线HTML失败: {exc}', page_url=product_url,
                    captured_at=captured_at)
                stats['html_missing'] += 1
            else:
                checks = inspect_frontend(
                    html, expected_size=expected_size, asin=asin,
                    page_url=product_url, captured_at=captured_at)
                stats['html_matched'] += 1
        statuses = [str((checks.get(key) or {}).get('status') or 'unknown')
                    for key in CHECK_KEYS]
        stats.update(statuses)
        display_size = expected_size
        output.append([
            asin,
            str(source_row.get('sku') or ''),
            display_size,
            str(source_row.get('normal_price') or ''),
            str(source_row.get('h_type') or ''),
            '' if source_row.get('i_value') is None else str(source_row.get('i_value')),
            str(source_row.get('target_price') or ''),
            '', '', '', '', '', '',
            *frontend_columns(checks),
            captured_at,
            product_url,
        ])
    return output, stats


def _summary_row(stage: str, title: str, source_count: int, stats: Counter,
                 written_count: int, elapsed: float, run_id: str,
                 tested_at: str, evidence_dir: Path) -> list:
    missing = int(stats.get('html_missing', 0))
    status = 'complete' if missing == 0 else 'partial_unknown'
    return [
        stage, title, source_count, int(stats.get('html_matched', 0)), missing,
        written_count, int(stats.get('pass', 0)), int(stats.get('fail', 0)),
        int(stats.get('unknown', 0)) + int(stats.get('not_applicable', 0)),
        status, run_id, tested_at, str(evidence_dir),
    ]


def _create_target(fc: FeishuClient, run_id: str) -> tuple[str, str, str]:
    title = f'TEST_前端规则验证_{run_id}'
    created = fc.create_spreadsheet(title, '')
    token = str(created.get('spreadsheet_token') or '')
    if not token:
        raise RuntimeError('测试 Spreadsheet 创建成功但缺少 token')
    if token in _protected_tokens():
        raise RuntimeError('新建测试 Spreadsheet 与受保护生产 Token 重合，停止写入')
    url = str(created.get('url') or f'https://wit0jhu6kvu.feishu.cn/sheets/{token}')
    return token, title, url


def run(args) -> dict:
    started = time.monotonic()
    source_path = Path(args.source).resolve()
    html_root = Path(args.html_root).resolve()
    source_sheets = _source_sheets(source_path)
    if args.phase == 'single':
        requested = [args.sheet]
        if args.sheet not in source_sheets:
            raise RuntimeError(f'源快照没有指定子表: {args.sheet}')
    else:
        requested = list(source_sheets)

    if args.run_dir:
        run_dir = Path(args.run_dir).resolve()
        manifest = _load_manifest(run_dir)
        run_id = str(manifest.get('run_id') or args.run_id)
        token = str(manifest.get('target_token') or '')
        if not token:
            raise RuntimeError('测试 manifest 缺少 target_token')
    else:
        run_id = args.run_id or datetime.now().strftime('%Y%m%d_%H%M%S_frontend')
        token = ''
        run_dir = (OUTPUT_DIR / 'frontend_online_tests' / run_id).resolve()
        manifest = {
            'schema_version': 1,
            'run_id': run_id,
            'created_at': _now(),
            'source_path': str(source_path),
            'html_root': str(html_root),
            'frontend_check_rule_version': FRONTEND_CHECK_RULE_VERSION,
            'target_token': '',
            'target_title': '',
            'target_url': '',
            'summary_rows': [],
            'phases': [],
        }

    cfg = load_config()
    fc = FeishuClient(cfg)
    if not token:
        token, title, url = _create_target(fc, run_id)
        manifest.update(target_token=token, target_title=title, target_url=url)
        _save_manifest(run_dir, manifest)
    elif token in _protected_tokens():
        raise RuntimeError('传入的测试 Token 属于受保护资源，拒绝写入')

    html_by_asin = _html_index(html_root)
    tested_at = _now()
    stage = 'single' if args.phase == 'single' else 'all'
    phase_report = {'stage': stage, 'sheets': [], 'started_at': tested_at}

    # Keep the target workbook structurally obvious.  The default Sheet1, if
    # present, is never used for product output; TEST_SUMMARY is explicit.
    summary_id = _ensure_sheet(fc, token, 'TEST_SUMMARY', 0)
    for sheet_index, title in enumerate(requested, start=1):
        sheet_started = time.monotonic()
        sheet_id = _ensure_sheet(fc, token, title, sheet_index)
        _ensure_header(fc, token, sheet_id, RESULT_HEADERS)
        rows, stats = _build_rows(source_sheets[title], html_by_asin, tested_at)
        write_elapsed = _write_verified(fc, token, sheet_id, 3, 1, rows)
        summary = _summary_row(
            stage, title, len(source_sheets[title]), stats, len(rows),
            time.monotonic() - sheet_started, run_id, tested_at, run_dir)
        manifest['summary_rows'].append(summary)
        phase_report['sheets'].append({
            'sheet': title,
            'sheet_id': sheet_id,
            'source_rows': len(source_sheets[title]),
            'written_rows': len(rows),
            'html_matched': int(stats.get('html_matched', 0)),
            'html_missing': int(stats.get('html_missing', 0)),
            'pass_checks': int(stats.get('pass', 0)),
            'fail_checks': int(stats.get('fail', 0)),
            'unknown_checks': int(stats.get('unknown', 0)) + int(stats.get('not_applicable', 0)),
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
        'sheet_count': len(requested),
        'written_rows': sum(item['written_rows'] for item in phase_report['sheets']),
        'elapsed_seconds': phase_report['elapsed_seconds'],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description='前端七项结果在线测试表写入与回读')
    parser.add_argument('--phase', choices=('single', 'all'), required=True)
    parser.add_argument('--sheet', default='PD03', help='single 阶段的子表名')
    parser.add_argument('--run-dir', help='复用 single 阶段返回的本地测试目录')
    parser.add_argument('--run-id', help='新建测试表使用的唯一 run_id')
    parser.add_argument('--source', required=True, help='本地 source.json 快照')
    parser.add_argument('--html-root', required=True, help='离线商品 HTML 根目录')
    args = parser.parse_args()
    result = run(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
