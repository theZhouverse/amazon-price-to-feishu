# -*- coding: utf-8 -*-
"""pricing.py — 纯计算：Decimal 金额、四类型分类、一致性检查（无 IO，可单测）"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from models import (
    CrawlResult, PageStatus, ReportRow,
    TYPE_COUPON, TYPE_CODE, TYPE_PRICE, TYPE_ORIGINAL, DISCOUNT_TYPES,
)

TWO = Decimal('0.01')

# 折扣形式归一化 → 四种类型
_TYPE_MAP = {
    '原价调整': TYPE_ORIGINAL, '原价定档': TYPE_ORIGINAL, '原价定价': TYPE_ORIGINAL,
    '原价': TYPE_ORIGINAL, 'yourprice': TYPE_ORIGINAL, 'normalprice': TYPE_ORIGINAL,
    'code': TYPE_CODE, 'sale': TYPE_CODE, 'sales': TYPE_CODE,
    '价格折扣': TYPE_PRICE, '价格打折': TYPE_PRICE, '折扣': TYPE_PRICE,
    'deal': TYPE_PRICE, 'bd': TYPE_PRICE, 'ld': TYPE_PRICE, 'dod': TYPE_PRICE,
    'pricediscount': TYPE_PRICE, 'promotion': TYPE_PRICE, 'promo': TYPE_PRICE,
    'coupon': TYPE_COUPON,
}
# 预期类型候选（含归一化键）
_EXPECTED_KEYS = ('coupon', 'code', '价格折扣', '价格打折', '原价调整', '原价定档',
                  '原价', 'sale', 'sales', 'deal', 'bd', 'ld', 'dod', 'promo', 'promotion')
_ORIGINAL_PRICE_TYPES = {'原价调整', '原价定档', '原价'}
_SOURCE_PRICE_RANGE_RE = re.compile(
    r'^\s*([0-9]+(?:\.[0-9]+)?)\s*[-–—]\s*([0-9]+(?:\.[0-9]+)?)\s*$'
)


def dec(x) -> Decimal | None:
    """安全转 Decimal，失败返回 None"""
    if x is None or x == '':
        return None
    try:
        d = Decimal(str(x).strip())
        return d if d.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def q2(d: Decimal | None) -> Decimal | None:
    """量化到两位小数（ROUND_HALF_UP）"""
    if d is None:
        return None
    return d.quantize(TWO, rounding=ROUND_HALF_UP)


def parse_price_text(t) -> Decimal | None:
    """页面文本 → Decimal。去除 $, 逗号, 空格, \xa0，取首个数字。"""
    if t is None:
        return None
    s = str(t).replace(' ', '').replace('\xa0', '').replace(',', '')
    m = re.search(r'(\d+(?:\.\d+)?)', s)
    if not m:
        return None
    try:
        return Decimal(m.group(1))
    except InvalidOperation:
        return None


def parse_pct_text(t) -> Decimal | None:
    """'30%' / '-25%' → Decimal('0.30') / Decimal('0.25')（百分比内部一律存小数）"""
    if t is None:
        return None
    m = re.search(r'([\d.]+)\s*%', str(t))
    if not m:
        return None
    try:
        return abs(Decimal(m.group(1))) / Decimal(100)
    except (InvalidOperation, ZeroDivisionError):
        return None


def format_amount(d: Decimal | None) -> str:
    """金额两位小数：Decimal('-10') → '-10.00'；Decimal('15') → '15.00'"""
    if d is None:
        return ''
    return f'{q2(d):.2f}'


def format_pct(p: Decimal | None) -> str:
    """百分比小数 → 文本：Decimal('0.30') → '30%'；0.255 → '25.5%'（去尾零）"""
    if p is None:
        return ''
    x = p * 100
    q = x.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    s = format(q, 'f')
    if '.' in s:
        s = s.rstrip('0').rstrip('.')
    return f'{s}%'


def normalize_type(f: str) -> str | None:
    """周报'本周折扣形式' → 四种类型之一；无法识别返回 None"""
    s = (f or '').strip().lower().replace(' ', '').replace('的', '').replace('-', '')
    if not s:
        return None
    for k in _EXPECTED_KEYS:
        if s == k or s.endswith(k) or k.endswith(s):
            # 精确或包含匹配；避免 'code' 误伤 'pricediscount' 已由顺序保护
            if k in _TYPE_MAP:
                return _TYPE_MAP[k]
    # 兜底：逐字包含
    for k, v in _TYPE_MAP.items():
        if k in s:
            return v
    return None


def calc_target_price(row: ReportRow) -> Decimal | None:
    """Calculate the target price using the weekly-report formula semantics.

    The original-price branch is deliberately fail-closed: an empty or
    nonnumeric discount value must not silently become the normal price.  A
    source range such as ``26.99-28.99`` follows the current report formula's
    right-hand value.  Other discount types retain the historical E fallback
    for blank/zero values.
    """
    e = row.normal_price
    if e is None:
        return None
    i = dec(row.i_value)
    h = (row.h_type or '').strip()
    if h in _ORIGINAL_PRICE_TYPES:
        if i is None:
            raw = getattr(row, 'i_raw', None)
            if isinstance(raw, str):
                match = _SOURCE_PRICE_RANGE_RE.fullmatch(raw)
                if match:
                    i = dec(match.group(2))
        if i is None or i < 0:
            return None
        if i > 1:
            return q2(i)
        return q2(e * (1 - i))
    if i is None or i == 0:
        return q2(e)
    if 0 < i < 1:
        return q2(e * (1 - i))
    return q2(e)


def _evidence_set(cr: CrawlResult) -> set[str]:
    """页面证据类型集合；无任何证据视为 原价调整"""
    s: set[str] = set()
    if cr.coupon_pct is not None or cr.coupon_amount is not None or cr.coupon_final is not None:
        s.add(TYPE_COUPON)
    if cr.code_pct is not None:
        s.add(TYPE_CODE)
    if cr.save_pct is not None:
        s.add(TYPE_PRICE)
    if not s:
        s.add(TYPE_ORIGINAL)
    return s


def decide_discount_type(cr: CrawlResult) -> tuple[str, bool]:
    """按页面真实证据分类，周报类型只用于记录是否不一致。

    页面可能同时显示价格折扣和进一步优惠，固定优先级为：
    coupon > code > 价格折扣 > 原价调整。
    返回 (实际页面类型, 是否与周报预期类型不一致)。
    """
    expected = normalize_type(cr.expected_type)
    ev = _evidence_set(cr)
    actual = next(t for t in (TYPE_COUPON, TYPE_CODE, TYPE_PRICE, TYPE_ORIGINAL) if t in ev)
    return actual, expected is not None and expected != actual


def compute_result(row: ReportRow, cr: CrawlResult, tolerance: str) -> None:
    """填充 cr 的六列业务字段（display_price/discount_type/discount_value/final_price/match/timestamp 由调用方给）"""
    if cr.status != PageStatus.OK:
        cr.final_price, cr.match, cr.discount_type, cr.discount_value = None, '-', '-', ''
        return
    tol = dec(tolerance)
    if tol is None:
        tol = Decimal('0.50')
    for value in (tol, cr.display_price, row.normal_price, row.target_price,
                  cr.coupon_amount, cr.coupon_final, cr.coupon_pct, cr.code_pct, cr.save_pct):
        if value is not None and not value.is_finite():
            cr.status, cr.match = PageStatus.PARSE_ERROR, '-'
            cr.error = 'non_finite_price: 金额或比例不是有限数字'
            cr.final_price = None
            return
    if tol < 0:
        raise ValueError('价格容差不得为负数')
    for name in ('coupon_pct', 'code_pct', 'save_pct'):
        value = getattr(cr, name)
        if value is not None and not 0 < value < 1:
            cr.status, cr.match = PageStatus.PARSE_ERROR, '-'
            cr.error = f'invalid_promotion: {name}必须大于0且小于100%'
            cr.final_price = None
            return
    display = cr.display_price
    if cr.coupon_final is not None and (cr.coupon_final <= 0 or
            (display is not None and cr.coupon_final > display)):
        cr.status, cr.match = PageStatus.PARSE_ERROR, '-'
        cr.error = 'invalid_coupon_final: Coupon最终价必须大于0且不超过主价'
        cr.final_price = None
        return
    if cr.coupon_amount is not None and (cr.coupon_amount < 0 or
            (display is not None and cr.coupon_amount > display)):
        cr.status, cr.match = PageStatus.PARSE_ERROR, '-'
        cr.error = 'invalid_coupon_amount: 优惠金额不能为负数或超过主价'
        cr.final_price = None
        return

    if display is None or display <= 0:
        cr.status = PageStatus.SOLD_OUT if cr.status == PageStatus.OK else cr.status
        cr.match = '-'
        return

    typ, mismatch = decide_discount_type(cr)
    if not typ:
        cr.status = PageStatus.PARSE_ERROR
        cr.error = f'多类型证据无法唯一确定: 证据={sorted(_evidence_set(cr))}'
        cr.match = '-'
        return

    cr.discount_type = typ
    cr.expected_type_mismatch = mismatch
    cr.target_price = row.target_price if row.target_price is not None else row.normal_price

    # 折扣值 + 最终价格
    if typ == TYPE_ORIGINAL:
        # 折扣值 = 目标成交价 - 正常售价；最终价格 = 展示价格
        diff = (cr.target_price or Decimal(0)) - (row.normal_price or Decimal(0))
        cr.discount_value = format_amount(diff)
        cr.final_price = q2(display)
    elif typ == TYPE_CODE:
        pct = cr.code_pct
        if pct is None or pct >= 1:
            cr.status = PageStatus.PARSE_ERROR
            cr.error = 'code 证据缺比例或比例异常'
            cr.match = '-'
            return
        cr.discount_value = format_pct(pct)
        cr.final_price = q2(display * (1 - pct))
    elif typ == TYPE_PRICE:
        # 价格折扣：展示价格已是折后主价，不重复扣减
        cr.discount_value = format_pct(cr.save_pct) if cr.save_pct is not None else ''
        cr.final_price = q2(display)
    else:  # coupon（3.10：折扣值优先百分比，最终价优先 Saving 金额；0 值视为无效）
        if cr.coupon_final is not None and cr.coupon_final > 0:
            cr.discount_value = format_pct(cr.coupon_pct) if cr.coupon_pct is not None \
                else format_amount(cr.coupon_amount)
            cr.final_price = q2(cr.coupon_final)
        elif cr.coupon_amount is not None and cr.coupon_amount > 0:
            cr.discount_value = format_pct(cr.coupon_pct) if cr.coupon_pct is not None \
                else format_amount(cr.coupon_amount)
            cr.final_price = q2(display - cr.coupon_amount)
        elif cr.coupon_pct is not None and 0 < cr.coupon_pct < 1:
            cr.discount_value = format_pct(cr.coupon_pct)
            cr.final_price = q2(display * (1 - cr.coupon_pct))
        else:
            cr.status = PageStatus.PARSE_ERROR
            cr.error = 'coupon 证据缺少比例/金额/最终价'
            cr.match = '-'
            return

    # 一致性检查前先验证币种。源金额币种由子表 Marketplace 唯一决定；
    # 未知币种或跨站币种只保留解析结果，不做数值比较，也不做汇率换算。
    expected_currency = {'US': 'USD', 'CA': 'CAD'}.get((row.marketplace or '').upper())
    actual_currency = (cr.currency_code or '').upper()
    if not expected_currency or actual_currency != expected_currency:
        cr.status = PageStatus.CURRENCY_ERROR
        cr.price_diff = None
        cr.match = '-'
        cr.error = (f'currency_mismatch: marketplace={row.marketplace or "unknown"}, '
                    f'expected={expected_currency or "unknown"}, '
                    f'actual={actual_currency or "unknown"}')
        return

    # 同币种一致性检查
    f, t = q2(cr.final_price), q2(cr.target_price)
    if f is None or t is None:
        cr.match = '-'
        return
    diff = f - t
    cr.price_diff = diff
    sign = '+' if diff > 0 else ('-' if diff < 0 else '')
    if abs(diff) <= tol:
        cr.match = f'✅({sign}{abs(diff):.2f})'
    else:
        cr.match = f'❌({sign}{abs(diff):.2f})'


def build_abnormal(six_ts: str) -> CrawlResult:
    """异常/售罄/404 的六列模板：除时间戳外全空，一致性为 '-'。"""
    cr = CrawlResult(asin='')
    cr.timestamp = six_ts
    cr.match = '-'
    return cr
