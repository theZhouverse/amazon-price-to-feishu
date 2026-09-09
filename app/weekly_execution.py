"""Price-only weekly preparation and durable delivery helpers."""
from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from weekly_assets import initialize_weekly_assets, require_business_ready, safe_period_id
from weekly_mapping import build_discovery, validate_discovery
from weekly_result import sync_weekly_result_base
from product_links import audit_manifest_links
from runtime_state import atomic_json
from publication_guard import claim_latest_run, assert_latest_run


def price_only_config(cfg):
    return {**cfg, 'html_archive_enabled': False, 'html_archive_required': False,
            'html_server_enabled': False, '_html_server_base_url': ''}


def ensure_price_week(fc, store, selection, registry, cfg, *, allow_create,
                      run_id=None, resume=False):
    """Prepare a new source snapshot; keep the fixed result untouched until delivery."""
    with store.lock('fixed-result-prepare'):
        if run_id is not None:
            return _prepare_price_run(fc, store, selection, registry, cfg,
                                      run_id, allow_create, resume)
        manifest = store.load(selection.period_id)
        fixed = store.fixed_result()
        if manifest and (manifest.get('source') or {}).get('url') != selection.source_url:
            raise RuntimeError('同一周期源链接发生变化，请在登记表新增序号，禁止覆盖旧周报')
        if manifest and manifest.get('business_ready') and manifest.get('status') == 'ready':
            if fixed and (manifest['result']['spreadsheet_token'] != fixed['spreadsheet_token']
                          or fixed.get('period_id') not in (None, selection.period_id)):
                raise RuntimeError('固定结果表已属于其他周期，禁止用旧快照覆盖')
            return require_business_ready(store, selection.period_id)
        if not allow_create:
            return require_business_ready(store, selection.period_id)
        if not fixed:
            raise RuntimeError('尚未登记固定结果表，禁止自动新建结果表')
        if not manifest or manifest.get('status') != 'ready':
            manifest, _ = initialize_weekly_assets(
                fc, store, selection, {**registry, 'url': cfg['weekly_registry_url']},
                manager_open_id=cfg.get('feishu_manager_open_id', ''))
        if not manifest.get('mapping_ready'):
            discovery = build_discovery(fc, manifest['snapshot']['spreadsheet_token'])
            validate_discovery(discovery)
            manifest['sheet_mappings'] = [s for s in discovery['sheets']
                                         if s['status'] in ('mapped', 'mapped_empty')]
            manifest['mapping_ready'] = True
            manifest['business_ready'] = False
            store.save(selection.period_id, manifest)
        audit = audit_manifest_links(fc, manifest)
        atomic_json(store.root / selection.period_id / 'link_audit.json', audit)
        if audit['invalid_count']:
            raise RuntimeError('新周商品链接审计失败，旧结果表保留；请检查link_audit.json')
        manifest['base_sync_pending'] = True
        store.save(selection.period_id, manifest)
        return manifest  # Fetch first; publish new A:G and results together afterwards.


def _prepare_price_run(fc, store, selection, registry, cfg, run_id, allow_create, resume):
    """Freeze fresh input per new run, never refresh an explicit recovery's input."""
    safe_period_id(run_id)
    old = store.load(selection.period_id)
    fixed = store.fixed_result()
    if not fixed:
        raise RuntimeError('尚未登记固定结果表，禁止自动新建结果表')
    if old and (old.get('source') or {}).get('url') != selection.source_url:
        raise RuntimeError('同一周期源链接发生变化，请在登记表新增序号')
    if old and old['result'].get('spreadsheet_token') not in (None, fixed['spreadsheet_token']):
        raise RuntimeError('周期结果表与固定结果表不一致')
    if resume and (not old or old.get('snapshot_run_id') != run_id):
        raise RuntimeError('只能恢复当前登记批次；禁止旧批次覆盖最新基础数据')
    if resume and allow_create and old.get('mapping_ready'):
        # A run can fail after its manifest/snapshot is ready but before the
        # latest-run claim (for example during discovery).  If the current
        # manifest explicitly carries this same snapshot_run_id, it is the
        # recoverable batch—not an older batch—and may claim the pointer now.
        # Any other run_id still has to pass the strict identity guard.
        if old.get('snapshot_run_id') == run_id:
            claim_latest_run(store, old, run_id)
        else:
            assert_latest_run(store, old, run_id)
    if resume and not allow_create and old.get('status') != 'ready':
        raise RuntimeError('只读模式不能续作云端快照初始化')
    if not allow_create and not resume:
        # A newly prepared weekly snapshot is already the safe read-only
        # source for a frontend sample run, even before A:G has been published
        # to the fixed result table. Do not fall back to the mutable registry
        # source in this case: field matching and Amazon identity checks must
        # exercise the frozen copy requested by the operator.
        if (old and old.get('status') == 'ready' and old.get('mapping_ready')
                and old.get('link_rules_ready')
                and (old.get('snapshot') or {}).get('spreadsheet_token')):
            manifest = deepcopy(old)
            manifest['snapshot_run_id'] = run_id
            manifest['readonly_preview'] = True
            return manifest
        # Dry/fetch-only observes live source without making a cloud copy.
        from weekly_registry import validate_feishu_resource_url
        kind, token = validate_feishu_resource_url(selection.source_url, cfg['feishu_allowed_hosts'])
        if kind == 'wiki':
            token, kind = fc.resolve_wiki_obj(selection.source_url)
            if kind != 'sheet':
                raise RuntimeError('周报底层必须为sheet')
        if token == fixed['spreadsheet_token']:
            raise RuntimeError('原表不能是固定结果表')
        manifest = {'period_id': selection.period_id, 'source': {'url': selection.source_url},
                    'snapshot': {'spreadsheet_token': token}, 'result': deepcopy(fixed),
                    'snapshot_run_id': run_id, 'readonly_preview': True}
    else:
        same_run = bool(old and old.get('snapshot_run_id') == run_id)
        if old and not same_run:
            # Full prior manifest kept for audit; it must not become current again.
            prior_id = safe_period_id(old.get('snapshot_run_id') or f"generation-{old.get('generation', 0)}")
            atomic_json(store.root / selection.period_id / 'runs' / f'{prior_id}.json', old)
        if not same_run or old.get('status') != 'ready':
            manifest, _ = initialize_weekly_assets(
                fc, store, selection, {**registry, 'url': cfg['weekly_registry_url']},
                recreate=bool(old and not same_run),
                manager_open_id=cfg.get('feishu_manager_open_id', ''),
                snapshot_run_id=run_id)
        else:
            manifest = old
        if manifest.get('mapping_ready'):
            if allow_create:
                claim_latest_run(store, manifest, run_id)
            return manifest
    discovery = build_discovery(fc, manifest['snapshot']['spreadsheet_token'])
    validate_discovery(discovery)
    manifest['sheet_mappings'] = [item for item in discovery['sheets']
                                 if item['status'] in ('mapped', 'mapped_empty')]
    audit = audit_manifest_links(fc, manifest)
    atomic_json(store.root / selection.period_id / 'runs' / f'{run_id}_link_audit.json', audit)
    if audit['invalid_count']:
        raise RuntimeError('本批商品链接审计失败，固定结果表未修改')
    manifest.update(mapping_ready=True, business_ready=False, base_sync_pending=True)
    if allow_create:
        store.save(selection.period_id, manifest)
        claim_latest_run(store, manifest, run_id)
    return manifest
