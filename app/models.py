# -*- coding: utf-8 -*-
"""models.py — 数据模型：页面状态、周报行、抓取+计算结果"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum


class PageStatus(str, Enum):
    """内部页面状态（飞书六列只输出业务结果，状态写本地）"""
    OK = 'ok'                        # 正常获得展示价格并分类
    PAGE_NOT_FOUND = 'page_not_found'  # 明确 404
    SOLD_OUT = 'sold_out'            # 页面存在但无价格，业务归类售罄
    CRAWL_ERROR = 'crawl_error'      # 技术异常：captcha/blocked/超时/空白/通信失败
    IDENTITY_MISMATCH = 'identity_mismatch'  # 已加载但请求ASIN被站点重定向为其他商品
    PARSE_ERROR = 'parse_error'      # 页面正常但无法按规则解析（冲突/结构变化）
    CURRENCY_ERROR = 'currency_error'  # Marketplace/币种未知或不一致，禁止价格比较
    SOURCE_INVALID = 'source_data_invalid'  # 源数据无效（E/K 为空），不抓取，六列输出 '-'


# 正常页面四种折扣类型
DISCOUNT_TYPES = ('原价调整', 'code', '价格折扣', 'coupon')
TYPE_COUPON = 'coupon'
TYPE_CODE = 'code'
TYPE_PRICE = '价格折扣'
TYPE_ORIGINAL = '原价调整'

# 目标成交价来源（5.x）
TARGET_SOURCE_FEISHU = 'feishu_value'
TARGET_SOURCE_EXCEL = 'excel_cached_value'
TARGET_SOURCE_FALLBACK = 'local_fallback'
TARGET_SOURCE_MISSING = 'missing'


@dataclass
class ReportRow:
    """周报一行（来自飞书原始表或本地 xlsx 快照）"""
    row_num: int                    # 源表行号
    asin: str
    sku: str = ''
    size: str = ''                       # 尺寸（源表“尺寸”列）
    normal_price: Decimal | None = None   # 正常售价 E 列
    h_type: str = ''                # 本周折扣形式
    i_value: str | Decimal | None = None  # 本周折扣值（原值，可为 % 文本或金额）
    target_price: Decimal | None = None   # 目标成交价（K 列公式结果）
    target_price_source: str = TARGET_SOURCE_MISSING   # 来源追踪
    marketplace: str = 'US'
    product_url: str = ''
    source_product_url: str = ''

    def as_dict(self) -> dict:
        return {
            'row_num': self.row_num, 'asin': self.asin, 'sku': self.sku, 'size': self.size,
            'normal_price': str(self.normal_price) if self.normal_price is not None else None,
            'h_type': self.h_type,
            'i_value': str(self.i_value) if self.i_value is not None else None,
            'target_price': str(self.target_price) if self.target_price is not None else None,
            'target_price_source': self.target_price_source,
            'marketplace': self.marketplace, 'product_url': self.product_url,
            'source_product_url': self.source_product_url,
        }


@dataclass
class PriceCandidate:
    """主价候选记录（用于追溯和冲突检测）"""
    rule: str                       # 命中的选择器/规则名
    raw_text: str = ''
    value: Decimal | None = None
    visible: bool = True


@dataclass
class CrawlResult:
    """单个 ASIN 的完整抓取+计算结果（含诊断字段，飞书只用六列）"""
    asin: str
    run_id: str = ''
    status: PageStatus = PageStatus.OK
    error: str = ''

    # 六列业务字段
    display_price: Decimal | None = None
    discount_type: str = ''         # 原价调整/code/价格折扣/coupon / '-'（异常）
    discount_value: str = ''        # '30%' 或 '-10.00' 或 '15.00'
    final_price: Decimal | None = None
    match: str = ''                 # '✅(+0.20)' / '❌(-0.75)' / '-'
    timestamp: str = ''

    # 诊断字段
    target_price: Decimal | None = None
    price_diff: Decimal | None = None
    price_rule: str = ''            # 命中的主价规则
    price_candidates: list[PriceCandidate] = field(default_factory=list)
    promotion_raw: str = ''         # coupon/code/save 原始文案拼接
    coupon_pct: Decimal | None = None
    coupon_amount: Decimal | None = None
    coupon_final: Decimal | None = None
    code_pct: Decimal | None = None
    save_pct: Decimal | None = None
    expected_type: str = ''         # 周报预期类型
    expected_type_mismatch: bool = False
    attempt_count: int = 0
    page_url: str = ''
    page_title: str = ''
    duration_ms: int = 0
    marketplace: str = 'US'
    currency_code: str = ''
    product_url: str = ''
    source_product_url: str = ''
    location_verified: bool = False
    # 可抓取的位置策略上下文；direct_no_postal 为 True 但不代表邮编已验证。
    location_context_ready: bool = False
    location_verification_method: str = ''
    risk_cooldown_seconds: float = 0.0
    html_path: str = ''
    html_url: str = ''
    html_sha256: str = ''
    html_size_bytes: int = 0
    archive_ms: int = 0
    archive_status: str = ''
    archive_error: str = ''
    post_archive_delay_seconds: float = 0.0
    stripped_noncore_css_resources: int = 0
    # Same-page product checks.  The values are evidence-rich dictionaries;
    # only their status is written to the fixed result table.
    frontend_checks: dict = field(default_factory=dict)
    frontend_check_rule_version: str = ''

    def six_columns(self) -> list:
        """飞书固定六列（顺序固定）：展示价格/折扣类型/折扣值/最终价格/价格一致性/时间戳"""
        def _num(d: Decimal | None) -> float | None:
            return round(float(d), 2) if d is not None else None
        return [
            _num(self.display_price),
            self.discount_type or '-',
            self.discount_value,
            _num(self.final_price),
            self.match,
            self.timestamp,
        ]

    def frontend_columns(self) -> list[str]:
        """Visible N:T values; checks are ✅/❌ and unknown states are '-'."""
        from frontend_checks import frontend_columns
        return frontend_columns(
            self.frontend_checks,
            page_status=self.status.value if isinstance(self.status, PageStatus)
            else str(self.status or ''),
        )

    def as_dict(self) -> dict:
        return {
            'asin': self.asin, 'run_id': self.run_id,
            'status': self.status.value, 'error': self.error,
            'display_price': str(self.display_price) if self.display_price is not None else None,
            'discount_type': self.discount_type,
            'discount_value': self.discount_value,
            'final_price': str(self.final_price) if self.final_price is not None else None,
            'match': self.match, 'timestamp': self.timestamp,
            'target_price': str(self.target_price) if self.target_price is not None else None,
            'price_diff': str(self.price_diff) if self.price_diff is not None else None,
            'price_rule': self.price_rule,
            'price_candidates': [
                {'rule': c.rule, 'raw_text': c.raw_text,
                 'value': str(c.value) if c.value is not None else None,
                 'visible': c.visible}
                for c in self.price_candidates
            ],
            'promotion_raw': self.promotion_raw,
            'coupon_pct': str(self.coupon_pct) if self.coupon_pct is not None else None,
            'coupon_amount': str(self.coupon_amount) if self.coupon_amount is not None else None,
            'coupon_final': str(self.coupon_final) if self.coupon_final is not None else None,
            'code_pct': str(self.code_pct) if self.code_pct is not None else None,
            'save_pct': str(self.save_pct) if self.save_pct is not None else None,
            'expected_type': self.expected_type,
            'expected_type_mismatch': self.expected_type_mismatch,
            'attempt_count': self.attempt_count,
            'page_url': self.page_url, 'page_title': self.page_title,
            'duration_ms': self.duration_ms,
            'marketplace': self.marketplace, 'currency_code': self.currency_code,
            'product_url': self.product_url,
            'source_product_url': self.source_product_url,
            'location_verified': self.location_verified,
            'location_context_ready': self.location_context_ready,
            'location_verification_method': self.location_verification_method,
            'risk_cooldown_seconds': self.risk_cooldown_seconds,
            'html_path': self.html_path, 'html_url': self.html_url,
            'html_sha256': self.html_sha256, 'html_size_bytes': self.html_size_bytes,
            'archive_ms': self.archive_ms, 'archive_status': self.archive_status,
            'archive_error': self.archive_error,
            'post_archive_delay_seconds': self.post_archive_delay_seconds,
            'stripped_noncore_css_resources': self.stripped_noncore_css_resources,
            'frontend_checks': self.frontend_checks,
            'frontend_check_rule_version': self.frontend_check_rule_version,
        }
