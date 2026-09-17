# -*- coding: utf-8 -*-
"""每周飞书资源生命周期：幂等初始化、manifest、排他锁与只读保护。"""
from __future__ import annotations

import json
import hashlib
import os
import re
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from weekly_registry import validate_feishu_resource_url
from runtime_state import atomic_json


def safe_period_id(period_id: str) -> str:
    value = str(period_id or '').strip()
    if not value or not re.fullmatch(r'[A-Za-z0-9_-]+', value):
        raise RuntimeError(f'period_id 不适合用作目录名: {period_id!r}')
    return value


class WeeklyAssetStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    def manifest_path(self, period_id: str) -> Path:
        return self.root / safe_period_id(period_id) / 'weekly_manifest.json'

    def fixed_result(self):
        path = self.root / 'fixed_result.json'
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding='utf-8'))
        if not data.get('spreadsheet_token') or not data.get('url'):
            raise RuntimeError('固定结果表登记不完整，禁止新建替代结果表')
        return data

    def load(self, period_id: str) -> dict | None:
        path = self.manifest_path(period_id)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f'weekly manifest 无法读取: {path}: {exc}') from exc

    def save(self, period_id: str, data: dict) -> Path:
        path = self.manifest_path(period_id)
        atomic_json(path, data)
        return path

    @contextmanager
    def lock(self, period_id: str):
        lock_dir = self.root / '.locks'
        lock_dir.mkdir(parents=True, exist_ok=True)
        path = lock_dir / f'{safe_period_id(period_id)}.lock'
        handle = open(path, 'a+b')
        locked = False
        try:
            if handle.seek(0, os.SEEK_END) == 0:
                handle.write(b'0'); handle.flush()
            handle.seek(0)
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except OSError as exc:
                raise RuntimeError(f'该周期已有初始化任务运行中: {period_id}') from exc
            yield
        finally:
            if locked:
                handle.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()  # OS releases locks after a crash; marker files are harmless.


def assert_result_write_target(manifest: dict, spreadsheet_token: str) -> None:
    """阻止登记表、原表和完整快照成为业务写入目标。"""
    protected = {
        str((manifest.get('registry') or {}).get('spreadsheet_token') or ''),
        str((manifest.get('source') or {}).get('spreadsheet_token') or ''),
        str((manifest.get('snapshot') or {}).get('spreadsheet_token') or ''),
    }
    expected = str((manifest.get('result') or {}).get('spreadsheet_token') or '')
    if not spreadsheet_token or spreadsheet_token in protected or spreadsheet_token != expected:
        raise RuntimeError('写入目标不是 manifest 登记的独立结果 Spreadsheet')


def require_business_ready(store: WeeklyAssetStore, period_id: str) -> dict:
    """普通每日任务门禁：资源及业务映射均 ready 才允许继续。"""
    manifest = store.load(period_id)
    if not manifest:
        raise RuntimeError(f'周期 {period_id} 尚未初始化，请先运行 --new-week --confirm')
    if manifest.get('status') != 'ready':
        raise RuntimeError(f'周期 {period_id} 初始化未完成，禁止启动每日任务')
    if not manifest.get('business_ready'):
        raise RuntimeError(f'周期 {period_id} 尚未完成结果子表映射（R1.5），禁止启动 Amazon')
    return manifest


def _find_exact(files: list[dict], name: str) -> dict | None:
    matches = [item for item in files
               if item.get('name') == name and item.get('type') == 'sheet' and item.get('token')]
    if len(matches) > 1:
        raise RuntimeError(f'Drive 根目录存在多个同名资源，停止自动选择: {name}')
    return matches[0] if matches else None


def structure_shape(structure: dict) -> dict:
    """Return the stable workbook shape used for copy-integrity checks.

    ``spreadsheet_structure`` also contains an A1:P10 sample and a content
    hash.  Those cells include formulas, timestamps and operational notes that
    may legitimately change while a Drive copy is being materialised.  They
    are valuable audit evidence, but are not a reliable definition of whether
    the copied workbook has the same shape.  Copy validation therefore uses
    only the ordered *business* tabs and their grid capacity.  Auxiliary tabs
    may be added, removed, or reordered by the weekly workbook owner; they are
    not a price-capture input and must not make an otherwise complete copy
    fail.  Business headers and Marketplace are validated by
    ``weekly_mapping`` before any fetch.
    """
    all_sheets = list((structure or {}).get('sheets') or [])

    def is_business(item: dict) -> bool:
        title = str(item.get('title') or '').strip()
        # Prefixes are the contractual business-tab routes.  Descriptive new
        # tabs are included when their probe contains the complete source
        # schema; discovery will subsequently require explicit US/CA evidence.
        if re.match(r'^(?:PD|XD|PDF|CPD)', title, re.IGNORECASE):
            return True
        sample = item.get('sample') or []
        try:
            from weekly_mapping import find_asin_header, source_schema
            located = find_asin_header(sample)
            if not located:
                return False
            header = sample[located[0] - 1]
            return bool(source_schema(header).get('complete'))
        except (TypeError, IndexError):
            # A malformed auxiliary sample is not allowed to weaken the copy
            # gate; the complete source discovery step remains fail-closed.
            return False

    business = []
    auxiliary_titles = []
    for item in all_sheets:
        entry = {
            'title': str(item.get('title') or ''),
            # Absolute Sheet index is intentionally omitted: inserting or
            # reordering auxiliary tabs must not look like business drift.
            'row_count': item.get('row_count'),
            'column_count': item.get('column_count'),
        }
        if is_business(item):
            business.append(entry)
        else:
            auxiliary_titles.append(entry['title'])
    return {
        # Retain total counts for audit, while comparison below uses only the
        # business list. Existing manifest readers can continue to inspect
        # ``sheets`` without learning a second shape format.
        'sheet_count': len(all_sheets),
        'business_sheet_count': len(business),
        'auxiliary_sheet_count': len(auxiliary_titles),
        'auxiliary_titles': auxiliary_titles,
        'sheets': business,
    }


def structure_shape_sha256(structure: dict) -> str:
    shape = structure_shape(structure)
    # The digest is intentionally limited to the compared business shape.  If
    # an auxiliary tab is added/reordered, the audit fields below still show
    # that fact, but the stable-shape fingerprint must not change or appear as
    # a false copy-integrity drift.
    canonical = json.dumps({
        'business_sheet_count': shape.get('business_sheet_count',
                                          len(shape.get('sheets') or [])),
        'sheets': shape.get('sheets') or [],
    }, ensure_ascii=False,
                            sort_keys=True, separators=(',', ':'), default=str)
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def compare_structure_shape(expected: dict, actual: dict) -> dict:
    """Compare stable workbook metadata and return an auditable diff.

    The comparison is positional only within the business-tab list: source
    order is retained for result-sheet creation, so a copied workbook that
    drops/reorders business tabs or changes their grid capacity is unsafe.
    Auxiliary tabs are intentionally excluded from this gate, allowing the
    workbook owner to add or reorder helper tabs without blocking a copy.  A
    changed formula/value sample does not fail this check; the later dynamic
    discovery step remains responsible for required business headers and
    Marketplace routing.
    """
    left = structure_shape(expected)
    right = structure_shape(actual)
    differences = []
    left_count = left.get('business_sheet_count', len(left.get('sheets') or []))
    right_count = right.get('business_sheet_count', len(right.get('sheets') or []))
    if left_count != right_count:
        differences.append(
            f"business_sheet_count {left_count} != {right_count}")
    for pos in range(max(len(left.get('sheets') or []),
                         len(right.get('sheets') or []))):
        before = left['sheets'][pos] if pos < len(left.get('sheets') or []) else None
        after = right['sheets'][pos] if pos < len(right.get('sheets') or []) else None
        if before != after:
            differences.append(
                f"sheet[{pos}] {before!r} != {after!r}")
    expected_content = (expected or {}).get('sha256') or ''
    actual_content = (actual or {}).get('sha256') or ''
    return {
        'compatible': not differences,
        'differences': differences,
        'expected_shape_sha256': structure_shape_sha256(expected),
        'actual_shape_sha256': structure_shape_sha256(actual),
        'expected_content_sha256': expected_content,
        'actual_content_sha256': actual_content,
        'content_equal': bool(expected_content and actual_content and
                              expected_content == actual_content),
    }


def initialize_weekly_assets(fc, store: WeeklyAssetStore, selection, registry_info: dict,
                             recreate: bool = False,
                             manager_open_id: str = '', snapshot_run_id: str = '') -> tuple[dict, bool]:
    """创建/复用本周快照和独立结果表；返回 (manifest, reused)。"""
    period_id = safe_period_id(selection.period_id)
    with store.lock(period_id):
        old = store.load(period_id)
        fixed = store.fixed_result()
        if fixed and old and (old.get('result') or {}).get('spreadsheet_token') not in (None, '', fixed['spreadsheet_token']):
            raise RuntimeError('周期结果表与固定结果表不一致，禁止静默切换')
        if not manager_open_id:
            raise RuntimeError('缺少 feishu_manager_open_id，禁止使用应用所有的周资源')
        if old and old.get('status') == 'ready' and not recreate:
            if 'business_ready' not in old:
                old['business_ready'] = False
                store.save(period_id, old)
            if (old.get('source') or {}).get('url') != selection.source_url:
                raise RuntimeError('同一 period 的登记表源链接已变化，禁止静默复用')
            fc.wait_spreadsheet_structure(old['snapshot']['spreadsheet_token'], attempts=3)
            fc.query_sheets(old['result']['spreadsheet_token'])
            snapshot_access = fc.ensure_permission_member(
                old['snapshot']['spreadsheet_token'], 'sheet', manager_open_id)
            result_access = fc.ensure_permission_member(
                old['result']['spreadsheet_token'], 'sheet', manager_open_id)
            old['human_manager'] = {
                'member_type': 'openid', 'member_id': manager_open_id,
                'perm': 'full_access', 'snapshot': snapshot_access,
                'result': result_access, 'verified_at': datetime.now().isoformat(),
            }
            store.save(period_id, old)
            return old, True

        resuming = bool(old and old.get('status') == 'initializing' and not recreate)
        if resuming and old['source']['url'] != selection.source_url:
            raise RuntimeError('初始化中的周期源链接发生变化，禁止静默切换')
        generation = int((old or {}).get('generation') or 0) + (0 if resuming else 1)
        suffix = '' if generation == 1 else f'_v{generation}'
        day = datetime.now().strftime('%Y%m%d')
        snapshot_name = f'Amazon周报_{period_id}_完整快照_{day}{suffix}'
        if snapshot_run_id:
            snapshot_name = f'Amazon周报_{period_id}_完整快照_{safe_period_id(snapshot_run_id)}{suffix}'
        result_name = f'Amazon价格校验_{period_id}_结果表_{day}{suffix}'
        if resuming:
            snapshot_name = (old.get('resource_names') or {}).get('snapshot') or (old.get('snapshot') or {}).get('name') or snapshot_name
            result_name = (old.get('resource_names') or {}).get('result') or (old.get('result') or {}).get('name') or result_name
        resource_type, direct_token = validate_feishu_resource_url(
            selection.source_url, fc.cfg['feishu_allowed_hosts'])
        if resource_type == 'wiki':
            source_token, source_type = fc.resolve_wiki_obj(selection.source_url)
        else:
            source_token, source_type = direct_token, 'sheet'
        if source_type != 'sheet':
            raise RuntimeError(f'周报底层必须是 sheet，当前为 {source_type}')
        if fixed and fixed['spreadsheet_token'] in (source_token, registry_info.get('spreadsheet_token')):
            raise RuntimeError('固定结果表不能是原始周报或登记表')
        # Always observe the current source, including an interrupted
        # initialization.  The source may have received formula/helper-column
        # updates after the copy request; the immutable snapshot remains the
        # input for the batch, while this observation is kept for audit.
        source_structure = fc.spreadsheet_structure(source_token)
        files = fc.list_root_files(page_size=200)

        history = list((old or {}).get('history') or [])
        if old and not resuming:
            history.append({
                'generation': old.get('generation'), 'snapshot': old.get('snapshot'),
                'result': old.get('result'), 'replaced_at': datetime.now().isoformat(),
            })
        manifest = {
            'schema_version': 1, 'period_id': period_id, 'generation': generation,
            'snapshot_run_id': snapshot_run_id,
            'status': 'initializing', 'created_at': datetime.now().isoformat(),
            'registry': {
                'url': registry_info.get('url') or '',
                'spreadsheet_token': registry_info.get('spreadsheet_token') or '',
                'sheet_id': registry_info.get('sheet_id') or '',
                'row_number': selection.row_number,
            },
            'source': {'url': selection.source_url, 'spreadsheet_token': source_token,
                       'type': source_type, 'structure_sha256': source_structure['sha256'],
                       'structure_shape_sha256': structure_shape_sha256(source_structure)},
            'snapshot': {}, 'result': {}, 'history': history, 'business_ready': False,
            'resource_names': {'snapshot': snapshot_name, 'result': result_name},
            'source_structure': source_structure,
            'access_policy': {'registry': 'readonly', 'source': 'readonly',
                              'snapshot': 'readonly', 'result': 'readwrite'},
        }
        if resuming:
            manifest = old
            # Keep the original source fingerprint for recovery provenance, but
            # record the latest read separately so a live formula recalculation
            # cannot make the interrupted batch fail on resume.
            manifest['source_structure_current'] = source_structure
            manifest.setdefault('source', {})['structure_sha256_current'] = source_structure['sha256']
            manifest.setdefault('source', {})['structure_shape_sha256_current'] = \
                structure_shape_sha256(source_structure)
        store.save(period_id, manifest)

        saved_snapshot = manifest.get('snapshot') or {}
        snapshot = ({'token': saved_snapshot['spreadsheet_token'], 'url': saved_snapshot.get('url', '')}
                    if saved_snapshot.get('spreadsheet_token') else _find_exact(files, snapshot_name))
        if not snapshot:
            snapshot = fc.copy_file(source_token, 'sheet', snapshot_name, '')
        snapshot_token = snapshot['token']
        manifest['snapshot'] = {
            'name': snapshot_name, 'spreadsheet_token': snapshot_token,
            'url': snapshot.get('url') or '', 'status': 'pending_validation',
        }
        store.save(period_id, manifest)
        snapshot_structure = fc.wait_spreadsheet_structure(snapshot_token)
        structure_check = compare_structure_shape(source_structure, snapshot_structure)
        if not structure_check['compatible']:
            detail = '；'.join(structure_check['differences'][:3])
            raise RuntimeError(f'正式快照与原周报结构不一致（稳定结构）：{detail}')
        manifest['snapshot']['structure_sha256'] = snapshot_structure['sha256']
        manifest['snapshot']['structure_shape_sha256'] = \
            structure_check['actual_shape_sha256']
        manifest['snapshot']['copy_integrity'] = structure_check
        manifest['snapshot']['status'] = 'ready'
        snapshot_access = fc.ensure_permission_member(
            snapshot_token, 'sheet', manager_open_id)
        store.save(period_id, manifest)

        saved_result = manifest.get('result') or {}
        result = ({'token': fixed['spreadsheet_token'], 'url': fixed['url']} if fixed else
                  {'token': saved_result['spreadsheet_token'], 'url': saved_result.get('url', '')}
                  if saved_result.get('spreadsheet_token') else _find_exact(files, result_name))
        if fixed:
            result_name = fixed.get('name') or 'Amazon 周报前端价格捕捉任务'
        if not result:
            created = fc.create_spreadsheet(result_name, '')
            result = {'token': created['spreadsheet_token'],
                      'url': created.get('url') or '', 'name': result_name}
        result_token = result['token']
        manifest['result'] = {'name': result_name, 'spreadsheet_token': result_token,
                              'url': result.get('url') or '', 'status': 'pending_validation'}
        store.save(period_id, manifest)
        result_sheets = fc.query_sheets(result_token)
        manifest['result'] = {
            'name': result_name, 'spreadsheet_token': result_token,
            'url': result.get('url') or '', 'status': 'ready',
            'initial_sheet_ids': [s.get('sheet_id') for s in result_sheets if s.get('sheet_id')],
        }
        manifest['status'] = 'ready'
        manifest['validated_at'] = datetime.now().isoformat()
        # The fixed result spreadsheet is still an application-owned delivery
        # resource.  It may predate the automatic-grant rule, so reusing it
        # must also enforce and read back the manager permission instead of
        # treating its existing state as proof of access.
        result_access = fc.ensure_permission_member(
            result_token, 'sheet', manager_open_id)
        if fixed:
            result_access['fixed_result'] = True
        manifest['human_manager'] = {
            'member_type': 'openid', 'member_id': manager_open_id,
            'perm': 'full_access', 'snapshot': snapshot_access,
            'result': result_access, 'verified_at': datetime.now().isoformat(),
        }
        assert_result_write_target(manifest, result_token)
        store.save(period_id, manifest)
        return manifest, False
