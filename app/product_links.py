# -*- coding: utf-8 -*-
"""MarketplaceProfile 与 ASIN/商品 URL 安全规范化。"""
from __future__ import annotations

import re
import json
from dataclasses import dataclass
from urllib.parse import unquote, urlparse
from pathlib import Path

ASIN_FULL_RE = re.compile(r'^B0[A-Z0-9]{8}$', re.IGNORECASE)
ASIN_SEARCH_RE = re.compile(r'(?<![A-Z0-9])(B0[A-Z0-9]{8})(?![A-Z0-9])', re.IGNORECASE)
URL_RE = re.compile(r'https?://[^\s"<>]+', re.IGNORECASE)


@dataclass(frozen=True)
class MarketplaceProfile:
    code: str
    domain: str
    currency_code: str

    @property
    def allowed_hosts(self) -> set[str]:
        return {self.domain, f'www.{self.domain}', f'smile.{self.domain}'}

    def product_url(self, asin: str) -> str:
        return f'https://www.{self.domain}/dp/{asin}'


MARKETPLACES = {
    'US': MarketplaceProfile('US', 'amazon.com', 'USD'),
    'CA': MarketplaceProfile('CA', 'amazon.ca', 'CAD'),
}


class ProductLinkError(ValueError):
    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


def _flatten(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, dict):
        parts = []
        for key in ('link', 'url', 'text', 'value', 'formula'):
            if key in value:
                parts.extend(_flatten(value.get(key)))
        return parts
    if isinstance(value, (list, tuple)):
        parts = []
        for item in value:
            parts.extend(_flatten(item))
        return parts
    text = str(value).strip()
    return [text] if text else []


def _asin_from_url(url: str, profile: MarketplaceProfile) -> str:
    parsed = urlparse(url.rstrip(').,;'))
    host = (parsed.hostname or '').lower().rstrip('.')
    if host not in profile.allowed_hosts:
        amazon_hosts = MARKETPLACES['US'].allowed_hosts | MARKETPLACES['CA'].allowed_hosts
        reason = 'cross_marketplace' if host in amazon_hosts else 'invalid_domain'
        raise ProductLinkError(reason, f'不允许的商品链接域名: {host or "<empty>"}')
    path = unquote(parsed.path or '')
    match = re.search(r'/(?:dp|gp/product)/([A-Z0-9]{10})(?:/|$)', path, re.IGNORECASE)
    if not match or not ASIN_FULL_RE.fullmatch(match.group(1)):
        raise ProductLinkError('asin_not_found', 'Amazon URL 中没有合法 B0 ASIN')
    return match.group(1).upper()


def normalize_product(value, marketplace: str) -> tuple[str, str]:
    """返回 (ASIN, 标准商品 URL)；显式 URL 永远先做域名校验。"""
    profile = MARKETPLACES.get(str(marketplace).upper())
    if not profile:
        raise ProductLinkError('unknown_marketplace', f'未知 Marketplace: {marketplace}')
    parts = _flatten(value)
    if not parts:
        raise ProductLinkError('empty', 'ASIN 单元格为空')
    urls = []
    for part in parts:
        urls.extend(URL_RE.findall(part))
    if urls:
        errors = []
        for url in urls:
            try:
                asin = _asin_from_url(url, profile)
                return asin, profile.product_url(asin)
            except ProductLinkError as exc:
                errors.append(exc)
        # 存在显式 URL 时禁止回退到显示文本中的 ASIN，以免绕过恶意域名检查。
        raise errors[0]
    for part in parts:
        stripped = part.strip().upper()
        if ASIN_FULL_RE.fullmatch(stripped):
            return stripped, profile.product_url(stripped)
    for part in parts:
        match = ASIN_SEARCH_RE.search(part)
        if match:
            asin = match.group(1).upper()
            return asin, profile.product_url(asin)
    raise ProductLinkError('asin_not_found', '未提取到合法 B0 ASIN')


def source_product_url(value, marketplace: str, expected_asin: str | None = None) -> str:
    """返回经过同一域名/ASIN校验的源链接（保留原始查询参数）。

    周报中的 `?th=1`、`?psc=1` 或变体上下文不能在读取时无声丢弃。
    该函数只返回已通过 Marketplace 校验的 Amazon 链接；没有显式 URL
    时返回空字符串，调用方应回退到标准 `/dp/{asin}`。
    """
    profile = MARKETPLACES.get(str(marketplace).upper())
    if not profile:
        raise ProductLinkError('unknown_marketplace', f'未知 Marketplace: {marketplace}')
    expected = (expected_asin or '').upper()
    for part in _flatten(value):
        for raw_url in URL_RE.findall(part):
            url = raw_url.rstrip(').,;')
            asin = _asin_from_url(url, profile)
            if expected and asin.upper() != expected:
                raise ProductLinkError(
                    'asin_mismatch', f'商品链接 ASIN {asin} 与 ASIN 列 {expected} 不一致')
            return url
    return ''


def safe_preview(value, limit: int = 100) -> str:
    """审计报告只保留类型与截断文本，不输出 Cookie 等页面内容。"""
    text = ' | '.join(_flatten(value))
    return text[:limit]


def _col_letter(number: int) -> str:
    out = ''
    while number:
        number, rem = divmod(number - 1, 26)
        out = chr(65 + rem) + out
    return out


def audit_manifest_links(fc, manifest: dict) -> dict:
    """只读审计所有当前有 ASIN 表头的映射表并生成标准 URL。"""
    snapshot = manifest['snapshot']['spreadsheet_token']
    from sheet_io import read_rows
    sheet_reports = []
    total_valid = total_invalid = total_skipped = 0
    for mapping in manifest.get('sheet_mappings') or []:
        title = mapping['source_sheet']
        marketplace = mapping['marketplace']
        header_row = mapping.get('header_row')
        asin_col = mapping.get('asin_col')
        valid = []
        invalid = []
        skipped = []
        if header_row and asin_col:
            letter = _col_letter(int(asin_col))
            values = read_rows(fc, snapshot, mapping['source_sheet_id'],
                first=letter, last=letter, start=int(header_row) + 1, row_count=mapping.get('row_capacity'))
            for offset, row in enumerate(values, start=int(header_row) + 1):
                value = row[0] if isinstance(row, list) and row else row
                if not _flatten(value):
                    continue
                try:
                    asin, url = normalize_product(value, marketplace)
                    source_url = source_product_url(value, marketplace, asin)
                    valid.append({'row': offset, 'asin': asin, 'product_url': url,
                                  'source_product_url': source_url,
                                  'source_type': type(value).__name__})
                except ProductLinkError as exc:
                    preview = safe_preview(value)
                    productish = bool(URL_RE.search(preview) or re.search(
                        r'(?<![A-Z0-9])B0[A-Z0-9]*', preview, re.IGNORECASE))
                    target = invalid if productish or exc.reason not in ('asin_not_found',) else skipped
                    target.append({'row': offset, 'reason': (exc.reason if productish
                                   else 'non_product_label'),
                                   'source_type': type(value).__name__, 'preview': preview})
        total_valid += len(valid)
        total_invalid += len(invalid)
        total_skipped += len(skipped)
        sheet_reports.append({
            'sheet': title, 'sheet_id': mapping['source_sheet_id'],
            'marketplace': marketplace, 'currency_code': MARKETPLACES[marketplace].currency_code,
            'valid_count': len(valid), 'invalid_count': len(invalid),
            'skipped_non_product_count': len(skipped),
            'valid': valid, 'invalid': invalid, 'skipped_non_product': skipped,
        })
    return {'period_id': manifest['period_id'], 'valid_count': total_valid,
            'invalid_count': total_invalid, 'skipped_non_product_count': total_skipped,
            'sheets': sheet_reports}


def save_link_audit(report: dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.json.tmp')
    temp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)
    return path
