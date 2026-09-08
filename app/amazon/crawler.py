# -*- coding: utf-8 -*-
"""crawler.py — Amazon 页面抓取（R1.7：按 Marketplace 单活动 tab）

并发模型（用户确认的正确模型）：
    一个 ChromiumPage（浏览器进程）+ N 个 ChromiumTab（标签页），
    多线程各自 acquire/release 一个独立 tab，跨 tab 并发抓取。
    不用多浏览器实例——DrissionPage 4.1.1.4 多实例并发必崩（PageDisconnectedError）。

页面状态判定：page_not_found / sold_out / crawl_error(captcha·blocked·超时·空白) / parse_error
重试策略（方案 17）：404 不重试；sold_out 刷新一次确认；技术错误按 retry 重试；captcha 新 tab 重试 1 次；parse_error 刷新一次。
"""
from __future__ import annotations

import random
import re
import threading
import time
from urllib.parse import urlsplit
from amazon.price_evidence import observed_currency, explicitly_unavailable, Tree
from decimal import Decimal

from DrissionPage import ChromiumOptions, ChromiumPage

from amazon.parser import (
    collect_promotion_raw, parse_code, parse_coupon,
    parse_main_price, parse_save, select_main_price, PromotionEvidenceError,
)
from amazon.selectors import (
    TITLE_404, TITLE_BLOCKED, TITLE_CAPTCHA,
)
from models import CrawlResult, PageStatus, ReportRow
from product_links import MARKETPLACES, MarketplaceProfile

DEFAULT_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')


class AmazonBrowser:
    """单浏览器 + tab 池。

    - 一个 ChromiumPage（一个浏览器进程），预创建 tabs 个标签页；
    - acquire() 取一个空闲 tab（池空时新建），release() 归还；
    - rebuild() 关闭异常 tab 并补新（captcha/断开时用）；
    - cookie 是浏览器级共享（同域），setup 初始化一次即可。
    """

    def __init__(self, headless: bool = True, us_zip: str = '90210',
                 proxy: str | None = None, tabs: int = 1,
                 marketplace: str = 'US', postal_code: str | None = None):
        if marketplace not in MARKETPLACES:
            raise RuntimeError(f'未知 Marketplace: {marketplace}')
        self.profile: MarketplaceProfile = MARKETPLACES[marketplace]
        self.marketplace = marketplace
        self.postal_code = postal_code or us_zip
        self.location_verified = False
        self.location_error = ''
        self.location_verification_method = ''
        self._sleep = time.sleep
        # 运行环境使用固定 VPN；只有显式配置 proxy 时才设置浏览器代理，
        # 不自动继承 HTTP_PROXY/HTTPS_PROXY，避免意外切换出口。
        self.proxy = proxy
        co = ChromiumOptions()
        co.set_argument('--disable-blink-features=AutomationControlled')
        co.set_argument('--start-maximized')
        language = 'en-CA,en' if marketplace == 'CA' else 'en-US,en'
        co.set_argument(f'--lang={language}')
        co.set_argument(f'--user-agent={DEFAULT_UA}')
        if headless:
            co.headless(True)
        if self.proxy:
            # 注意：set_proxy 需要 'host:port' 格式（不带协议和尾斜杠），
            # 否则 Chrome 不生效会加载离线页（dino）
            co.set_proxy(self.proxy)
        self.page = ChromiumPage(co)
        self._lock = threading.Lock()
        self._free: list = []      # 空闲 tab
        self._live: set = set()    # 存活 tab 的 id
        # 一个浏览器进程内预创建受控数量的商品 tab；每个 tab 由一个 worker
        # 独占至解析、归档校验和商品间等待全部完成。
        for _ in range(max(1, int(tabs))):
            tab = self._create_tab()
            self._free.append(tab)
        self._inited = False

    # ---------- tab 池 ----------
    def _create_tab(self):
        """创建一个已登记但尚未放入空闲池的 tab。"""
        try:
            tab = self.page.new_tab('about:blank')
        except Exception:
            time.sleep(2)
            tab = self.page.new_tab('about:blank')
        with self._lock:
            self._live.add(id(tab))
        return tab

    def acquire(self):
        """取一个空闲 tab；池空时创建一个仅归当前调用方持有的 tab。"""
        with self._lock:
            if self._free:
                return self._free.pop()
        return self._create_tab()

    def release(self, tab):
        """归还 tab。断开的 tab 丢弃（下次 acquire 会自动新建）。"""
        with self._lock:
            if id(tab) in self._live and tab not in self._free:
                self._free.append(tab)

    def rebuild(self, tab):
        """关闭异常 tab，返回一个仅归当前调用方持有的新 tab。"""
        with self._lock:
            self._live.discard(id(tab))
        try:
            tab.close()
        except Exception:
            pass
        return self._create_tab()

    # ---------- 初始化 ----------
    def setup(self, strict_location: bool = True) -> bool:
        """初始化三要素（URL + zip cookie），cookie 浏览器级共享，一次即可。失败返回 False"""
        try:
            language = 'en_CA' if self.marketplace == 'CA' else 'en_US'
            self.page.get(f'https://www.{self.profile.domain}/?language={language}', timeout=30, retry=0)
            try:
                self.page.wait.doc_loaded(timeout=15)
            except Exception:
                time.sleep(3)
            for _ in range(3):
                try:
                    self.page.run_js(
                        f"document.cookie = 'sp-cdn={self.postal_code}|M|{self.postal_code}; "
                        f"path=/; domain=.{self.profile.domain}';")
                    break
                except Exception:
                    time.sleep(2)
            # Amazon 地址组件可能在首次打开时仍显示旧状态（例如
            # `Update location`），这属于初始化瞬态，不应要求整轮任务
            # 由调度器重启。有限重试并保留最后一次诊断；连续失败才阻断。
            self.location_verified = False
            for location_attempt in range(3):
                self.location_verified = self._set_postal_code()
                if self.location_verified:
                    break
                if location_attempt < 2:
                    self._sleep(2)
            if strict_location and not self.location_verified:
                print(f'[setup] {self.marketplace} 邮编未能在页面地址栏验证: '
                      f'{self.postal_code}', flush=True)
                return False
            self._sleep(1)
            self._inited = True
            return True
        except Exception as e:
            print(f'[setup] 浏览器初始化失败: {type(e).__name__}: {e}', flush=True)
            return False

    def _set_postal_code(self) -> bool:
        """通过 Amazon 地址弹窗设置邮编，并从导航栏文本回读验证。"""
        try:
            normalize = lambda text: re.sub(r'[^A-Z0-9]', '', text.upper())
            ingress = self.page.ele('css:#glow-ingress-line2')
            observed = (ingress.text if ingress else '') or ''
            if self._postal_matches(observed):
                return True
            trigger = self.page.ele('css:#nav-global-location-popover-link')
            if not trigger:
                self.location_error = 'location_trigger_not_found'
                return False
            self._safe_click(trigger)
            self._sleep(1)
            first = self.page.ele('css:#GLUXZipUpdateInput_0')
            second = self.page.ele('css:#GLUXZipUpdateInput_1')
            if first and second:
                compact = re.sub(r'[^A-Z0-9]', '', self.postal_code.upper())
                if len(compact) != 6:
                    self.location_error = 'ca_postal_must_have_6_characters'
                    return False
                first.input(compact[:3], clear=True)
                second.input(compact[3:], clear=True)
            else:
                field = self.page.ele('css:#GLUXZipUpdateInput')
                if not field:
                    self.location_error = 'postal_input_not_found'
                    return False
                field.input(self.postal_code, clear=True)
            button = self.page.ele('css:#GLUXZipUpdate')
            if not button:
                button = self.page.ele('css:#GLUXZipUpdate-announce')
            if not button:
                self.location_error = 'postal_submit_not_found'
                return False
            self._safe_click(button)
            self._sleep(2)
            confirm = self.page.ele('css:#GLUXConfirmClose')
            if confirm:
                self._safe_click(confirm)
                self._sleep(1)
            ingress = self.page.ele('css:#glow-ingress-line2')
            observed = (ingress.text if ingress else '') or ''
            verified = self._postal_matches(observed)
            if not verified:
                self.location_error = f'postal_not_observed:{observed[:80]}'
            return verified
        except Exception as exc:
            self.location_error = f'{type(exc).__name__}:{str(exc)[:120]}'
            return False

    @staticmethod
    def _safe_click(element) -> None:
        try:
            element.click()
        except Exception:
            element.click(by_js=True)

    def _postal_matches(self, observed: str) -> bool:
        normalize = lambda text: re.sub(r'[^A-Z0-9]', '', text.upper())
        expected = normalize(self.postal_code)
        actual = normalize(observed)
        if expected and expected in actual:
            self.location_verification_method = 'visible_exact'
            return True
        # Amazon.ca 导航栏会将 6 字符邮编最后一位截断为省略符。
        if self.marketplace == 'CA' and len(expected) == 6 and expected[:5] in actual:
            complete_codes = re.findall(r'[A-Z]\d[A-Z]\s*\d[A-Z]\d', observed.upper())
            if complete_codes:
                return expected in {normalize(code) for code in complete_codes}
            self.location_verification_method = 'visible_prefix5'
            return True
        return False

    # ---------- 单次抓取 ----------
    def fetch_once(self, tab, row: ReportRow, cfg: dict) -> CrawlResult:
        t0 = time.time()
        cr = CrawlResult(asin=row.asin)
        cr.expected_type = row.h_type
        cr.marketplace = self.marketplace
        cr.currency_code = self.profile.currency_code
        cr.location_verified = self.location_verified
        url = row.product_url or self.profile.product_url(row.asin)
        cr.product_url = url
        cr.source_product_url = getattr(row, 'source_product_url', '') or ''
        deadline = cfg.get('_deadline', time.monotonic() + float(cfg.get('per_asin_timeout', 90)))
        def budget(limit):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('deadline_exceeded: 单 ASIN 总时间预算耗尽')
            return min(float(limit), remaining)
        try:
            tab.set.timeouts(base=budget(cfg['page_timeout']),
                             page_load=budget(cfg['page_timeout']), script=budget(cfg['page_timeout']))
            # DrissionPage may report a navigation failure by returning False
            # instead of raising.  Never inspect the DOM after that point: the
            # tab can still contain the preceding ASIN and would otherwise
            # produce a false identity_mismatch.
            navigation_ok = tab.get(url, timeout=budget(cfg['page_timeout']), retry=0)
            if navigation_ok is False:
                cr.status = PageStatus.CRAWL_ERROR
                cr.error = 'navigation_failed: tab.get 返回失败'
                return cr
            try:
                loaded = tab.wait.doc_loaded(timeout=budget(cfg['page_timeout']))
            except Exception as exc:
                cr.status = PageStatus.CRAWL_ERROR
                cr.error = f'navigation_timeout: 页面未完成加载 ({type(exc).__name__})'
                return cr
            # Some DrissionPage versions return False on a doc-loaded timeout
            # without raising.  Treat it exactly like an exception.
            if loaded is False:
                cr.status = PageStatus.CRAWL_ERROR
                cr.error = 'navigation_timeout: 页面未完成加载 (doc_loaded=False)'
                return cr
            page_meta = tab.run_js('return {url:location.href,title:document.title};',
                                   timeout=budget(cfg['page_timeout']))
            cr.page_url = page_meta['url']
            cr.page_title = (page_meta['title'] or '').strip()[:200]

            title = cr.page_title.lower()
            if '429' in title or 'too many requests' in title:
                cr.status = PageStatus.CRAWL_ERROR
                cr.error = 'risk_429: 请求频率受限'
                return cr
            if '503' in title or 'service unavailable' in title:
                cr.status = PageStatus.CRAWL_ERROR
                cr.error = 'risk_503: 服务暂时不可用'
                return cr
            # 404
            if any(k in title for k in TITLE_404):
                cr.status = PageStatus.PAGE_NOT_FOUND
                return cr
            # Captcha
            if any(k in title for k in TITLE_CAPTCHA) or 'captcha' in cr.page_url.lower():
                cr.status = PageStatus.CRAWL_ERROR
                cr.error = 'captcha: 机器人验证页'
                return cr
            # 访问受限
            if any(k in title for k in TITLE_BLOCKED) or 'sorry' in title and 'make sure' in title:
                cr.status = PageStatus.CRAWL_ERROR
                cr.error = 'blocked: 访问受限'
                return cr

            # 明确404已优先归类；其余正常商品页若跳转到另一个 ASIN，则页面
            # 价格绝不能归到原 ASIN。查询参数（如 ?th=1）不参与比较。
            identity = re.search(
                r'/(?:dp|gp/product)/([A-Z0-9]{10})(?:[/?#]|$)',
                cr.page_url or '', re.IGNORECASE)
            if not identity or identity.group(1).upper() != row.asin.upper():
                cr.status = PageStatus.IDENTITY_MISMATCH
                cr.error = (f'identity_mismatch: 请求 {row.asin}，最终页面 '
                            f'{identity.group(1).upper() if identity else "unknown"}')
                return cr
            host = (urlsplit(cr.page_url).hostname or '').lower()
            if host not in (self.profile.domain, 'www.' + self.profile.domain):
                cr.status = PageStatus.CURRENCY_ERROR
                cr.currency_code = ''
                cr.error = 'marketplace_mismatch: 最终页面域名与目标站点不一致'
                return cr
            if not self.location_verified:
                cr.status = PageStatus.CRAWL_ERROR
                cr.error = 'location_unverified: 未验证目标邮编，禁止采信价格'
                return cr

            # 等待主价；不额外刷新，所有等待受同一绝对deadline约束。
            try:
                tab.wait.ele_displayed('css:.a-price, css:.priceToPay', timeout=budget(cfg['price_wait_timeout']))
            except Exception:
                pass  # retries navigate once more within the same absolute deadline
            time.sleep(budget(random.uniform(0.5, 1.5)))

            tab.set.timeouts(script=budget(cfg['page_timeout']))
            # One JavaScript turn freezes identity, location and HTML together.
            sample = tab.run_js('''const clone = document.documentElement.cloneNode(true);
                const selector = '[id*="corePrice"],.priceToPay,.apex-pricetopay-value,#buybox,[id*="coupon" i],[class*="coupon" i],.a-alert-content,.a-alert-container,.savingsPercentage,.apex-savings-percentage,#availability';
                const live = document.documentElement.querySelectorAll(selector);
                const copied = clone.querySelectorAll(selector);
                for (let i=0; i<live.length; i++) {
                    if (!live[i].getClientRects().length || getComputedStyle(live[i]).visibility !== 'visible')
                        copied[i].setAttribute('data-price-audit-hidden','true');
                }
                return {url:location.href,title:document.title,
                asin:document.querySelector('#ASIN')?.value || '',
                postal:document.querySelector('#glow-ingress-line2')?.innerText || '',
                html:clone.outerHTML};''', timeout=budget(cfg['page_timeout']))
            budget(cfg['page_timeout'])
            cr.page_url, cr.page_title = sample['url'], sample['title'][:200]
            if any(k in cr.page_title.lower() for k in TITLE_404):
                cr.status = PageStatus.PAGE_NOT_FOUND
                return cr
            identity = re.search(r'/(?:dp|gp/product)/([A-Z0-9]{10})(?:[/?#]|$)', cr.page_url, re.I)
            if (not identity or identity.group(1).upper() != row.asin.upper()
                    or (sample.get('asin') and sample['asin'].upper() != row.asin.upper())):
                cr.status = PageStatus.IDENTITY_MISMATCH
                cr.error = 'identity_mismatch: 读取价格时商品身份已变化'
                return cr
            if (urlsplit(cr.page_url).hostname or '').lower() not in (self.profile.domain, 'www.' + self.profile.domain):
                cr.status = PageStatus.CURRENCY_ERROR
                cr.currency_code = ''
                cr.error = 'marketplace_mismatch: 读取价格时站点已变化'
                return cr
            cr.location_verified = self._postal_matches(sample.get('postal') or '')
            if not cr.location_verified:
                cr.status = PageStatus.CRAWL_ERROR
                cr.error = 'location_unverified: 当前商品页面邮编不匹配'
                return cr
            tree = Tree(sample['html'])
            cands = parse_main_price(tree)
            price, rule, ambiguous = select_main_price(cands, str(cfg['ambiguous_price_ratio']))
            cr.price_candidates = cands

            if ambiguous:
                cr.status = PageStatus.PARSE_ERROR
                cr.error = f'多主价候选冲突: {[(c.rule, str(c.value)) for c in cands if c.value]}'
                cr.display_price = None
                return cr
            if price is None:
                if explicitly_unavailable(tree):
                    cr.status = PageStatus.SOLD_OUT
                else:
                    cr.status = PageStatus.PARSE_ERROR
                    cr.error = 'main_price_missing: 缺少可靠主价格或明确售罄证据'
                return cr
            currencies = {observed_currency(c.raw_text, self.profile.currency_code)
                          for c in cands if c.value is not None}
            if currencies != {self.profile.currency_code}:
                cr.currency_code = next(iter(currencies)) if len(currencies) == 1 else ''
                cr.status = PageStatus.CURRENCY_ERROR
                cr.error = f'currency_mismatch: 主价币种证据 {sorted(currencies)} 与站点不一致'
                return cr

            cr.display_price = price
            cr.price_rule = rule

            # 折扣证据（全部读取，类型决策交给 pricing.compute_result）
            save_pct, raw_save = parse_save(tree)
            code_pct, raw_code = parse_code(tree)
            coupon_pct, coupon_amt, raw_coupon = parse_coupon(tree)
            cr.save_pct = save_pct
            cr.code_pct = code_pct
            cr.coupon_pct = coupon_pct
            cr.coupon_amount = coupon_amt
            cr.promotion_raw = ' | '.join(x for x in (raw_save, raw_code, raw_coupon) if x)[:500]
            cr.status = PageStatus.OK
            return cr
        except PromotionEvidenceError as e:
            cr.status = PageStatus.PARSE_ERROR
            cr.display_price = None
            cr.error = str(e)
            return cr
        except Exception as e:
            cr.status = PageStatus.CRAWL_ERROR
            cr.error = f'{type(e).__name__}: {str(e)[:80]}'
            return cr
        finally:
            if time.monotonic() >= deadline:
                cr.status = PageStatus.CRAWL_ERROR
                cr.display_price = None
                cr.error = 'deadline_exceeded: 单 ASIN 总时间预算耗尽'
            cr.duration_ms = int((time.time() - t0) * 1000)
            if cr.timestamp == '':
                from datetime import datetime
                cr.timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    def _page_looks_normal(self, tab, row: ReportRow) -> bool:
        """页面是否存在：title 非空、URL 含商品页特征、body 含 ASIN"""
        try:
            title = (tab.title or '').strip()
            if not title:
                return False
            if 'captcha' in (tab.url or '').lower():
                return False
            try:
                body = tab.ele('tag:body')
                body_txt = body.text if body else ''
            except Exception:
                body_txt = ''
            if body_txt and row.asin in body_txt:
                return True
            # Currently unavailable 等明确文案也算页面存在
            low = (body_txt or '').lower()
            if any(k in low for k in ('currently unavailable', 'temporarily out of stock')):
                return True
            return '/dp/' in (tab.url or '') or '/gp/product' in (tab.url or '')
        except Exception:
            return False

    # ---------- 带重试的入口（返回 (result, tab)，tab 可能被 rebuild 更换） ----------
    def fetch_with_retry(self, tab, row: ReportRow, cfg: dict) -> tuple[CrawlResult, object]:
        attempts = 0
        last: CrawlResult | None = None
        deadline = time.monotonic() + float(cfg['per_asin_timeout'])
        while True:
            if time.monotonic() >= deadline:
                last = last or CrawlResult(asin=row.asin)
                last.status = PageStatus.CRAWL_ERROR
                last.error = 'deadline_exceeded: 单 ASIN 总时间预算耗尽'
                break
            attempts += 1
            last = self.fetch_once(tab, row, {**cfg, '_deadline': deadline})
            last.attempt_count = attempts
            if time.monotonic() >= deadline:
                last.status = PageStatus.CRAWL_ERROR
                last.display_price = None
                last.error = 'deadline_exceeded: 单 ASIN 总时间预算耗尽'
                break
            st = last.status

            if st in (PageStatus.OK, PageStatus.PAGE_NOT_FOUND):
                break
            if st == PageStatus.SOLD_OUT:
                if attempts == 1:          # 刷新一次确认售罄
                    continue
                break
            if st in (PageStatus.CRAWL_ERROR, PageStatus.IDENTITY_MISMATCH):
                if self.is_risk_result(last):
                    cooldown = random.uniform(cfg['risk_cooldown_min'], cfg['risk_cooldown_max'])
                    # 冷却属于单 ASIN 总预算；不得因 60~180 秒等待突破绝对 deadline。
                    cooldown = min(cooldown, max(0.0, deadline - time.monotonic()))
                    last.risk_cooldown_seconds += cooldown
                    if cooldown > 0:
                        self._sleep(cooldown)
                if attempts <= cfg['retry']:
                    # Navigation/driver failures often leave the tab displaying
                    # the preceding ASIN. Reusing it caused bursts of false
                    # identity_mismatch, especially on the slower CA site.
                    tab = self.rebuild(tab)
                    continue
                break
            if st == PageStatus.PARSE_ERROR:
                if attempts == 1:
                    tab = self.rebuild(tab)
                    continue
                break
            break
        return last, tab

    @staticmethod
    def is_risk_result(result: CrawlResult) -> bool:
        error = (result.error or '').lower()
        return any(key in error for key in ('captcha', 'blocked', 'risk_429', 'risk_503'))

    def wait_after_archive(self, cfg: dict, archive_validated: bool) -> float:
        """兼容旧字段名：商品结束后等待；有HTML时包含归档结束，每商品仅一次。"""
        delay = random.uniform(cfg['post_archive_delay_min'], cfg['post_archive_delay_max'])
        self._sleep(delay)
        return delay

    def quit(self):
        try:
            self.page.quit()
        except Exception:
            pass
