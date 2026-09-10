# -*- coding: utf-8 -*-
"""每周飞书资源生命周期：幂等初始化、manifest、排他锁与只读保护。"""
from __future__ import annotations

import json
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
        source_structure = (old.get('source_structure') if resuming else None) or fc.spreadsheet_structure(source_token)
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
                       'type': source_type, 'structure_sha256': source_structure['sha256']},
            'snapshot': {}, 'result': {}, 'history': history, 'business_ready': False,
            'resource_names': {'snapshot': snapshot_name, 'result': result_name},
            'source_structure': source_structure,
            'access_policy': {'registry': 'readonly', 'source': 'readonly',
                              'snapshot': 'readonly', 'result': 'readwrite'},
        }
        if resuming:
            manifest = old
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
        if snapshot_structure['sheets'] != source_structure['sheets']:
            raise RuntimeError('正式快照与原周报结构不一致')
        manifest['snapshot']['structure_sha256'] = snapshot_structure['sha256']
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
