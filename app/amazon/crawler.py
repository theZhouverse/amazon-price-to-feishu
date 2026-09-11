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
from datetime import datetime
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
from frontend_checks import (
    FRONTEND_CHECK_RULE_VERSION,
    inspect_frontend,
    stamp_frontend_checks,
    unknown_checks,
)
from models import CrawlResult, PageStatus, ReportRow
from product_links import MARKETPLACES, MarketplaceProfile

class AmazonBrowser:
    """单浏览器 + tab 池。

    - 一个 ChromiumPage（一个浏览器进程），预创建 tabs 个标签页；
    - acquire() 取一个空闲 tab（池空时新建），release() 归还；
    - rebuild() 关闭异常 tab 并补新（captcha/断开时用）；
    - cookie 是浏览器级共享（同域），setup 初始化一次即可。
    """

    def __init__(self, headless: bool = True, us_zip: str = '90210',
                 proxy: str | None = None, tabs: int = 1,
                 marketplace: str = 'US', postal_code: str | None = None,
                 location_mode: str | None = None,
                 browser_auto_port: bool = True,
                 browser_no_sandbox: bool = True,
                 browser_disable_gpu: bool = True,
                 logger=None):
        self.logger = logger
        self.page = None
        self.browser_startup_stage = 'options'
        self.browser_startup_started = time.monotonic()
        if marketplace not in MARKETPLACES:
            raise RuntimeError(f'未知 Marketplace: {marketplace}')
        self.profile: MarketplaceProfile = MARKETPLACES[marketplace]
        self.marketplace = marketplace
        # US 默认信任已配置的代理/固定 VPN 出口，不再把示例邮编 90210
        # 注入 Amazon 地址组件。CA 默认仍使用独立的加拿大邮编上下文。
        self.location_mode = (location_mode or
                              ('postal' if marketplace == 'CA' else 'proxy')).strip().lower()
        if self.location_mode not in {'proxy', 'postal'}:
            raise RuntimeError(f'未知 location_mode: {self.location_mode}')
        if self.location_mode == 'postal':
            self.postal_code = (postal_code or
                                (us_zip if marketplace == 'US' else '')).strip()
        else:
            self.postal_code = ''
        self.location_verified = False
        self.location_error = ''
        self.location_verification_method = ''
        self._sleep = time.sleep
        # 运行环境使用固定 VPN；只有显式配置 proxy 时才设置浏览器代理，
        # 不自动继承 HTTP_PROXY/HTTPS_PROXY，避免意外切换出口。
        self.proxy = proxy
        co = ChromiumOptions()
        # 旧配置固定 9222 且 auto_port=False；异常退出后会留下孤儿浏览器，
        # 下一轮可能连接到错误会话。每个任务使用受控随机本地端口，避免串会话。
        if browser_auto_port:
            co.auto_port(True, scope=(9222, 19222))
        co.set_argument('--disable-blink-features=AutomationControlled')
        # Chrome 136+（本机当前 152）默认拒绝非浏览器来源的 CDP WebSocket；
        # DrissionPage 4.1.1.4 需要显式放行本机调试连接，否则会在构造
        # ChromiumPage 时收到 403，表现为 PageDisconnected/FrameTree timeout。
        co.set_argument('--remote-allow-origins=*')
        # 当前 Windows 主机的 Chrome 152 在管理员/无头启动时会让 GPU 子进程
        # 崩溃。disable-gpu 避免 GPU 进程，而 no-sandbox 是该主机上唯一已验证
        # 能让 CDP 建立连接的兼容选项；两项均可由配置关闭，便于迁移到已有
        # 沙箱策略的机器。不得再使用已验证会卡住的 --in-process-gpu。
        if browser_disable_gpu:
            co.set_argument('--disable-gpu')
        if browser_no_sandbox:
            co.set_argument('--no-sandbox')
        co.set_argument('--start-maximized')
        language = 'en-CA,en' if marketplace == 'CA' else 'en-US,en'
        co.set_argument(f'--lang={language}')
        # 不覆盖 Chromium 的原生 User-Agent。固定 Chrome/124 会随着本机
        # Chrome 升级而过期；使用浏览器自身 UA 可保持版本同步。
        if headless:
            co.headless(True)
        if self.proxy:
            # 注意：set_proxy 需要 'host:port' 格式（不带协议和尾斜杠），
            # 否则 Chrome 不生效会加载离线页（dino）
            co.set_proxy(self.proxy)
        self._log('info',
                  '启动浏览器 stage=options '
                  f'marketplace={marketplace} headless={headless} '
                  f'auto_port={browser_auto_port} no_sandbox={browser_no_sandbox} '
                  f'disable_gpu={browser_disable_gpu} proxy_configured={bool(self.proxy)} '
                  f'args={self._safe_args(co.arguments)}')
        self.browser_startup_stage = 'cdp_connect'
        try:
            self.page = ChromiumPage(co)
            self.browser_startup_stage = 'connected'
            elapsed_ms = int((time.monotonic() - self.browser_startup_started) * 1000)
            self._log('info',
                      '浏览器启动成功 '
                      f'address={getattr(co, "address", "")} '
                      f'browser_path={getattr(co, "browser_path", "")} '
                      f'version={getattr(getattr(self.page, "browser", None), "version", "")} '
                      f'elapsed_ms={elapsed_ms}')
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - self.browser_startup_started) * 1000)
            self.browser_startup_stage = 'failed'
            self._log('error',
                      '浏览器启动失败 '
                      f'stage=cdp_connect error={type(exc).__name__}: {str(exc)[:240]} '
                      f'elapsed_ms={elapsed_ms} args={self._safe_args(co.arguments)}')
            if self.page is not None:
                try:
                    self.page.quit()
                except Exception:
                    pass
            raise
        self._lock = threading.Lock()
        self._free: list = []      # 空闲 tab
        self._live: set = set()    # 存活 tab 的 id
        # 一个浏览器进程内预创建受控数量的商品 tab；每个 tab 由一个 worker
        # 独占至解析、归档校验和商品间等待全部完成。
        for _ in range(max(1, int(tabs))):
            tab = self._create_tab()
            self._free.append(tab)
        self._inited = False

    def _log(self, level: str, message: str) -> None:
        """把浏览器启动/初始化诊断写入统一运行日志；无 logger 时兼容 CLI。"""
        text = f'[browser] {message}'
        logger = getattr(self, 'logger', None)
        handler = getattr(logger, level, None) if logger is not None else None
        if callable(handler):
            handler(text)
        else:
            print(text, flush=True)

    @staticmethod
    def _safe_args(args) -> str:
        """记录启动参数但隐藏自动生成的用户目录路径。"""
        values = []
        for value in list(args or []):
            value = str(value)
            if value.startswith('--user-data-dir='):
                value = '--user-data-dir=<managed>'
            elif value.startswith('--proxy-server='):
                # Do not put a proxy host, username or password into the
                # persistent run log.  The separate proxy_configured boolean
                # is enough to explain routing; credentials must stay in the
                # runtime environment/configuration secret boundary.
                value = '--proxy-server=<configured>'
            values.append(value)
        return repr(values)

    def _url_matches_request(self, url: str, asin: str) -> bool:
        """Return True only when *url* is the requested ASIN on this marketplace.

        This guard is intentionally independent of DOM state.  DrissionPage may
        report ``get()``/``doc_loaded()`` as False while the browser URL has
        already changed; accepting that state is safe only when both the target
        host and the requested ASIN are visible in the URL.  A blank URL, a
        different ASIN, or a cross-marketplace redirect is rejected so a tab's
        previous page cannot be parsed accidentally.
        """
        if not isinstance(url, str) or not url:
            return False
        host = (urlsplit(url).hostname or '').lower()
        target = getattr(getattr(self, 'profile', None), 'domain', '')
        if host not in {target.lower(), f'www.{target.lower()}'}:
            return False
        identity = re.search(
            r'/(?:dp|gp/product)/([A-Z0-9]{10})(?:[/?#]|$)', url,
            re.IGNORECASE)
        return bool(identity and identity.group(1).upper() == str(asin).upper())

    @staticmethod
    def _is_browser_error_url(url: str) -> bool:
        """识别 Chrome 内置错误页，避免把它归类成 ASIN 身份错配。"""
        return isinstance(url, str) and url.lower().startswith('chrome-error://')

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
        """初始化站点和位置上下文，失败返回 False。

        location_mode=proxy（默认 US）不触碰地址弹窗或邮编 cookie，位置由
        浏览器实际代理/VPN出口决定；location_mode=postal（默认 CA）才设置并
        回读对应站点的邮编。两种模式都仍由商品页最终域名门禁保护。
        """
        self._log('info',
                  f'setup开始 marketplace={self.marketplace} '
                  f'location_mode={getattr(self, "location_mode", "unknown")} '
                  f'address={getattr(self.page, "address", "")}')
        try:
            location_mode = getattr(
                self, 'location_mode',
                'postal' if self.marketplace == 'CA' else 'proxy')
            language = 'en_CA' if self.marketplace == 'CA' else 'en_US'
            navigation_ok = self.page.get(
                f'https://www.{self.profile.domain}/?language={language}',
                timeout=30, retry=0)
            if navigation_ok is False:
                # DrissionPage 4.1.1.4 returns False when its document-load
                # wait expires, even though Page.navigate has already moved
                # the page to the requested Amazon host.  Do not discard a
                # valid session solely because the boolean is False; the
                # host gate below remains authoritative.  An empty/wrong
                # host is still a hard failure and prevents stale-page use.
                observed_url = getattr(self.page, 'url', '') or ''
                observed_host = (urlsplit(observed_url).hostname or '').lower()
                target_hosts = (self.profile.domain, 'www.' + self.profile.domain)
                if observed_host not in target_hosts:
                    self.location_error = 'navigation_failed: marketplace_home'
                    self._log('error',
                              'setup首页导航失败，未到达目标域名 '
                              f'observed_url={observed_url!r} observed_host={observed_host or "unknown"}')
                    return False
                self._log('warning',
                          'setup首页导航返回False但目标域名已到达，继续执行位置/页面校验 '
                          f'observed_url={observed_url!r}')
            try:
                self.page.wait.doc_loaded(timeout=15)
            except Exception:
                time.sleep(3)
            # 首页若被站点区域重定向到另一个 Amazon 域名，先在 setup 阶段
            # 阻断；尤其是 CA 不能把 amazon.com 页面当作加拿大上下文继续用。
            home_url = getattr(self.page, 'url', '')
            if isinstance(home_url, str) and home_url:
                home_host = (urlsplit(home_url).hostname or '').lower()
                if home_host not in (self.profile.domain, 'www.' + self.profile.domain):
                    self.location_error = (
                        f'marketplace_mismatch: 首页域名 {home_host or "unknown"} '
                        f'不是目标 {self.profile.domain}')
                    return False
            if location_mode == 'proxy':
                # 不读取/覆盖 US 地址组件；此处只登记模式。商品抓取时仍会
                # 强制最终页面 host 与目标 Marketplace 一致，代理出口由运行
                # 环境（紫鸟/VPN/显式 proxy）负责。
                self.location_verified = True
                self.location_verification_method = 'proxy_egress'
            else:
                if not getattr(self, 'postal_code', ''):
                    self.location_error = 'postal_missing_for_postal_mode'
                    return False
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
                print(f'[setup] {self.marketplace} 位置上下文未能验证 '
                      f'(mode={location_mode}, value={self.postal_code or "<proxy>"})',
                      flush=True)
                return False
            self._sleep(1)
            self._inited = True
            self._log('info',
                      f'setup成功 marketplace={self.marketplace} '
                      f'location_verified={getattr(self, "location_verified", False)} '
                      f'location_method={getattr(self, "location_verification_method", "") or "none"} '
                      f'page_url={getattr(self.page, "url", "")}')
            return True
        except Exception as e:
            self._log('error',
                      f'setup失败 marketplace={self.marketplace} '
                      f'error={type(e).__name__}: {str(e)[:240]} '
                      f'location_error={getattr(self, "location_error", "")}')
            return False

    def _set_postal_code(self) -> bool:
        """通过 Amazon 地址弹窗设置邮编，并从导航栏文本回读验证。"""
        location_mode = getattr(
            self, 'location_mode',
            'postal' if self.marketplace == 'CA' else 'proxy')
        if location_mode != 'postal' or not getattr(self, 'postal_code', ''):
            self.location_error = 'postal_setup_not_required_in_proxy_mode'
            return location_mode == 'proxy'
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
            # 地址弹窗由 Amazon 异步渲染；在英国/其它默认区域切换到
            # US/CA 邮编时，输入框经常在点击后 1～3 秒才进入 DOM。固定
            # sleep(1) 会把正常慢弹窗误判成 postal_input_not_found。
            first = second = field = None
            for attempt in range(8):
                first = self.page.ele('css:#GLUXZipUpdateInput_0')
                second = self.page.ele('css:#GLUXZipUpdateInput_1')
                field = self.page.ele('css:#GLUXZipUpdateInput')
                if (first and second) or field:
                    break
                if attempt < 7:
                    self._sleep(0.5)
            if first and second:
                compact = re.sub(r'[^A-Z0-9]', '', self.postal_code.upper())
                if len(compact) != 6:
                    self.location_error = 'ca_postal_must_have_6_characters'
                    return False
                first.input(compact[:3], clear=True)
                second.input(compact[3:], clear=True)
            else:
                if not field:
                    self.location_error = 'postal_input_not_found'
                    return False
                field.input(self.postal_code, clear=True)
            button = None
            for attempt in range(6):
                button = self.page.ele('css:#GLUXZipUpdate')
                if not button:
                    button = self.page.ele('css:#GLUXZipUpdate-announce')
                if button:
                    break
                if attempt < 5:
                    self._sleep(0.5)
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
            # instead of raising.  It can mean either a real failure or a
            # document-load timeout after the URL has already changed.  Only
            # continue when the tab URL is already bound to this request's
            # ASIN and Marketplace; otherwise fail closed so a preceding ASIN
            # can never leak into the result.
            navigation_ok = tab.get(url, timeout=budget(cfg['page_timeout']), retry=0)
            if navigation_ok is False:
                observed_url = getattr(tab, 'url', '') or ''
                if not self._url_matches_request(observed_url, row.asin):
                    cr.status = PageStatus.CRAWL_ERROR
                    cr.error = (f'navigation_failed: tab.get 返回失败，当前URL未绑定请求 '
                                f'ASIN/站点 observed_url={observed_url!r}')
                    self._log('warning',
                              f'navigation失败 asin={row.asin} observed_url={observed_url!r}')
                    return cr
                self._log('warning',
                          f'navigation返回False但当前URL已绑定请求 asin={row.asin} '
                          f'observed_url={observed_url!r}，继续DOM门禁')
            try:
                loaded = tab.wait.doc_loaded(timeout=budget(cfg['page_timeout']))
            except Exception as exc:
                cr.status = PageStatus.CRAWL_ERROR
                cr.error = f'navigation_timeout: 页面未完成加载 ({type(exc).__name__})'
                self._log('warning',
                          f'doc_loaded异常 asin={row.asin} '
                          f'error={type(exc).__name__}: {str(exc)[:160]}')
                return cr
            # Some DrissionPage versions return False on a doc-loaded timeout
            # without raising.  If navigation has already reached this ASIN,
            # continue to the shell/identity checks; otherwise fail closed.
            if loaded is False:
                observed_url = getattr(tab, 'url', '') or ''
                if not self._url_matches_request(observed_url, row.asin):
                    cr.status = PageStatus.CRAWL_ERROR
                    cr.error = ('navigation_timeout: 页面未完成加载 (doc_loaded=False)，'
                                f'当前URL未绑定请求 observed_url={observed_url!r}')
                    self._log('warning',
                              f'doc_loaded超时且URL不匹配 asin={row.asin} '
                              f'observed_url={observed_url!r}')
                    return cr
                self._log('warning',
                          f'doc_loaded返回False但URL已绑定请求 asin={row.asin} '
                          f'observed_url={observed_url!r}，继续DOM门禁')
            page_meta = tab.run_js('return {url:location.href,title:document.title};',
                                   timeout=budget(cfg['page_timeout']))
            if not isinstance(page_meta, dict):
                cr.status = PageStatus.CRAWL_ERROR
                cr.error = 'navigation_failed: 无法读取当前页面 URL/title'
                self._log('warning',
                          f'页面元数据无效 asin={row.asin} type={type(page_meta).__name__}')
                return cr
            cr.page_url = str(page_meta.get('url') or '')
            cr.page_title = str(page_meta.get('title') or '').strip()[:200]
            if self._is_browser_error_url(cr.page_url):
                cr.status = PageStatus.CRAWL_ERROR
                cr.error = f'navigation_failed: Chrome错误页 {cr.page_url}'
                self._log('warning',
                          f'导航落入Chrome错误页 asin={row.asin} page_url={cr.page_url!r} '
                          f'title={cr.page_title!r}')
                return cr

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

            # 等待当前商品主体；不能等待全页面任意 `.a-price`，因为推荐卡
            # 通常先渲染，会让等待条件提前满足，随后采到“标题/URL正确但主体
            # 为空”的残缺页面。所有等待受同一绝对 deadline 约束。
            try:
                tab.wait.ele_displayed(
                    'css:#productTitle, css:#landingImage, '
                    'css:#imgTagWrapperId img, '
                    'css:#corePrice_feature_div .a-price, '
                    'css:#corePriceDisplay_desktop_feature_div .a-price, '
                    'css:#corePrice_feature_div .priceToPay, '
                    'css:#corePriceDisplay_desktop_feature_div .priceToPay, '
                    'css:#buybox .a-price, css:#buybox .priceToPay, '
                    'css:#buybox, css:#availability, '
                    'css:#availabilityInsideBuyBox_feature_div',
                    timeout=budget(cfg['price_wait_timeout']))
            except Exception as exc:
                self._log('warning',
                          f'商品主体等待异常 asin={row.asin} '
                          f'error={type(exc).__name__}: {str(exc)[:160]}；交由shell门禁分类')
                pass  # 后续 shell 门禁会明确分类；重试仍受同一 deadline 约束
            time.sleep(budget(random.uniform(0.5, 1.5)))

            tab.set.timeouts(script=budget(cfg['page_timeout']))
            # One JavaScript turn freezes identity, location and HTML together.
            # AC can be painted by a custom element's open Shadow DOM; that
            # subtree is intentionally absent from outerHTML. Capture only a
            # visibility-checked, current-ASIN-bound badge marker so the pure
            # offline parser can consume the same snapshot without guessing.
            sample = tab.run_js('''
                const visible = (el) => {
                    if (!el || el.nodeType !== Node.ELEMENT_NODE) return false;
                    let current = el;
                    while (current) {
                        const cs = getComputedStyle(current);
                        if (cs.display === 'none' || cs.visibility === 'hidden' ||
                            cs.visibility === 'collapse' || Number(cs.opacity) === 0 ||
                            current.getAttribute('aria-hidden') === 'true' ||
                            current.hasAttribute('hidden')) return false;
                        current = current.parentElement ||
                            (current.getRootNode()?.host || null);
                    }
                    return el.getClientRects().length > 0;
                };
                const elements = (root) => {
                    const out = [];
                    const walk = (node) => {
                        if (!node) return;
                        if (node.nodeType === Node.ELEMENT_NODE) {
                            out.push(node);
                            if (node.shadowRoot) walk(node.shadowRoot);
                            for (const child of node.children) walk(child);
                        } else if (node.nodeType === Node.DOCUMENT_FRAGMENT_NODE) {
                            for (const child of node.children || []) walk(child);
                        }
                    };
                    walk(root);
                    return out;
                };
                const visibleText = (root) => {
                    const chunks = [];
                    const walk = (node) => {
                        if (!node) return;
                        if (node.nodeType === Node.TEXT_NODE) {
                            const parent = node.parentElement;
                            if (parent && visible(parent)) chunks.push(node.nodeValue || '');
                            return;
                        }
                        if (node.nodeType !== Node.ELEMENT_NODE &&
                            node.nodeType !== Node.DOCUMENT_FRAGMENT_NODE) return;
                        if (node.nodeType === Node.ELEMENT_NODE && !visible(node)) return;
                        if (node.shadowRoot) walk(node.shadowRoot);
                        for (const child of node.childNodes || []) walk(child);
                    };
                    walk(root);
                    return chunks.join(' ').replace(/\\s+/g, ' ').trim();
                };
                const normalizedChoice = (value) => String(value || '')
                    .replace(/\\s+/g, ' ').trim();
                const choiceRe = /^amazon['’]?s\\s*choice$/i;
                const pageAsin = String(document.querySelector('#ASIN')?.value || '')
                    .toUpperCase().trim();
                let ac_badge = {visible:false, text:'', locator:'', asin:''};
                const acRoots = [...document.querySelectorAll('#acBadge_feature_div')];
                for (const acRoot of acRoots) {
                    const rootAsin = String(acRoot.getAttribute('data-csa-c-asin') ||
                        acRoot.getAttribute('data-asin') || '').toUpperCase().trim();
                    if (!rootAsin || (pageAsin && rootAsin !== pageAsin)) continue;
                    // The class name varies by desktop/mobile experiment. The
                    // bound AC feature container is already a strong scope,
                    // so inspect every visible descendant and use marker
                    // classes only as a diagnostic hint, not as a hard gate.
                    const candidates = elements(acRoot).filter(visible);
                    for (const candidate of candidates) {
                        const labels = [visibleText(candidate),
                            candidate.getAttribute('aria-label') || '']
                            .map(normalizedChoice).filter(Boolean);
                        const text = labels.find((value) => choiceRe.test(value)) || '';
                        if (text) {
                            ac_badge = {visible:true, text:text,
                                locator:'#acBadge_feature_div (live badge)', asin:rootAsin};
                            break;
                        }
                    }
                    if (ac_badge.visible) break;
                }
                const clone = document.documentElement.cloneNode(true);
                // A response can have the correct title/URL while the product
                // detail application never renders (Amazon risk/resource
                // degradation).  Keep a structural diagnostic with the same
                // frozen DOM so the caller can fail fast instead of treating
                // the shell as a normal "price missing" product.
                const visibleNode = (selector) => {
                    const node = document.querySelector(selector);
                    return Boolean(node && visible(node));
                };
                const product_shell = {
                    title: visibleNode('#productTitle,[data-feature-name="title"]'),
                    main_image: visibleNode('#landingImage,#imgTagWrapperId img'),
                    center: visibleNode('#centerCol,#dp-container,#ppd'),
                    buybox: visibleNode('#buybox,#buyBoxAccordion'),
                    // Do not count recommendation-card .a-price nodes as the
                    // current product price; scope candidates to product
                    // detail containers only.
                    price: visibleNode('#corePrice_feature_div .a-price,' +
                                      '#corePriceDisplay_desktop_feature_div .a-price,' +
                                      '.priceToPay,.apex-pricetopay-value,' +
                                      '#buybox .a-price'),
                    availability: visibleNode('#availability,#availabilityInsideBuyBox_feature_div')
                };
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
                html:clone.outerHTML,ac_badge:ac_badge,product_shell:product_shell};''', timeout=budget(cfg['page_timeout']))
            budget(cfg['page_timeout'])
            if not isinstance(sample, dict):
                cr.status = PageStatus.CRAWL_ERROR
                cr.error = 'navigation_failed: 页面快照无效'
                self._log('warning',
                          f'页面快照无效 asin={row.asin} type={type(sample).__name__}')
                return cr
            cr.page_url = str(sample.get('url') or '')
            cr.page_title = str(sample.get('title') or '')[:200]
            if self._is_browser_error_url(cr.page_url):
                cr.status = PageStatus.CRAWL_ERROR
                cr.error = f'navigation_failed: Chrome错误页 {cr.page_url}'
                self._log('warning',
                          f'快照落入Chrome错误页 asin={row.asin} page_url={cr.page_url!r} '
                          f'title={cr.page_title!r}')
                return cr
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
            location_mode = getattr(
                self, 'location_mode',
                'postal' if self.marketplace == 'CA' else 'proxy')
            if location_mode == 'proxy':
                # US proxy 模式不再读取或比较固定邮编；只继承 setup 对实际
                # 浏览器出口模式的确认，最终 amazon.com host 门禁仍在上方。
                cr.location_verified = self.location_verified
            else:
                cr.location_verified = self._postal_matches(sample.get('postal') or '')
            if not cr.location_verified:
                cr.status = PageStatus.CRAWL_ERROR
                cr.error = ('location_unverified: 当前商品页面位置上下文未验证'
                            if location_mode == 'proxy'
                            else 'location_unverified: 当前商品页面邮编不匹配')
                return cr
            shell = sample.get('product_shell') or {}
            # The document title and URL can survive an Amazon risk/resource
            # downgrade while the actual product app is empty.  Require a
            # price/buy-box anchor, or the normal title+image pair, before
            # attempting price parsing; otherwise classify explicitly as a
            # technical incomplete page.
            shell_has_product = bool(shell.get('price') or shell.get('buybox') or
                                     (shell.get('title') and shell.get('main_image')))
            if shell and not shell_has_product:
                cr.status = PageStatus.CRAWL_ERROR
                cr.error = ('incomplete_product_page: 商品详情结构未加载 '
                            f'(shell={shell})')
                return cr
            # Price parsing and all seven frontend checks consume this same
            # frozen DOM. No check is allowed to trigger another navigation.
            cr.frontend_check_rule_version = FRONTEND_CHECK_RULE_VERSION
            try:
                cr.frontend_checks = inspect_frontend(
                    sample['html'], row.size, row.asin,
                    page_ready=True, page_status='ok', page_url=cr.page_url,
                    live_ac_badge=sample.get('ac_badge'))
            except Exception as exc:
                # A selector failure is explicit unknown, never a fabricated
                # pass and never a price crawl failure.
                cr.frontend_checks = unknown_checks(
                    f'前端检查解析失败: {type(exc).__name__}', page_url=cr.page_url)

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
                    cr.error = ('main_price_missing: 缺少可靠主价格或明确售罄证据 '
                                f'(shell={shell})')
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
            self._log('warning', f'促销证据解析失败 asin={row.asin} error={str(e)[:200]}')
            return cr
        except Exception as e:
            cr.status = PageStatus.CRAWL_ERROR
            cr.error = f'{type(e).__name__}: {str(e)[:80]}'
            self._log('warning',
                      f'商品抓取异常 asin={row.asin} '
                      f'error={type(e).__name__}: {str(e)[:200]}')
            return cr
        finally:
            if time.monotonic() >= deadline:
                cr.status = PageStatus.CRAWL_ERROR
                cr.display_price = None
                cr.error = 'deadline_exceeded: 单 ASIN 总时间预算耗尽'
            cr.duration_ms = int((time.time() - t0) * 1000)
            if cr.timestamp == '':
                cr.timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            if not cr.frontend_checks and cr.status.value in {
                    'page_not_found', 'crawl_error', 'identity_mismatch',
                    'currency_error', 'parse_error', 'source_data_invalid'}:
                cr.frontend_checks = unknown_checks(
                    f'页面门禁: {cr.status.value}', page_url=cr.page_url)
            stamp_frontend_checks(cr.frontend_checks, cr.timestamp)

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

            if st == PageStatus.CRAWL_ERROR and (last.error or '').startswith(
                    'incomplete_product_page'):
                # A blank/risk shell is not a normal network retry.  Rebuild
                # once to discard the session's stale document, with a short
                # bounded pause; repeated shells are handled by the caller's
                # circuit breaker instead of spending the full risk cooldown.
                if attempts <= 1:
                    self._sleep(random.uniform(1.0, 3.0))
                    tab = self.rebuild(tab)
                    continue
                break

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
            page = getattr(self, 'page', None)
            if page is not None:
                page.quit()
            self._log('info',
                      f'浏览器已关闭 address={getattr(page, "address", "")} '
                      f'startup_stage={getattr(self, "browser_startup_stage", "unknown")}')
        except Exception:
            pass
