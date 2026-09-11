# -*- coding: utf-8 -*-
"""config.py — 配置加载与校验。

非敏感配置：代码默认值 → config/config.json。
敏感配置：项目根目录 .env → 系统环境变量（系统环境变量优先）。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
DATA_DIR = PROJECT_ROOT / 'data'
OUTPUT_DIR = PROJECT_ROOT / 'outputs'
CACHE_ROOT = OUTPUT_DIR / 'fetch_cache'
SNAPSHOT_DIR = OUTPUT_DIR / 'snapshots'
CSV_DIR = OUTPUT_DIR / 'csv'
LOG_DIR = OUTPUT_DIR / 'logs'
DEBUG_DIR = OUTPUT_DIR / 'debug'

# 默认配置（代码兜底）
DEFAULTS = {
    # 抓取
    'workers': 4,                     # 正式全量：单浏览器四个独立 Tab
    'post_archive_delay_min': 1.0,    # 商品完成后等待；价格/可选HTML共用，不重复等待
    'post_archive_delay_max': 3.0,
    'risk_cooldown_min': 60.0,
    'risk_cooldown_max': 180.0,
    'page_timeout': 30,               # 整个页面加载超时(秒)
    'price_wait_timeout': 12,         # 等待价格元素出现(秒)
    'per_asin_timeout': 90,           # 单个 ASIN 总超时(秒)
    'retry': 2,                       # 技术失败重试次数
    'incomplete_page_circuit_threshold': 8,  # 残缺商品页连续达阈值后中断当表
    'save_every': 10,                 # 每 N 条原子保存一次缓存
    'us_zip': '90210',                # 仅兼容旧配置；proxy 模式下不会使用
    'us_location_mode': 'proxy',     # US: proxy=依赖出口IP；postal=兼容旧方案
    # CA 默认不再打开地址弹窗或注入邮编；直接读取当前 amazon.ca 页面价格。
    # postal 仍保留为经过明确验证的兼容模式，proxy 依赖加拿大代理出口。
    'ca_location_mode': 'direct_no_postal',
    'ca_postal': 'M5V 3A8',
    'proxy': '',                      # 显式浏览器代理；留空使用固定 VPN，不自动读取系统代理
    # 浏览器启动兼容性：Chrome 136+/152 + DrissionPage 4.1.1.4 需要独立
    # CDP 端口；当前 Windows 主机必须启用 no-sandbox/disable-gpu 才能稳定启动。
    'browser_auto_port': True,
    'browser_no_sandbox': True,
    'browser_disable_gpu': True,
    # HTML 归档（独立于代码产物，默认位于项目同级约定目录）
    'html_archive_root': str(PROJECT_ROOT / 'htmls'),
    'html_retention_days': 5,
    'html_min_free_gb': 40.0,
    'html_archive_required': False,
    'html_archive_enabled': False,
    'html_server_enabled': False,
    'html_server_bind': '0.0.0.0',
    'html_server_port': 8765,
    # 新建/复制任意飞书云端资源后，统一给配置的周成业授予管理权限；
    # 权限写入后必须回读确认，历史资源不在此策略的自动扫描范围内。
    'feishu_auto_grant_generated_resources': True,
    'feishu_manager_open_id': '',
    'feishu_generated_resource_member_type': 'openid',
    'feishu_generated_resource_perm': 'full_access',
    # 计算
    'price_tolerance': '0.50',        # 一致性容差(USD, Decimal 字符串)
    'ambiguous_price_ratio': '0.05',  # 多候选冲突阈值
    # 缓存
    'cache_max_age_hours': 12,        # 缓存有效期
    'parser_rule_version': '2026-08-26-v4',
    # 飞书
    'feishu_source_wiki': 'https://wit0jhu6kvu.feishu.cn/wiki/O1t1wJVlHiEbd1kLdj6cIr0KnIg',
    'feishu_target_wiki': 'https://wit0jhu6kvu.feishu.cn/wiki/JbiQwDZXeiJan0k8ydRczfWNnFc',
    'weekly_registry_url': 'https://wit0jhu6kvu.feishu.cn/wiki/HwxpwCnZ7iV1o5klIGbc8wJHnrd',
    'weekly_registry_sheet_id': 'c1fcd1',  # 2026-08-24 R1.1 只读发现：Sheet1
    'weekly_registry_max_age_days': 8.0,
    'feishu_allowed_hosts': ['wit0jhu6kvu.feishu.cn'],
    'feishu_app_id': 'cli_aa097133e3355ccd',
    'feishu_app_secret': '',          # 仅由根目录 .env 或环境变量 FS_APP_SECRET 注入
    'feishu_output_start_col': 8,     # 紧凑目标表 H 列(1-based)
    'feishu_header_row': 2,           # 目标表表头固定第 2 行
    'feishu_output_headers': [
        '展示价格', '折扣类型', '折扣值', '最终价格', '价格一致性', '时间戳',
    ],
    # Seller Central Feedback；默认关闭，未登记真实后台会话和固定子表前不得启用
    'feedback': {
        'enabled': False,
        'timezone': 'Asia/Shanghai',
        'initial_window_days': 7,
        'incremental_window_days': 3,
        'retention_days': 10,
        'max_rating': 3,
        'target_spreadsheet_token': '',
        'target_sheet_id': '',
        'target_sheet_name': 'Feedback差评汇总',
        'state_path': str(OUTPUT_DIR / 'feedback' / 'feedback_state.json'),
        'evidence_root': str(OUTPUT_DIR / 'feedback'),
        'store_order': ['store_a', 'store_b'],
        'page_wait_min': 8.0,
        'page_wait_max': 12.0,
        'detail_wait_min': 8.0,
        'detail_wait_max': 12.0,
        'max_pages': 50,
        'stores': [],
    },
    # 历史/人工指定子表示例；正式周报运行按快照元数据动态发现，不受此列表限制。
    'sheets': ['PD03', 'PD17', 'PD05', 'PD25', 'XD03', 'XD17', 'PD52', 'PD39', 'PD33',
               'PDF075', 'PD63', 'CPD03', 'CPD17', 'CPD05', 'CPD25', 'CPD39', 'CPD33',
               'CPD52'],
    'sheet_profiles': {
        'PD03': 'US', 'PD17': 'US', 'PD05': 'US', 'PD25': 'US',
        'XD03': 'US', 'XD17': 'US', 'PD52': 'US', 'PD39': 'US',
        'PD33': 'US', 'PDF075': 'US', 'PD63': 'US',
        'CPD03': 'CA', 'CPD17': 'CA', 'CPD05': 'CA', 'CPD25': 'CA',
        'CPD39': 'CA', 'CPD33': 'CA', 'CPD52': 'CA',
    },
    # 历史布局列位置(1-based)，仅供布局检查等非正式解析兜底；生产源读取按表头。
    'source_cols': {
        'asin': 1, 'sku': 2, 'size': 4, 'normal_price': 5,
        'h_type': 8, 'i_value': 9, 'target_price': 11,
    },
    # 汇总与告警
    'max_error_ratio_for_push': 0.10, # 技术异常占比超阈值不推送
    'max_target_fallback_ratio_for_push': 0.10,  # 目标价缺失(missing)占比超阈值不推送；上传 xlsx 本地兜底属常态不阻止
    'log_keep': 30,
    # 六列输出布局（P0-2.2）：日常优先读 outputs/migration_record.json，这里仅兜底
    'feishu_output_layout': {
        'default_start_col': None,
        'sheet_start_cols': {},
    },
}

# 数值字段类型检查
_NUM_FIELDS = {
    'workers': (int, 1, 16),
    'post_archive_delay_min': (float, 0, 60),
    'post_archive_delay_max': (float, 0, 120),
    'risk_cooldown_min': (float, 1, 600),
    'risk_cooldown_max': (float, 1, 900),
    'page_timeout': (float, 5, 180),
    'price_wait_timeout': (float, 1, 60),
    'per_asin_timeout': (float, 10, 600),
    'retry': (int, 0, 5),
    'save_every': (int, 1, 1000),
    'price_tolerance': (str, '0', '10'),
    'ambiguous_price_ratio': (str, '0', '1'),
    'cache_max_age_hours': (float, 0.5, 720),
    'weekly_registry_max_age_days': (float, 1, 31),
    'max_error_ratio_for_push': (float, 0, 1),
    'max_target_fallback_ratio_for_push': (float, 0, 1),
    'log_keep': (int, 1, 365),
    'html_retention_days': (int, 1, 30),
    'html_min_free_gb': (float, 0, 10000),
    'html_server_port': (int, 1, 65535),
}


def load_config(config_path: Path | None = None) -> dict:
    """加载唯一职责配置源；启动即校验，错误直接抛。"""
    cfg = dict(DEFAULTS)
    path = Path(config_path) if config_path else PROJECT_ROOT / 'config' / 'config.json'
    if path.exists():
        try:
            with open(path, encoding='utf-8') as f:
                user = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            raise RuntimeError(f'config.json 解析失败: {e}')
        if not isinstance(user, dict):
            raise RuntimeError('config.json 必须是 JSON 对象')
        if 'feishu_app_secret' in user:
            raise RuntimeError(
                'config.json 禁止配置 feishu_app_secret；请使用项目根目录 .env '
                '或系统环境变量 FS_APP_SECRET'
            )
        cfg.update({k: v for k, v in user.items() if v is not None})

    # The Windows host may keep one checked-in config file while changing only
    # machine-local paths and feature switches at runtime. Keep this mapping
    # deliberately small and explicit: arbitrary environment-to-config
    # mapping would make production behavior difficult to audit.
    env_overrides = {
        'html_archive_root': 'AMAZON_HTML_ARCHIVE_ROOT',
        'html_server_bind': 'AMAZON_HTML_SERVER_BIND',
        'html_server_port': 'AMAZON_HTML_SERVER_PORT',
        'html_archive_enabled': 'AMAZON_HTML_ARCHIVE_ENABLED',
        'html_archive_required': 'AMAZON_HTML_ARCHIVE_REQUIRED',
        'html_server_enabled': 'AMAZON_HTML_SERVER_ENABLED',
        'workers': 'AMAZON_WORKERS',
        # Amazon浏览器代理必须显式声明；不自动继承通用HTTP_PROXY，避免
        # 调度环境因其他工具的代理变量而悄然切换出口。
        'proxy': 'AMAZON_PROXY',
        # CA 无邮编策略可由计划任务或临时单点显式覆盖；不把它隐含在
        # 通用代理变量中，避免迁移机器时悄然改变价格口径。
        'us_location_mode': 'AMAZON_US_LOCATION_MODE',
        'ca_location_mode': 'AMAZON_CA_LOCATION_MODE',
        # 允许本机计划任务按运行环境明确开关 Feedback，避免复制一份
        # 容易漂移的 config.json；宿主机生产仍须使用经过验收的紫鸟会话。
        'feedback_enabled': 'AMAZON_FEEDBACK_ENABLED',
    }
    for key, env_name in env_overrides.items():
        raw = os.environ.get(env_name)
        if raw is None or not raw.strip():
            continue
        if key in {'html_archive_enabled', 'html_archive_required', 'html_server_enabled',
                   'feedback_enabled'}:
            lowered = raw.strip().lower()
            if lowered not in {'1', '0', 'true', 'false', 'yes', 'no', 'on', 'off'}:
                raise RuntimeError(f'环境变量 {env_name} 必须是布尔值')
            value = lowered in {'1', 'true', 'yes', 'on'}
            if key == 'feedback_enabled':
                cfg.setdefault('feedback', {})['enabled'] = value
            else:
                cfg[key] = value
        elif key in {'html_server_port', 'workers'}:
            try:
                cfg[key] = int(raw.strip())
            except ValueError as exc:
                raise RuntimeError(f'环境变量 {env_name} 必须是整数') from exc
        else:
            cfg[key] = raw.strip()

    # Secret 只允许来自根目录 .env 或系统环境变量；系统环境变量优先。
    envf = PROJECT_ROOT / '.env'
    file_secret = ''
    if envf.is_file():
        try:
            for line in envf.read_text(encoding='utf-8').splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith('#'):
                    continue
                if stripped.startswith('FS_APP_SECRET='):
                    file_secret = stripped.split('=', 1)[1].strip()
                    break
        except OSError:
            pass
    elif envf.is_dir():
        # 兼容该项目既有的本地凭证目录；不扫描目录，也不接受多个候选文件。
        legacy = envf / '飞书凭证.txt'
        if legacy.is_file():
            try:
                lines = [line.strip() for line in legacy.read_text(encoding='utf-8').splitlines()
                         if line.strip()]
            except OSError as exc:
                raise RuntimeError(f'无法读取本地飞书凭证文件: {exc}') from exc
            if len(lines) != 2:
                raise RuntimeError('本地飞书凭证文件必须恰好包含 App ID 和 Secret 两个非空行')
            if lines[0] != str(cfg.get('feishu_app_id') or ''):
                raise RuntimeError('本地飞书凭证 App ID 与 config.json 不一致，禁止混用')
            file_secret = lines[1]
    cfg['feishu_app_secret'] = os.environ.get('FS_APP_SECRET', '').strip() or file_secret

    validate(cfg)
    return cfg


def validate(cfg: dict) -> None:
    """校验数值类型与范围，错误时启动阶段报错。"""
    for key, (typ, lo, hi) in _NUM_FIELDS.items():
        v = cfg.get(key)
        if v is None or v == '':
            raise RuntimeError(f'配置项 {key} 不能为空')
        try:
            if typ is int:
                n = int(v)
            else:
                n = float(v)
        except (ValueError, TypeError):
            raise RuntimeError(f'配置项 {key} 必须是数字，当前值: {v!r}')
        if typ is int:
            if not (int(lo) <= n <= int(hi)):
                raise RuntimeError(f'配置项 {key} 超出范围 [{lo}, {hi}]，当前值: {n}')
        else:
            lo_f, hi_f = float(lo), float(hi)
            if not (lo_f <= n <= hi_f):
                raise RuntimeError(f'配置项 {key} 超出范围 [{lo}, {hi}]，当前值: {n}')

    # Decimal 字符串必须可解析
    from decimal import Decimal, InvalidOperation
    for key in ('price_tolerance', 'ambiguous_price_ratio'):
        try:
            number = Decimal(str(cfg[key]))
            if not number.is_finite() or number < 0:
                raise RuntimeError(f'配置项 {key} 必须是有限非负数')
        except InvalidOperation:
            raise RuntimeError(f'配置项 {key} 不是合法的小数: {cfg[key]!r}')

    if cfg['post_archive_delay_max'] < cfg['post_archive_delay_min']:
        raise RuntimeError('post_archive_delay_max 不能小于 post_archive_delay_min')
    if cfg['risk_cooldown_max'] < cfg['risk_cooldown_min']:
        raise RuntimeError('risk_cooldown_max 不能小于 risk_cooldown_min')
    if cfg['per_asin_timeout'] < cfg['page_timeout']:
        raise RuntimeError('per_asin_timeout 不能小于 page_timeout')
    for key in ('us_location_mode', 'ca_location_mode'):
        allowed_modes = {'proxy', 'postal'}
        if key == 'ca_location_mode':
            allowed_modes.add('direct_no_postal')
        mode = str(cfg.get(key) or '').strip().lower()
        if mode not in allowed_modes:
            raise RuntimeError(
                f'{key} 只能是 {"、".join(sorted(allowed_modes))}')
    if not cfg['feishu_app_id']:
        raise RuntimeError('feishu_app_id 不能为空')
    if not isinstance(cfg.get('feishu_allowed_hosts'), list) or not cfg['feishu_allowed_hosts']:
        raise RuntimeError('feishu_allowed_hosts 必须是非空数组')
    profiles = cfg.get('sheet_profiles')
    if not isinstance(profiles, dict) or set(profiles) != set(cfg.get('sheets') or []):
        raise RuntimeError('sheet_profiles 必须与 sheets 一一对应')
    if any(value not in ('US', 'CA') for value in profiles.values()):
        raise RuntimeError('sheet_profiles Marketplace 只允许 US 或 CA')
    from weekly_registry import validate_feishu_resource_url
    validate_feishu_resource_url(cfg['weekly_registry_url'], cfg['feishu_allowed_hosts'])
    if cfg['feishu_output_start_col'] < 1:
        raise RuntimeError('feishu_output_start_col 必须 >= 1')
    if len(cfg['feishu_output_headers']) != 6:
        raise RuntimeError('feishu_output_headers 必须正好 6 列')
    if not isinstance(cfg.get('html_archive_root'), str) or not cfg['html_archive_root'].strip():
        raise RuntimeError('html_archive_root 必须是非空路径')
    for key in ('html_archive_enabled', 'html_archive_required', 'html_server_enabled',
                'browser_auto_port', 'browser_no_sandbox', 'browser_disable_gpu'):
        if not isinstance(cfg.get(key), bool):
            raise RuntimeError(f'{key} 必须是布尔值')
    if not isinstance(cfg.get('html_server_bind'), str) or not cfg['html_server_bind'].strip():
        raise RuntimeError('html_server_bind 必须是非空字符串')

    feedback = cfg.get('feedback')
    if not isinstance(feedback, dict):
        raise RuntimeError('feedback 必须是对象')
    for key, lower, upper in (
        ('initial_window_days', 1, 31),
        ('incremental_window_days', 1, 14),
        ('retention_days', 1, 31),
        ('max_rating', 1, 5),
    ):
        try:
            value = int(feedback.get(key))
        except (TypeError, ValueError):
            raise RuntimeError(f'feedback.{key} 必须是整数')
        if not lower <= value <= upper:
            raise RuntimeError(f'feedback.{key} 超出范围 [{lower}, {upper}]')
    if feedback.get('timezone') != 'Asia/Shanghai':
        raise RuntimeError('feedback.timezone 必须固定为 Asia/Shanghai')
    if not isinstance(feedback.get('store_order'), list) or len(feedback['store_order']) != 2:
        raise RuntimeError('feedback.store_order 必须正好包含两个店铺')
    if len(set(feedback['store_order'])) != 2:
        raise RuntimeError('feedback.store_order 不能包含重复店铺')
    for key in ('page_wait_min', 'page_wait_max', 'detail_wait_min', 'detail_wait_max'):
        try:
            value = float(feedback.get(key))
        except (TypeError, ValueError):
            raise RuntimeError(f'feedback.{key} 必须是数字')
        if value < 1 or value > 120:
            raise RuntimeError(f'feedback.{key} 必须在1到120秒之间')
    if feedback['page_wait_max'] < feedback['page_wait_min']:
        raise RuntimeError('feedback.page_wait_max 不能小于 page_wait_min')
    if feedback['detail_wait_max'] < feedback['detail_wait_min']:
        raise RuntimeError('feedback.detail_wait_max 不能小于 detail_wait_min')
    try:
        max_pages = int(feedback.get('max_pages'))
    except (TypeError, ValueError):
        raise RuntimeError('feedback.max_pages 必须是整数')
    if not 1 <= max_pages <= 500:
        raise RuntimeError('feedback.max_pages 必须在1到500页之间')
    if not isinstance(feedback.get('stores'), list):
        raise RuntimeError('feedback.stores 必须是数组')
    if feedback.get('enabled'):
        if len(feedback['stores']) != 2:
            raise RuntimeError('启用Feedback时必须登记两个店铺')
        store_keys = []
        display_names = []
        for item in feedback['stores']:
            if not isinstance(item, dict):
                raise RuntimeError('feedback.stores 每项必须是对象')
            store_keys.append(str(item.get('key') or '').strip())
            display_names.append(str(item.get('display_name') or '').strip())
            for key in ('key', 'display_name', 'store_id', 'expected_store_identity',
                        'feedback_manager_url', 'secret_ref'):
                if not str(item.get(key) or '').strip():
                    raise RuntimeError(f'启用Feedback时 feedback.stores.{key} 不能为空')
            if not isinstance(item.get('selectors'), dict):
                raise RuntimeError('启用Feedback时 feedback.stores.selectors 必须是对象')
        if set(store_keys) != set(str(item) for item in feedback['store_order']):
            raise RuntimeError('feedback.stores.key 必须与 feedback.store_order 一一对应')
        if len(set(display_names)) != len(display_names):
            raise RuntimeError('启用Feedback时 feedback.stores.display_name 不能重复')
        if not str(feedback.get('target_spreadsheet_token') or '').strip():
            raise RuntimeError('启用Feedback时 target_spreadsheet_token 不能为空')
        if not str(feedback.get('target_sheet_id') or '').strip():
            raise RuntimeError('启用Feedback时 target_sheet_id 不能为空')


def ensure_dirs() -> None:
    for d in (OUTPUT_DIR, CACHE_ROOT, SNAPSHOT_DIR, CSV_DIR, LOG_DIR, DEBUG_DIR):
        d.mkdir(parents=True, exist_ok=True)
