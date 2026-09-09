# -*- coding: utf-8 -*-
"""Seller Central Feedback Manager browser adapter.

The business contract lives in :mod:`seller_feedback`; this module only
handles the deliberately narrow browser boundary.  It uses the official
``ziniao-cli`` bridge, keeps the two stores serial, and refuses to guess when
the visible page or control is not uniquely identifiable.

Real Amazon selectors are configuration data because Seller Central markup is
not stable.  The adapter is therefore disabled until each store has a tested
``selectors`` block.  No selector is inferred from a historical class name.
"""
from __future__ import annotations

import json
import random
import re
import subprocess
import time
from pathlib import Path
from typing import Callable, Protocol
from urllib.parse import urlparse

from seller_feedback import (FeedbackDataError, FeedbackSafetyStop, parse_feedback_date,
                              redact_secrets)


class SafetyStop(FeedbackSafetyStop):
    """The page/session no longer satisfies the safe automation contract."""


class FeedbackRunner(Protocol):
    def store_open(self, store_id: str) -> object: ...
    def store_close(self, store_id: str) -> object: ...
    def page_visit(self, store_id: str, url: str) -> object: ...
    def page_wait_nav(self, store_id: str, timeout_ms: int = 30000) -> object: ...
    def page_exec(self, store_id: str, script: str, timeout_ms: int = 30000) -> object: ...


_CLI_CANDIDATES = (
    Path(r'C:\Users\Administrator\.workbuddy\binaries\node\versions\22.22.2\ziniao-cli.cmd'),
    Path(r'C:\Users\Administrator\AppData\Roaming\npm\ziniao-cli.cmd'),
)
_DANGER_MARKERS = (
    'captcha', 'validatecaptcha', 'robot check', 'verify you are human',
    'signin', 'sign in', 'login required', 'access denied', 'suspicious',
    '被登出', '验证码', '风控',
)


def validate_feedback_manager_url(value: object) -> str:
    """Accept only an HTTPS Seller Central URL that names the feedback area."""
    url = str(value or '').strip()
    parsed = urlparse(url)
    host = (parsed.hostname or '').lower()
    host_pattern = r'^sellercentral(?:-[a-z0-9-]+)?\.amazon\.[a-z]{2,}(?:\.[a-z]{2,})?$'
    target = (parsed.path or '') + ('?' + parsed.query if parsed.query else '')
    if parsed.scheme.lower() != 'https' or not re.fullmatch(host_pattern, host):
        raise FeedbackDataError(
            f'Feedback管理器URL必须是HTTPS Seller Central域名，拒绝: {redact_secrets(url[:300])}'
        )
    if 'feedback' not in target.lower():
        raise FeedbackDataError(
            'Feedback管理器URL路径未包含feedback，拒绝访问非反馈页面'
        )
    return url


def parse_cli_json(stdout: str) -> object:
    """Parse strict JSON or the largest JSON object in noisy CLI output."""
    text = str(stdout or '').strip()
    if not text:
        raise RuntimeError('ziniao-cli 没有返回内容')
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        candidates = []
        for index, char in enumerate(text):
            if char != '{':
                continue
            try:
                value, _ = decoder.raw_decode(text[index:])
            except json.JSONDecodeError:
                continue
            candidates.append(value)
        if not candidates:
            raise RuntimeError('无法解析 ziniao-cli 输出: ' + text[-1000:])
        return max(candidates, key=lambda item: len(json.dumps(item, ensure_ascii=False)))


def _guard_page_output(value: object) -> None:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    lowered = text.lower()
    if any(marker in lowered for marker in _DANGER_MARKERS):
        raise SafetyStop(
            '紫鸟/亚马逊页面出现登录、验证码或风控信号，已停止：'
            + redact_secrets(text[:500])
        )


def _cli_value(value: object) -> object:
    if not isinstance(value, dict):
        return value
    if value.get('ok') is False:
        raise RuntimeError('ziniao-cli 失败: ' + redact_secrets(
            json.dumps(value, ensure_ascii=False)[:1200]))
    if value.get('exceptionDetails'):
        raise RuntimeError('页面脚本异常: ' + redact_secrets(
            json.dumps(value['exceptionDetails'], ensure_ascii=False)[:1200]))
    if 'result' in value:
        result = value['result']
        if isinstance(result, str):
            try:
                return json.loads(result)
            except json.JSONDecodeError:
                return result
        return result
    if 'data' in value:
        return _cli_value(value['data'])
    return value


class ZiniaoCliRunner:
    """Small official-CLI wrapper with output and risk-marker guards."""

    def __init__(self, cli_path: Path | None = None, *, cwd: Path | None = None,
                 run_fn: Callable = subprocess.run):
        self.cli_path = Path(cli_path) if cli_path else next(
            (item for item in _CLI_CANDIDATES if item.is_file()), None)
        self.cwd = Path(cwd) if cwd else Path.cwd()
        self.run_fn = run_fn

    def call(self, args: list[str], timeout: int = 120) -> object:
        if self.cli_path is None or not self.cli_path.is_file():
            raise RuntimeError('找不到已登记的紫鸟 CLI；未执行任何店铺操作')
        completed = self.run_fn(
            [str(self.cli_path), *args], cwd=str(self.cwd), capture_output=True,
            text=True, encoding='utf-8', errors='replace', timeout=timeout,
        )
        stderr = completed.stderr or ''
        _guard_page_output(stderr)
        if completed.returncode != 0:
            raise RuntimeError(
                'ziniao-cli exit=' + str(completed.returncode) + ': '
                + redact_secrets((stderr or completed.stdout or '')[-1500:])
            )
        if not (completed.stdout or '').strip():
            if stderr.strip() and not any(mark in stderr for mark in ('✓', '成功')):
                raise RuntimeError('ziniao-cli 成功但没有结构化返回值：' + redact_secrets(stderr[-1000:]))
            return {}
        value = parse_cli_json(completed.stdout)
        _guard_page_output(value)
        return _cli_value(value)

    def store_open(self, store_id: str) -> object:
        return self.call(['store', 'open', '--id', store_id], timeout=180)

    def store_close(self, store_id: str) -> object:
        return self.call(['store', 'close', '--id', store_id], timeout=60)

    def page_visit(self, store_id: str, url: str) -> object:
        return self.call([
            'page', 'visit', '--store-id', store_id, '--url', url,
            # Seller Central Feedback Manager keeps background requests open;
            # networkidle does not settle reliably. The collector performs its
            # own bounded post-navigation wait before reading the DOM.
            '--wait-until', 'domcontentloaded',
        ], timeout=180)

    def page_wait_nav(self, store_id: str, timeout_ms: int = 30000) -> object:
        return self.call([
            'page', 'wait-nav', '--store-id', store_id,
            '--timeout', str(timeout_ms),
        ], timeout=max(60, int(timeout_ms / 1000) + 30))

    def page_exec(self, store_id: str, script: str, timeout_ms: int = 30000) -> object:
        return self.call([
            'page', 'exec', '--store-id', store_id, '--timeout', str(timeout_ms),
            '--script', script,
        ], timeout=max(60, int(timeout_ms / 1000) + 30))


def _sleep_random(low: float, high: float, sleep_fn: Callable = time.sleep) -> float:
    delay = random.uniform(float(low), float(high))
    sleep_fn(delay)
    return delay


def _required_selector_config(store: dict) -> dict:
    selectors = store.get('selectors')
    if not isinstance(selectors, dict):
        raise FeedbackDataError(
            f"店铺 {store.get('key') or store.get('store_id')}: 未登记 selectors，拒绝猜测页面结构"
        )
    identity_mode = str(
        store.get('identity_mode') or selectors.get('store_identity_mode')
        or 'page_selector'
    ).strip()
    required = (
        'latest_feedback_marker', 'row_selector', 'date_selector',
        'rating_selector', 'order_id_selector', 'comment_selector',
    )
    missing = [key for key in required if not str(selectors.get(key) or '').strip()]
    if identity_mode != 'cli_store_context' and not str(
        selectors.get('store_identity_selector') or ''
    ).strip():
        missing.append('store_identity_selector')
    detail = selectors.get('detail')
    if not isinstance(detail, dict):
        missing.append('detail')
    else:
        for key in ('marker', 'order_id_selector', 'order_item_number_selector',
                    'asin_selector', 'sku_selector'):
            if not str(detail.get(key) or '').strip():
                missing.append('detail.' + key)
    if missing:
        raise FeedbackDataError(
            f"店铺 {store.get('key') or store.get('store_id')}: 页面选择器未登记: {', '.join(missing)}"
        )
    return selectors


def _read_script(selectors: dict) -> str:
    config = json.dumps({
        'marker': selectors['latest_feedback_marker'],
        'identity': selectors.get('store_identity_selector', ''),
        'row': selectors['row_selector'],
        'date': selectors['date_selector'],
        'rating': selectors['rating_selector'],
        'order': selectors['order_id_selector'],
        'comment': selectors['comment_selector'],
        'feedback_id': selectors.get('feedback_id_selector', ''),
        'order_link': selectors.get('order_link_selector', ''),
        'identity_mode': selectors.get('store_identity_mode', 'page_selector'),
    }, ensure_ascii=False)
    return f'''/* feedback-read */ JSON.stringify((() => {{
      const cfg = {config};
      const visible = el => {{
        if (!el) return false;
        const rect = el.getBoundingClientRect();
        const style = getComputedStyle(el);
        return rect.width > 0 && rect.height > 0 && style.display !== 'none'
          && style.visibility !== 'hidden' && style.opacity !== '0';
      }};
      const text = el => (el?.innerText || el?.textContent || '').trim();
      const attribute = (el, name) => (el?.getAttribute(name) || '').trim();
      const find = (root, selector) => selector ? root.querySelector(selector) : null;
      const shadowText = el => text(
        el?.shadowRoot?.querySelector('a,button,[role="button"],span')
      );
      const label = el => text(el) || shadowText(el)
        || (el?.getAttribute('aria-label') || '').trim()
        || (el?.getAttribute('label') || '').trim();
      const isNext = value => /^(下一个|下一页|next)/i.test(
        String(value || '').replace(/\\s+/g, '')
      );
      const markerNodes = cfg.marker ? [...document.querySelectorAll(cfg.marker)] : [];
      const exactMarkers = markerNodes.filter(el => text(el) === '最新反馈');
      const marker = exactMarkers.length === 1 ? exactMarkers[0]
        : (markerNodes.length === 1 ? markerNodes[0] : null);
      const identity = cfg.identity ? text(document.querySelector(cfg.identity)) : '';
      const orderValue = el => {{
        const direct = label(el);
        if (direct) return direct;
        const href = el?.getAttribute('href')
          || el?.shadowRoot?.querySelector('a[href]')?.getAttribute('href') || '';
        const match = String(href).match(/\/orders-v3\/order\/([^/?#]+)/);
        return match ? decodeURIComponent(match[1]) : '';
      }};
      const nodes = [...document.querySelectorAll(cfg.row)].filter(visible);
      const rows = nodes.map((node, index) => {{
        const value = key => {{
          const element = find(node, cfg[key]);
          if (key === 'order') return orderValue(element);
          if (key === 'rating') return attribute(element, 'value') || label(element);
          return text(element);
        }};
        return {{
          _dom_index: index,
          feedback_id: value('feedback_id'),
          feedback_date: value('date'),
          rating: value('rating'),
          order_id: value('order'),
          content: value('comment')
        }};
      }});
      const signature = rows.map(row => [row.feedback_id, row.feedback_date,
        row.rating, row.order_id, row.content].join('\\u241f')).join('\\u241e');
      const buttons = [...document.querySelectorAll(
        'button,a,[role="button"],kat-button,kat-link'
      )].filter(visible).filter(el => isNext(label(el)));
      const next = {{ count: buttons.length,
        disabled: buttons.length === 1 && (buttons[0].disabled ||
          buttons[0].getAttribute('aria-disabled') === 'true'
          || buttons[0].getAttribute('disabled') !== null) }};
      return {{ ok: true, marker_found: Boolean(marker), marker_count: markerNodes.length,
        store_identity: identity, identity_mode: cfg.identity_mode,
        row_count: rows.length,
        rows, signature, next, url: location.href }};
    }})())'''


def _click_next_script() -> str:
    return '''/* feedback-click-next */ JSON.stringify((() => {
      const visible = el => { const rect = el.getBoundingClientRect();
        const style = getComputedStyle(el); return rect.width > 0 && rect.height > 0
          && style.display !== 'none' && style.visibility !== 'hidden'
          && style.opacity !== '0'; };
      const text = el => (el.innerText || el.textContent || '').trim();
      const shadowText = el => text(
        el?.shadowRoot?.querySelector('a,button,[role="button"],span')
      );
      const label = el => text(el) || shadowText(el)
        || (el.getAttribute('aria-label') || '').trim()
        || (el.getAttribute('label') || '').trim();
      const isNext = value => /^(下一个|下一页|next)/i.test(
        String(value || '').replace(/\\s+/g, '')
      );
      const nodes = [...document.querySelectorAll(
        'button,a,[role="button"],kat-button,kat-link'
      )].filter(visible).filter(el => isNext(label(el)));
      if (nodes.length !== 1) return {clicked: false, count: nodes.length};
      const el = nodes[0];
      if (el.disabled || el.getAttribute('aria-disabled') === 'true'
          || el.getAttribute('disabled') !== null)
        return {clicked: false, count: 1, disabled: true};
      const target = el.shadowRoot?.querySelector('button,a,[role="button"]') || el;
      target.click(); return {clicked: true, count: 1};
    })())'''


def _click_order_script(selectors: dict, order_id: str) -> str:
    config = json.dumps({
        'row': selectors['row_selector'],
        'order': selectors['order_id_selector'],
        'link': selectors.get('order_link_selector', ''),
        'order_id': order_id,
    }, ensure_ascii=False)
    return f'''/* feedback-click-order */ JSON.stringify((() => {{
      const cfg = {config};
      const text = el => (el?.innerText || el?.textContent || '').trim();
      const orderValue = el => {{
        const direct = text(el) || (el?.getAttribute('aria-label') || '').trim();
        if (direct) return direct;
        const href = el?.getAttribute('href')
          || el?.shadowRoot?.querySelector('a[href]')?.getAttribute('href') || '';
        const match = String(href).match(/\/orders-v3\/order\/([^/?#]+)/);
        return match ? decodeURIComponent(match[1]) : '';
      }};
      const visible = el => {{ const rect = el.getBoundingClientRect();
        const style = getComputedStyle(el); return rect.width > 0 && rect.height > 0
          && style.display !== 'none' && style.visibility !== 'hidden'
          && style.opacity !== '0'; }};
      const rows = [...document.querySelectorAll(cfg.row)].filter(visible)
        .filter(row => orderValue(row.querySelector(cfg.order)) === cfg.order_id);
      if (rows.length !== 1) return {{clicked: false, row_count: rows.length}};
      const row = rows[0];
      let links = cfg.link ? [...row.querySelectorAll(cfg.link)] :
        [...row.querySelectorAll('a,button,[role="button"],kat-link')]
          .filter(el => orderValue(el) === cfg.order_id);
      links = links.filter(visible);
      if (links.length !== 1) return {{clicked: false, row_count: 1, link_count: links.length}};
      const target = links[0].shadowRoot?.querySelector('a[href],button,[role="link"]')
        || links[0];
      target.click(); return {{clicked: true, row_count: 1, link_count: 1}};
    }})())'''


def _detail_script(detail: dict) -> str:
    # Detail selectors may be CSS selectors or the verified label forms
    # ``text:<exact visible text>``, ``label:<exact visible field label>`` and
    # ``data-test-id:<value>``.  The latter two avoid guessing a dynamic class
    # when Seller Central renders a value as adjacent text nodes.
    config = json.dumps({
        'marker': detail['marker'],
        'order': detail['order_id_selector'],
        'item': detail['order_item_number_selector'],
        'asin': detail['asin_selector'],
        'sku': detail['sku_selector'],
    }, ensure_ascii=False)
    return f'''/* feedback-read-detail */ JSON.stringify((() => {{
      const cfg = {config};
      const text = el => (el?.innerText || el?.textContent || '').trim();
      const ownText = el => [...(el?.childNodes || [])]
        .filter(node => node.nodeType === Node.TEXT_NODE)
        .map(node => node.textContent || '').join('').trim();
      const visible = el => {{
        if (!el) return false;
        const rect = el.getBoundingClientRect();
        const style = getComputedStyle(el);
        return rect.width > 0 && rect.height > 0 && style.display !== 'none'
          && style.visibility !== 'hidden' && style.opacity !== '0';
      }};
      const exactText = value => [...document.querySelectorAll(
        'span,div,th,label,[role="heading"]'
      )].find(el => visible(el) && ownText(el) === value) || null;
      const resolve = selector => {{
        const value = String(selector || '');
        if (value.startsWith('text:') || value.startsWith('label:'))
          return exactText(value.slice(value.indexOf(':') + 1));
        if (value.startsWith('data-test-id:'))
          return document.querySelector('[data-test-id="'
            + value.slice('data-test-id:'.length).replace(/"/g, '\\"') + '"]');
        return value ? document.querySelector(value) : null;
      }};
      const labeledValue = selector => {{
        const value = String(selector || '');
        const element = resolve(value);
        if (!element) return '';
        const clean = rendered => String(rendered || '')
          .replace(/^\\s*[:：]\\s*/, '').trim();
        if (value.startsWith('text:') || value.startsWith('label:')) {{
          const parent = element.parentElement;
          if (!parent) return '';
          return clean([...parent.childNodes]
            .filter(node => node !== element && !element.contains(node))
            .map(node => node.nodeType === Node.TEXT_NODE
              ? (node.textContent || '') : text(node))
            .join(' ').replace(/\\s+/g, ' '));
        }}
        const rendered = text(element);
        if (value.startsWith('data-test-id:'))
          return clean(rendered.replace(/^订单编号：#?\\s*/, ''));
        return clean(rendered);
      }};
      const marker = resolve(cfg.marker);
      const route = location.href.match(/\\/orders-v3\\/order\\/([^/?#]+)/);
      const routeOrderId = route ? decodeURIComponent(route[1]) : '';
      const orderId = labeledValue(cfg.order) || routeOrderId;
      return {{ ok: true, marker_found: Boolean(marker),
        order_id: orderId,
        order_item_number: labeledValue(cfg.item),
        asin: labeledValue(cfg.asin), sku: labeledValue(cfg.sku), url: location.href }};
    }})())'''


class FeedbackStoreCollector:
    """Callable collector for one store; all page actions are serialized."""

    def __init__(self, store: dict, runner: FeedbackRunner, *, page_wait_min: float = 8.0,
                 page_wait_max: float = 12.0, detail_wait_min: float = 8.0,
                 detail_wait_max: float = 12.0, max_pages: int = 50,
                 sleep_fn: Callable = time.sleep):
        self.store = dict(store)
        self.runner = runner
        self.page_wait_min = page_wait_min
        self.page_wait_max = page_wait_max
        self.detail_wait_min = detail_wait_min
        self.detail_wait_max = detail_wait_max
        self.max_pages = max_pages
        self.sleep_fn = sleep_fn
        self.selectors = _required_selector_config(self.store)

    @property
    def key(self) -> str:
        return str(self.store.get('key') or self.store.get('store_id') or '').strip()

    def _read_page(self, store_id: str) -> dict:
        value = self.runner.page_exec(store_id, _read_script(self.selectors), 30000)
        _guard_page_output(value)
        if not isinstance(value, dict) or value.get('ok') is not True:
            raise SafetyStop('反馈页面读取没有返回结构化结果')
        if value.get('marker_found') is not True:
            raise SafetyStop('当前页面没有唯一可确认的【最新反馈】区域，停止读取')
        identity_mode = str(
            self.store.get('identity_mode') or self.selectors.get('store_identity_mode')
            or 'page_selector'
        ).strip()
        expected_identity = str(self.store.get('expected_store_identity') or '').strip()
        actual_identity = str(value.get('store_identity') or '').strip()
        if identity_mode == 'cli_store_context':
            # Amazon does not render the Seller ID/store label in this page's
            # visible header. The official CLI store context is the identity
            # boundary; compare the configured expected value to the exact
            # store ID used for every page call and record that mode in evidence.
            if not expected_identity or expected_identity != str(store_id).strip():
                raise SafetyStop(
                    '反馈页面 CLI 店铺上下文与登记身份不一致：'
                    f'expected={expected_identity!r}, store_id={store_id!r}'
                )
        elif not expected_identity or actual_identity != expected_identity:
            raise SafetyStop(
                f'反馈页面店铺身份不一致，expected={expected_identity!r}, actual={actual_identity!r}'
            )
        if not isinstance(value.get('rows'), list):
            raise SafetyStop('反馈页面 rows 不是数组，停止读取')
        return value

    def _read_detail(self, store_id: str, order_id: str) -> dict:
        value = self.runner.page_exec(store_id, _detail_script(self.selectors['detail']), 30000)
        _guard_page_output(value)
        if not isinstance(value, dict) or value.get('ok') is not True:
            raise SafetyStop('订单详情读取没有返回结构化结果')
        if value.get('marker_found') is not True:
            raise SafetyStop('订单二级页面缺少登记的详情标记，停止读取')
        if str(value.get('order_id') or '').strip() != str(order_id).strip():
            raise SafetyStop('订单详情回读的订单编号与点击目标不一致，停止读取')
        return value

    def _return_to_feedback(self, store_id: str) -> dict:
        value = self.runner.page_exec(
            store_id,
            '''/* feedback-back */ JSON.stringify((() => { history.back(); return {started: true}; })())''',
            30000,
        )
        _guard_page_output(value)
        self.runner.page_wait_nav(store_id, 30000)
        _sleep_random(self.page_wait_min, self.page_wait_max, self.sleep_fn)
        return self._read_page(store_id)

    def __call__(self, *, window: dict | None = None) -> dict:
        started = time.monotonic()
        store_id = str(self.store.get('store_id') or '').strip()
        url = validate_feedback_manager_url(self.store.get('feedback_manager_url'))
        if not store_id or not url:
            raise FeedbackDataError(f'店铺 {self.key}: store_id 或 feedback_manager_url 未登记')
        start = parse_feedback_date((window or {}).get('start')) if window else None
        end = parse_feedback_date((window or {}).get('end')) if window else None
        pages = []
        detail_attempted = detail_complete = next_clicks = 0
        self.runner.store_open(store_id)
        try:
            _sleep_random(5.0, 7.0, self.sleep_fn)
            self.runner.page_visit(store_id, url)
            self.runner.page_wait_nav(store_id, 30000)
            _sleep_random(self.page_wait_min, self.page_wait_max, self.sleep_fn)
            previous_signature = None
            for page_number in range(1, self.max_pages + 1):
                payload = self._read_page(store_id)
                signature = str(payload.get('signature') or '')
                if previous_signature is not None and signature == previous_signature:
                    raise SafetyStop('反馈【下一个】点击后页面内容未变化，停止重复读取')
                previous_signature = signature
                page_items = []
                for primary in payload['rows']:
                    if not isinstance(primary, dict):
                        continue
                    item = dict(primary)
                    feedback_date = parse_feedback_date(item.get('feedback_date'))
                    rating_text = str(item.get('rating') or '')
                    eligible = bool(
                        feedback_date is not None and 1 <= _rating(rating_text) <= 3
                        and (start is None or feedback_date >= start)
                        and (end is None or feedback_date <= end)
                    )
                    item['_detail_status'] = 'skipped_out_of_window' if not eligible else 'pending'
                    if eligible:
                        order_id = str(item.get('order_id') or '').strip()
                        if not order_id:
                            item['_detail_status'] = 'partial'
                            item['_detail_error'] = '符合条件的反馈缺少订单编号'
                        else:
                            detail_attempted += 1
                            clicked = self.runner.page_exec(
                                store_id, _click_order_script(self.selectors, order_id), 30000)
                            _guard_page_output(clicked)
                            if not isinstance(clicked, dict) or clicked.get('clicked') is not True:
                                raise SafetyStop(
                                    '未找到唯一可见订单链接，拒绝按序号或模糊点击：'
                                    + redact_secrets(json.dumps(clicked, ensure_ascii=False)[:1000])
                                )
                            _sleep_random(self.detail_wait_min, self.detail_wait_max, self.sleep_fn)
                            in_detail = True
                            try:
                                detail = self._read_detail(store_id, order_id)
                                item.update({
                                    'order_item_number': str(detail.get('order_item_number') or '').strip(),
                                    'asin': str(detail.get('asin') or '').strip(),
                                    'sku': str(detail.get('sku') or '').strip(),
                                    '_detail_status': 'ok' if all(
                                        detail.get(key) for key in ('order_item_number', 'asin', 'sku'))
                                    else 'partial',
                                })
                                if item['_detail_status'] == 'ok':
                                    detail_complete += 1
                            except SafetyStop:
                                # Login/CAPTCHA/risk or identity mismatch is an
                                # immediate stop.  Do not navigate again after a
                                # risk signal; the caller records the blocked run.
                                raise
                            except Exception as exc:
                                item['_detail_status'] = 'partial'
                                item['_detail_error'] = redact_secrets(
                                    f'{type(exc).__name__}: {exc}')
                            finally:
                                # A detail failure may still leave us in the detail page.
                                # Back-navigation is verified before another row/page action.
                                if in_detail:
                                    self._return_to_feedback(store_id)
                    page_items.append(item)
                pages.append({
                    'source_url': url,
                    'page_number': page_number,
                    'items': page_items,
                    'signature': signature,
                })
                next_info = payload.get('next') or {}
                if next_info.get('count', 0) == 0:
                    if payload['rows']:
                        raise SafetyStop(
                            '反馈页面没有可确认的【下一个】按钮，拒绝静默截断分页'
                        )
                    break
                if next_info.get('disabled'):
                    break
                if next_info.get('count') != 1:
                    raise SafetyStop(
                        '反馈页面没有唯一可见的【下一个】按钮，拒绝猜测：'
                        + json.dumps(next_info, ensure_ascii=False)
                    )
                clicked = self.runner.page_exec(store_id, _click_next_script(), 30000)
                _guard_page_output(clicked)
                if not isinstance(clicked, dict) or clicked.get('clicked') is not True:
                    raise SafetyStop(
                        '反馈【下一个】按钮未成功点击：'
                        + redact_secrets(json.dumps(clicked, ensure_ascii=False)[:1000])
                    )
                next_clicks += 1
                self.runner.page_wait_nav(store_id, 30000)
                _sleep_random(self.page_wait_min, self.page_wait_max, self.sleep_fn)
            else:
                raise SafetyStop(f'反馈分页超过安全上限 {self.max_pages} 页，停止读取')
        finally:
            # Closing failure is intentionally visible to collect_feedback; the
            # caller must not continue to the second store with an uncertain context.
            self.runner.store_close(store_id)
        return {
            'source_url': url,
            'pages': pages,
            'detail_attempted': detail_attempted,
            'detail_complete': detail_complete,
            'next_clicks': next_clicks,
            'elapsed_seconds': round(time.monotonic() - started, 3),
        }


def _rating(value: object) -> int:
    text = str(value or '')
    match = re.search(r'\d+', text)
    return int(match.group(0)) if match else 0


def build_feedback_collectors(feedback_cfg: dict, *, runner: FeedbackRunner | None = None,
                              sleep_fn: Callable = time.sleep) -> dict[str, Callable]:
    """Build one serial collector per registered store without opening it yet."""
    stores = feedback_cfg.get('stores') or []
    if len(stores) != 2:
        raise FeedbackDataError('Feedback必须登记两个店铺后才能建立采集器')
    runner = runner or ZiniaoCliRunner()
    result = {}
    for store in stores:
        if not isinstance(store, dict):
            raise FeedbackDataError('feedback.stores 每项必须是对象')
        key = str(store.get('key') or '').strip()
        if not key or key in result:
            raise FeedbackDataError('Feedback店铺 key 为空或重复')
        result[key] = FeedbackStoreCollector(
            store, runner,
            page_wait_min=float(feedback_cfg.get('page_wait_min', 8.0)),
            page_wait_max=float(feedback_cfg.get('page_wait_max', 12.0)),
            detail_wait_min=float(feedback_cfg.get('detail_wait_min', 8.0)),
            detail_wait_max=float(feedback_cfg.get('detail_wait_max', 12.0)),
            max_pages=int(feedback_cfg.get('max_pages', 50)),
            sleep_fn=sleep_fn,
        )
    return result
