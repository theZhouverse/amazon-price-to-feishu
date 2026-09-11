# TASKS: Amazon Daily

## 2026-09-11 恢复 Windows 周一至周五自动任务（当前运行态）

- [x] 按用户确认启用 `AmazonDaily_0730`、`AmazonDaily_0730_weekday`、`AmazonDaily_1530`、`AmazonDaily_1530_weekday` 四个任务；任务定义未重建、未手动立即触发。
- [x] 管理员回读：四条任务均为 `State=Ready`、`Enabled=True`。周一任务下次运行时间为 `2026-09-14 07:30` 和 `2026-09-14 15:30`；工作日任务下次运行时间为 `2026-09-15 07:30` 和 `2026-09-15 15:30`。
- [x] 四条任务继续通过 `wscript.exe //B //NoLogo` → `bin/hidden_ps1.vbs` → `bin/scheduled_run.ps1` 启动，运行参数分别为 `monday_0730`、`weekday_0730`、`monday_1530`、`weekday_1530`，不会弹出可见终端窗口。
- [x] 启用前已完成代码入口、25项前端专项、361项完整离线测试和一次 `--weekly-run --dry-run --limit 1` 真实只读链路验证；真实 dry-run 仍有页面身份不一致和源数据无效行，程序会阻断并留证，不影响调度器启动。

## 2026-09-11 BSR当前DOM可见性与详情表边界修正（最新）

- [x] 复核历史 `B0DQTFFRCN`：`Best Sellers Rank` 位于当前商品 `#prodDetails` 下的一张 `.a-keyvalue.prodDetTable` 中，同表 `ASIN` 行为 `B0DQTFFRCN`，不是推荐商品或其他 ASIN；但其祖先 `.a-expander-content` 为 `style="display:none"`、`data-expanded="false"`，当前页面实际没有显示 BSR。
- [x] 修正 BSR 规则：精确匹配当前详情表中的 `th.prodDetSectionEntry`，同表 ASIN 行必须等于请求 ASIN；表根为表格时只在最近表格拓扑内找 ASIN，拒绝从嵌套表/推荐卡借用；BSR 行或祖先隐藏时不再判定 `pass`，返回 `fail` 并保留 `[hidden]` 定位和“折叠隐藏”原因。
- [x] 保持 BSR 与 AC 独立：同一当前商品可分别出现可见 AC 徽章和 BSR 字段；只有各自的当前 ASIN、容器和可见性条件满足时才通过，不能互相覆盖为 `ASIN不合法`。
- [x] 规则版本升级为 `2026-09-11-v16`。B0DQTFFRCN 原始历史文件现在应为 BSR=`fail`（折叠隐藏）、AC=`fail`（离线容器为空）；若实时页面在同一绑定容器中动态渲染可见 AC，AC 才可独立为 `pass`。
- [x] 新增隐藏 BSR 回归与嵌套表边界保护；完整离线回归 `361/361` 通过，`compileall` 与 `git diff --check` 通过。该验证仍不替代下一次真实浏览器在线验收。

## 2026-09-11 AC/BSR真实DOM规则修正（最新）

- [x] 按实际商品页 DOM 复核：AC 的有效证据是当前商品 `#acBadge_feature_div` 内可见的 `span.a-size-small`（或同等实际 badge 节点）文本，规范化后精确为 `Amazon's Choice`；隐藏的 `a-popover-preload` 说明、推荐卡、其他 ASIN 和空占位不计入。
- [x] BSR 的有效证据仍是当前商品详情树中的 `<tr>`，其中 `<th class="prodDetSectionEntry">Best Sellers Rank</th>`；详情表必须属于当前请求 ASIN，支持 `ASIN` 行或祖先 `data-csa-c-asin/data-asin` 绑定，导航/推荐/其他 ASIN 的同名文字不计入。
- [x] 移除错误的“同一 ASIN 同时检测到 BSR 和 AC 就两列 `fail`”覆盖。Amazon 正常 DOM 可能同时存在详情表 `Best Sellers Rank` 行和可见 AC 徽章；Q/T 现在分别按各自事实输出，不再将已识别的 AC 改写为 `ASIN不合法`。
- [x] 实时浏览器 AC 证据采集与静态解析规则对齐：页面身份可从 `#ASIN`、`#title_feature_div` 或当前商品 URL取得；AC 容器的 ASIN 绑定支持从自身及祖先 DOM 节点读取，并继续在同一快照递归检查开放 Shadow DOM。
- [x] （历史 v15）新增实际 `a-size-small` 徽章、同页 BSR+AC、隐藏说明和错误 ASIN 回归；BSR 折叠可见性规则已由顶部 v16 补充并覆盖。
- [x] 按项目约定设置 `PYTHONPATH=app;tests` 完整离线回归 `360/360` 通过（unittest 内部约 `2.857s`）；`compileall` 和 `git diff --check` 通过。该验证不替代下一次真实浏览器在线只读验收。
- [ ] 待在线验收：用下一次只读浏览器运行抽取至少一条明确显示 AC 的商品和一条明确仅显示 BSR 的商品，回读 bundle 中 `observed/status/evidence_locator`；未完成前不恢复生产计划任务或写固定结果表。

## 2026-09-11 周报动态子表与可变中间字段兼容（最新）

- [x] 源快照发现改为遍历飞书元数据中的全部子表，保留源表顺序，不再把配置中的旧 `sheets` 列表当作全量上限；因此新增四个业务子表会自动进入本批映射和固定结果表同步。已知 `PD`/`XD`/`PDF` 与 `CPD` 前缀继续分别路由 US/CA；描述性新表名在完整业务表头下可由明确的 US/CA 国家标题或 ASIN 单元格单一 Amazon URL 自动推断，纯 ASIN 无线索或混合站点保持未知并阻断。
- [x] 新增统一源表 schema 解析：`ASIN`、`SKU`、`尺寸/商品尺寸`、`正常售价/正常价格`、`本周折扣形式/本周折扣类型`、`本周折扣%`（含全角百分号）和 `目标成交价/目标价格` 均按规范化表头定位。解析器选取从左到右的第一处业务字段；后段辅助区的重复字段（现有 PD03/PD05 确实存在）只写入 `source_schema.duplicate` 审计，不覆盖主字段。中间插入、移动或追加辅助字段不会改变读取；缺失字段在抓取前明确报“源表字段结构不兼容”，不再使用旧绝对列号兜底。
- [x] 飞书读取按子表实际列容量分段；上传 XLSX 按工作表 `max_column` 读取，修复原先固定读取到 O 列造成的宽表截断。发现报告现在附带 `source_schema.columns/missing/duplicate`，可审计新增子表是否被纳入或为何阻断。
- [x] 固定结果表仍使用现有 A:V 位置和字段顺序；源表新增/移动列只影响 A:G 的字段来源，不创建第二结果表、不改变 H:V。新增回归覆盖“插入中间列仍读对字段”“后段重复表头审计且首列优先”“缺失字段不回退旧列”“四个描述性新表按明确 URL 自动发现”。
- [x] 定向验证：`test_report_reader` 与 `test_weekly_mapping` 共 18 项通过；并用 seq-4 真实快照的 18 份历史表头复核，PD03/PD05 的后段重复字段被正确审计且主字段仍取首列。本轮未执行飞书写入、Amazon 抓取或通知，四条 Windows 计划任务仍保持暂停。
- [x] 完整离线回归：PowerShell 中设置 `$env:PYTHONPATH='app;tests'` 后执行 `.venv\\Scripts\\python.exe -m unittest discover -s tests -p 'test_*.py' -q`，共 `359` 项通过，命令墙钟约 `3.813s`（unittest 内部约 `3.078s`）；`compileall` 和 `git diff --check` 均通过。
- [ ] 在线验收待下一次真实周报更新后完成：只读发现报告需确认实际新增四个子表的标题、Marketplace、schema 完整性和映射顺序；确认固定结果表新增对应子表并逐段回读后，再考虑恢复计划任务。

## 2026-09-11 今日手动全量读取（无邮编 CA，最新）

- [x] 在四条 Windows 计划任务均暂停的状态下，手动按周五下午槽位 `weekday_1530` 启动正式全量：run_id `20260911_154123`，来源 `period_id/source_period_id=seq-4`，`selection_mode=weekday_steady`，未切换周报。
- [x] 18 个价格子表、719 行完成抓取与前端结果处理；写入固定结果表 `base_rows_written=719`、`frontend_checks_written=719`，价格有效写入 `493` 行，`226` 行保留阻断；状态为 `partial`，其中 `ok=493`、`identity_mismatch=193`、`source_data_invalid=29`、`parse_error=4`，没有把异常行写成错误价格。
- [x] 市场站点统计：US 549 行、CA 170 行；CA 全部使用 `direct_no_postal`，日志 setup 记录 `location_context_ready=true`、`location_verified=false`、`location_method=direct_no_postal`，没有邮编弹窗等待。US 继续使用 `proxy_egress`，PD 回归逻辑未改变。
- [x] 固定结果表写入后的逐行回读验证通过（delivery `verified` 全部为 true）；本轮 HTML 归档保持关闭，未新增 HTML 文件。Feedback 按 `weekday_1530` 规则跳过，没有重复采集。
- [x] 一次性汇总通知已发送给 8 名应用协作者，失败 0；通知证据：`outputs/daily_runs/2026-09-11/20260911_154123_notifications.json`。完整本地证据：`outputs/daily_runs/2026-09-11/20260911_154123_weekly_bundle.json`、`20260911_154123_weekly_summary.json`、`20260911_154123_delivery.json`、`outputs/logs/run_20260911_1541.log`。
- [x] 运行总耗时约 `4679.651` 秒（约 77 分 59.651 秒，summary 统计 `4672.719` 秒）；运行结束后无 Chrome/Python 残留进程。Windows 四条任务仍为 `Disabled/Enabled=False`，未因手动运行自动恢复。

## 2026-09-11 CA取消邮编、直接读取页面价格与调度暂停（最新）

- [x] 按用户要求暂停本机全部 Amazon Windows 计划任务：`AmazonDaily_0730`、`AmazonDaily_0730_weekday`、`AmazonDaily_1530`、`AmazonDaily_1530_weekday` 均已回读 `State=Disabled`、`Enabled=False`；任务定义保留，未删除，等待本轮改造完成后再按明确指令恢复。
- [x] CA 位置策略改为 `ca_location_mode=direct_no_postal`：只导航 `www.amazon.ca`，不打开地址弹窗、不写入邮编 Cookie，直接读取当前页面展示价格；`postal` 和 `proxy` 保留为显式兼容模式。
- [x] 抓取结果新增位置上下文区分：无邮编直读记录 `location_context_ready=true`、`location_verified=false`、`location_verification_method=direct_no_postal`；仍强制最终站点、请求/最终 ASIN、页面主体和 CAD 币种门禁，不把无邮编直读冒充邮编验证。
- [x] 统一正式抓取、R1.7 单ASIN PoC、HTML/MHTML工具和缓存恢复读取新模式；新增 `AMAZON_CA_LOCATION_MODE` 运行时覆盖，便于隔离 A/B 测试，不复用通用 HTTP 代理变量。
- [x] 新增无邮编 CA setup/fetch 回归，完整离线套件 `353/353` 通过；Python `compileall`、JSON 解析和 `git diff --check`均通过。尚未恢复定时任务，未写飞书、未发通知。
- [x] CA 无邮编真实只读 PoC：`app/run.py --amazon-poc-marketplace CA --amazon-poc-asin B0BNDLKP54`，显式 `AMAZON_PROXY=127.0.0.1:7897`，总耗时约32.326秒（报告墙钟30.688秒）；不设置邮编，最终 `https://www.amazon.ca/dp/B0BNDLKP54?th=1`，`status=ok`、CAD、展示价69.99、`location_context_ready=true`、`location_verified=false`、`location_verification_method=direct_no_postal`。证据：`outputs/poc_resources/r1_7_ca_B0BNDLKP54.json`、`outputs/logs/run_20260911_1533.log`。
- [x] PD 回归只读 PoC：同一显式代理下 `B0C5R56QTF` 总耗时约36.111秒（报告墙钟约35.0秒），最终 `amazon.com`、USD、`status=ok`、展示价39.99、`location_verification_method=proxy_egress`。证据：`outputs/poc_resources/r1_7_us_B0C5R56QTF.json`、`outputs/logs/run_20260911_1534.log`。
- [x] 完整入口 CPD03 单行 dry-run：run_id `20260911_153634`，显式代理下总耗时约41.443秒；读取飞书登记表后进入 CA 浏览器，`direct_no_postal` setup 成功，最终请求 `B0D9NT9JQN` 跳转 `B0BNDLKP54`，正确记录 `identity_mismatch`、CAD、`location_context_ready=true`，未把落地ASIN价格写回源ASIN。证据：`outputs/daily_runs/2026-09-11/20260911_153634_weekly_bundle.json`、`outputs/logs/run_20260911_1536.log`；首次沙箱网络失败仅发生在飞书只读认证，未启动浏览器，非代码错误。
- [ ] 后续验收：同一 CPD ASIN 分别执行旧 postal 与 `direct_no_postal` 只读对照，记录setup/商品耗时、最终URL/ASIN、CAD价格和页面位置提示；确认价格口径后再决定是否长期保持无邮编模式。

## 2026-09-11 真实浏览器读取复测（最新）

- [x] US 单行只读：原始入口 `app/run.py --weekly-run --dry-run --force-fetch --sheets PD03 --limit 1`，run_id `20260911_144134`，显式 `AMAZON_PROXY=127.0.0.1:7897`，总耗时约58.207秒。`PD03/B0C5R56QTF` 最终 URL 为 `https://www.amazon.com/dp/B0C5R56QTF?th=1`，`status=ok`、USD、展示价39.99、目标价39.99、最终价39.99、位置验证通过、一次成功；未写飞书。证据：`outputs/daily_runs/2026-09-11/20260911_144134_weekly_bundle.json`、`outputs/logs/run_20260911_1441.log`。
- [x] CPD03 源链接只读：同一原始入口，run_id `20260911_144256`，总耗时约313.314秒。请求 `B0D9NT9JQN` 在 `amazon.ca` 最终落到 `B0BNDLKP54`，结果为 `identity_mismatch`、币种CAD；身份门禁生效，没有误写落地 ASIN 价格。证据：`outputs/daily_runs/2026-09-11/20260911_144256_weekly_bundle.json`、`outputs/logs/run_20260911_1442.log`。
- [x] 加拿大落地 ASIN 只读 PoC：`app/run.py --amazon-poc-marketplace CA --amazon-poc-asin B0BNDLKP54`，总耗时约308.458秒（其中位置上下文等待约4分41秒，商品读取耗时23.652秒）。最终 URL 为 `https://www.amazon.ca/dp/B0BNDLKP54?th=1`，`status=ok`、CAD、展示价69.99、加拿大邮编 `M5V 3A8` `visible_exact` 验证通过、currency evidence=true。证据：`outputs/poc_resources/r1_7_ca_B0BNDLKP54.json`、`outputs/logs/run_20260911_1448.log`。
- [x] 三次测试均为只读路径：没有 `--confirm`、没有结果表写入、没有通知发送；测试结束回读 `CHROME_PROCESSES=NONE`。结果说明：浏览器与US/CA读取链路可运行，CPD03剩余问题是源 ASIN 重定向/映射，不应放宽身份门禁。

## 2026-09-11 Docker方案取消、宿主机本地交付（最新）

- [x] 根据最新决策取消 Docker 支持：删除 `deploy/docker/` 下 Dockerfile、Compose、entrypoint 和迁移说明，同时删除 Docker 专用静态测试；不再把容器构建、镜像迁移或容器 cron 纳入交付验收。
- [x] SPEC、操作手册、交付清单和本文件已统一为 Windows 宿主机本地唯一生产路径：`.venv` + 本机 Chromium/DrissionPage + 紫鸟/ZClaw + 隐藏 Windows 计划任务。历史 Docker 验收记录保留在历史段落，仅用于追溯，不代表当前支持矩阵。
- [x] 本机运行时仍支持显式 `AMAZON_PROXY`、`AMAZON_FEEDBACK_ENABLED` 等环境覆盖；这些是宿主机运行参数，不再与 Docker 绑定。
- [x] 删除 Docker 测试后重新执行本地回归：`PYTHONPATH=app; .venv\\Scripts\\python.exe -m unittest discover -s tests -p 'test_*.py' -q`，`351/351`通过，命令墙钟约3.46秒；`compileall`、PowerShell/JSON 解析和 `git diff --check`均通过。未写飞书、未发送外部通知。
- [ ] `20260911_073004` 仍是旧代码/旧位置模式下的 `partial`（价格/前端 `written_rows=0`、`blocked=719`、Feedback写入11行）；修复后的18表全量、固定表写后回读和通知仍需下一次真实计划窗口验收。
- [ ] Windows 计划任务回读当前仍受“拒绝访问”限制，需在 Administrator PowerShell 确认四个本地隐藏任务的 Hidden、Enabled、实际槽位参数，以及15:30是否按运行策略禁用。

## 2026-09-11 浏览器启动兼容、导航门禁与诊断日志（最新）

- [x] 清理本轮残留 Chrome/Edge/Brave/Firefox 进程后再验证；未复用固定9222旧会话。`AmazonBrowser`默认启用受控随机CDP端口（`9222-19222`），启动时使用本机Chromium原生UA，并增加`--remote-allow-origins=*`、`--disable-gpu`、`--no-sandbox`兼容参数；三项由`browser_auto_port`、`browser_disable_gpu`、`browser_no_sandbox`配置控制。
- [x] 启动日志已写入`outputs/logs/run_*.log`：记录options/CDP连接阶段、Marketplace、headless、端口、Chrome版本、脱敏参数、代理是否显式配置、启动耗时、setup位置验证、关闭阶段；不记录Secret、Cookie或完整代理凭证。
- [x] 修复DrissionPage返回值误判：`Page.get()`/`doc_loaded()`返回`False`时，只有当前Tab URL同时匹配目标Marketplace域名和请求ASIN才允许继续后续DOM门禁；空URL、跨站、不同ASIN或`chrome-error://`错误页立即记录`navigation_failed`/`navigation_timeout`并阻断，禁止解析上一商品残留DOM。Chrome错误页单独分类为`crawl_error/navigation_failed`，不再伪装成`identity_mismatch`。
- [x] 新增回归覆盖：setup导航False的目标域名放行/错误域名阻断、Tab导航False的当前ASIN放行/上一ASIN阻断、doc_loaded超时门禁、Chrome错误页分类、异常日志、代理参数脱敏和Feedback运行时布尔开关；删除 Docker 专用测试后完整离线套件为`351/351`通过，`compileall`通过。
- [x] 真实浏览器只读单点复测（均显式使用本机代理`AMAZON_PROXY=127.0.0.1:7897`，未写飞书）：US `B0C5R56QTF` 于`13:28:31-13:29:16`完成，启动耗时1.155s、`status=ok`、USD、价格39.99；CA `B0BNDLKP54` 于`13:29:32-13:30:29`完成，启动耗时1.390s、`status=ok`、CAD、加拿大邮编`M5V 3A8`、价格69.99。证据分别为`outputs/poc_resources/r1_7_us_B0C5R56QTF.json`、`outputs/poc_resources/r1_7_ca_B0BNDLKP54.json`及对应`outputs/logs/run_20260911_1328.log`、`run_20260911_1329.log`；运行结束已确认无残留浏览器进程。
- [x] 无显式Amazon代理的对照运行正确暴露外部网络边界：US商品页落入`chrome-error://chromewebdata/`，日志和报告记录为`navigation_failed`，不是浏览器启动失败。项目仍不静默继承通用`HTTP_PROXY/HTTPS_PROXY`；生产如需该出口必须在`config.proxy`或`AMAZON_PROXY`显式配置，或确认紫鸟/VPN已提供出口。
- [ ] 生产全量仍需在实际计划窗口用目标设备的显式代理/紫鸟出口验收；单点通过不能外推全量风控、CA重定向或飞书写入成功。

## 2026-09-11 US代理位置与CA邮编策略收敛

- [x] 确认问题：旧入口无条件把 `cfg['us_zip']`（默认 `90210`）传给所有US浏览器并设置 `sp-cdn`/地址弹窗；这会把代理出口位置与固定示例邮编耦合，且可能覆盖紫鸟/VPN的真实区域。
- [x] 新增显式位置模式：`us_location_mode=proxy`、`ca_location_mode=postal`。US proxy模式不注入US邮编、不写US `sp-cdn`、不打开地址弹窗，抓取仍强制最终host为 `amazon.com`；CA postal模式只使用 `amazon.ca` 与独立 `M5V 3A8` 页面校验，不把邮编拼入商品URL。
- [x] 代理入口明确化：`config.proxy`或`AMAZON_PROXY`才会给Amazon浏览器设置代理；不自动继承通用`HTTP_PROXY/HTTPS_PROXY`。当前配置 `proxy` 为空，US位置验收必须依赖已生效的紫鸟/VPN出口或显式填入代理。
- [x] 正式抓取、单ASIN PoC、HTML/MHTML工具及配置示例已统一读取上述模式；旧 `us_zip` 仅保留兼容字段，proxy模式不会使用。
- [x] 回归验证：完整离线套件 `344/344` 通过，新增US proxy不写邮编/不读取页面邮编、US无邮编文本仍可解析商品的专门用例；未写飞书、未发送通知。
- [x] 补齐 Windows Chrome 152 启动兼容：增加 `--remote-allow-origins=*`（修复CDP WebSocket 403导致的 `PageDisconnected/FrameTree timeout`）、`--disable-gpu`和`--no-sandbox`（修复当前主机GPU/沙箱启动崩溃），并启用受控auto-port；新增CA首页跳转到amazon.com时的早期域名阻断。详细实测见本文件顶部最新记录。
- [x] 在线单点验收已在显式`AMAZON_PROXY=127.0.0.1:7897`下完成US/CA；代码不能凭空探测代理供应商的公网IP，运行日志以模式、最终域名、币种和页面位置证据为准。生产全量仍待目标计划窗口验收。

## 2026-09-11 Amazon残缺商品页识别与子表熔断修复

- [x] 根据 `20260911_073004` 全量证据确认 US/CA 均返回“正确标题/URL + 顶部导航/推荐卡 + 商品详情主体空白”的残缺页面；后续 A/B 已将根因收敛为自动化固定过期 UA（Chrome/124）叠加地址弹窗异步等待不足，不能再笼统归因于 Amazon 出口，也不是价格公式问题。
- [x] 在同一次冻结 DOM 快照中增加商品详情结构诊断：主价只统计 `corePrice`、`priceToPay`、Buy Box 等当前商品容器，不把推荐卡 `.a-price` 计为主价；缺少商品标题+主图、Buy Box 或当前商品主价时标记 `incomplete_product_page`，保存截图、shell 结构和诊断分类。
- [x] 残缺页仅执行一次轻量 Tab 重建重试，不进入60～180秒风险冷却；同一子表连续8条触发子表熔断，未取行明确写入 `batch_circuit_breaker` 恢复清单，避免再次完整请求数百条空壳页面。
- [x] 配置与示例新增 `incomplete_page_circuit_threshold=8`；等待时间试验已撤回，`price_wait_timeout` 保持12秒。
- [x] 验证：`PYTHONPATH=app; .venv\\Scripts\\python.exe -m unittest discover -s tests -q`，342/342通过；新增残缺商品页 shell 误判、推荐卡等待隔离、慢地址弹窗等待和“仅重建一次、短暂停顿后不进入60～180秒风险冷却”的专门回归用例；US在线单条 `20260911_100819` 和 CA在线单条 `20260911_101439` 均在旧 UA/旧地址等待逻辑下改判为 `crawl_error/incomplete_product_page`，shell=`title:false, main_image:false, center:true, buybox:false, price:false, availability:false`；CA样本 `B0D9NT9JQN` 的 `amazon.ca`、CAD和邮编验证均保留。
- [x] 根因修复与在线复测：移除过期 `Chrome/124` 固定 UA，改用本机 Chromium 原生 UA（当前 `HeadlessChrome/152`）；地址弹窗输入框/按钮增加有限异步等待。修复后同一 `PD03/B0C5R56QTF` 真实复测 run_id `20260911_112315` 成功：最终 URL 自动规范化为 `?th=1`，`status=ok`、展示价/目标价/最终价均 `39.99`、价格一致性 `✅(0.00)`、邮编验证通过；主图、品牌故事、前端尺寸、BSR、父子ASIN、环保标均 `pass`，AC 按页面事实为 `fail`。仅生成本地 bundle/log，未写生产飞书表。
- [x] CA 对照复测：`CPD03/B0D9NT9JQN` 的真实最终页为 `B0BNDLKP54`，run_id `20260911_112549` 正确返回 `identity_mismatch`，没有误写别的 ASIN；随后用落地 ASIN `B0BNDLKP54` 只读复测 run_id `20260911_112722` 成功，`amazon.ca`、CAD、加拿大邮编验证、展示价/目标价/最终价 `69.99`、价格一致 `✅(0.00)`、折扣 `22%` 均正常。证明 CA 主链已恢复，剩余是源 ASIN 重定向需进入人工/恢复清单。
- [x] 追加一次真实浏览器只读单点：通过 `tools/frontend_online_test.py` 内部原始入口执行 `PD03` 1 行，run_id `20260911_105130`，实际访问 `https://www.amazon.com/dp/B0C5R56QTF`，Chromium 导航、US 邮编和 USD 均到位；最终页面标题/URL正确但商品主体为空，结果明确为 `crawl_error/incomplete_product_page`，尝试2次、风险冷却0秒、没有主价候选，未写任何飞书表。截图、诊断、bundle和原始日志已保存；因主命令默认会创建隔离测试表，本次只调用同文件的在线抓取 helper，避免未经确认的云端写入。


## 2026-09-11 明早生产运行前完整检查与调度修复（最新）

- [x] 发现实际Windows入口仍只有两个“周一至周五”任务，且`hidden_ps1.vbs`不转发附加参数、`scheduled_run.ps1`未传`--scheduled-slot`；历史07:30任务因此以`manual`模式运行，2026-09-10 07:30 summary中的`source_period_id/scheduled_slot/selection_mode`为空，Feedback也会因非07:30逻辑槽位被跳过。
- [x] 修复完整参数链：四条任务分别传入`monday_0730`、`weekday_0730`、`monday_1530`、`weekday_1530`；VBS逐项转发参数，PowerShell把槽位写入START日志并传给`app/main.py`。`StartWhenAvailable`延迟补跑不再依赖实际启动时钟判断来源。
- [x] 重新安装并只读回查四条任务：`AmazonDaily_0730`仅周一、`AmazonDaily_0730_weekday`仅周二至周五；两者`Enabled=True`、`Hidden=True`、`Execute=wscript.exe`。明早2026-09-11 07:30由weekday任务运行。两条15:30任务继续保持`Enabled=False`，没有恢复下午执行。
- [x] 已过期的`AmazonDaily_20260826_0700`一次性任务无未来触发时间，但仍走可见BAT；已将其禁用，未删除历史任务记录。
- [x] 调度日志统一设置PowerShell控制台输出和Python为UTF-8，避免旧scheduler log中的中文乱码；manifest新增顶层`source_period_id`并同步最终Feedback统计，恢复和通知可直接审计同一来源/时段/Feedback状态。
- [x] 飞书登记表只读预检通过：扫描199行、有效链接4条、当前唯一序号4、当前登记行5；未执行飞书写入。配置JSON与example顶层键完全一致，Feedback已启用、两个店铺配置及状态账本完整，HTML归档和HTML服务保持关闭。
- [x] 正式同入口单条实时只读预跑`20260911_001435`通过：`PD03/B0C5R56QTF`、`period=seq-4`、`source_period_id=seq-4`、`scheduled_slot=weekday_1530`、`selection_mode=weekday_steady`、`status=ok`、`USD`、邮编验证通过，BSR=`pass`、AC=`fail`、前端规则v14；耗时51.547秒，未写飞书、未发送通知。
- [x] 加拿大站同入口单条实时只读预跑`20260911_002057`通过：`CPD03/B0BNDLKP54`落地`https://www.amazon.ca/dp/B0BNDLKP54?th=1`，`status=ok`、`marketplace=CA`、`CAD`、加拿大位置验证通过，BSR=`pass`、AC=`fail`、前端规则v14；耗时46.157秒，未写飞书、未发送通知。该样本证明CA主链可用，不代表其它重定向ASIN已修复。
- [x] 最终验证：338项unittest全部通过；Python `compileall`、三份PowerShell脚本语法、两份JSON配置解析、`git diff --check`均通过；真实VBS参数转发探针退出码0并已删除临时探针文件。
- [x] 本轮16个代码、测试和关联文档文件已固定为本地提交`29d4a3c`（`fix: harden frontend rules and scheduled slots`）；当前未推送远程，不把本地提交误报为GitHub已更新。
- [ ] 2026-09-11 07:30真实全量仍是最终在线验收：需核对scheduler START/END与UTF-8、`weekday_0730/weekday_steady/seq-4`、18表写入/阻断、CA身份异常、Feedback增量3日/10日留存、固定表回读和全员通知。计划任务存在与单条预跑不能替代该结果。

## 2026-09-10 BSR/AC规则按开发SPEC重新对齐（历史 v14，已被 v15 替代）

- [x] 对照 `D:\projects\amazon_daily_dev_20260821\docs\SPEC.md` 第5节和第6.1节，确认BSR与Amazon's Choice均需先绑定当前请求ASIN；BSR只读取当前商品详情表中的精确 `Best Sellers Rank` 字段，AC只读取当前商品作用域内的可见实际徽章。
- [x] （历史 v14）曾恢复同一当前ASIN同时出现BSR和AC时的业务唯一性门禁；该规则已被顶部 `2026-09-11-v16` DOM复核撤回，因为正常商品详情表可与AC徽章同时存在。推荐卡、隐藏说明、其他ASIN和无法绑定的证据仍不计入。
- [x] 保留现代页面 AC 的可见 Shadow DOM 递归采集及多节点文本拼接能力，避免规则对齐回退为“只读 outerHTML”；规则版本升级为 `2026-09-10-v14`，旧版 v13 bundle 不在新规则下重新解释。
- [x] 更新生产 `SPEC`、`README`、`REVIEWS` 与本节记录，补充同ASIN双标志冲突的离线回归；未读取或写入飞书表格，未改变结果表列顺序。

## 2026-09-10 全量重跑、Feedback店铺字段与结果表顺序收口（最新）

- [x] 以隐藏窗口执行一次完整生产批次：`app/main.py --weekly-run --confirm --scheduled-slot weekday_0730`，run_id `20260910_160228`，来源周期 `seq-4`、模式 `weekday_steady`；18 个业务子表（US 11、CA 7）全部完成实时抓取，抓取阶段耗时 `4847.703s`（约80.80分钟）。
- [x] 首次发布在表头预检处安全停止：固定结果表仍是旧版22列表头，未发生商品半批写入；随后仅迁移18个结果子表第2行表头（先生成18份本地备份、逐表回读），改为当前 `RESULT_HEADERS` 顺序：`A:G` 基础字段、`H:M` 价格/币种、`N:T` 商品主图/品牌故事/前端尺寸/BSR/父ASIN发散/环保标/AC标、`U:V` 时间戳/Amazon链接。
- [x] 用同一 run 的已验证快照执行 `--weekly-push-only --run-id 20260910_160228 --confirm`，不重抓、不混周期；固定结果表基础字段同步 `719` 行，前端/价格结果覆盖 `719` 行，其中 `489` 行通过写入、`230` 行因 `identity_mismatch`、`parse_error` 或 `source_data_invalid` 阻断并保留恢复清单。最终发布证据以 `outputs/daily_runs/2026-09-10/20260910_160228_weekly_push.json` 和同 run `delivery.json` 为准；首次 `weekly_summary.json` 的 `written_rows=0` 是旧表头门禁阶段的中间证据，不代表恢复发布结果。
- [x] Feedback 按两个店铺串行执行增量3日窗口（`2026-09-08`～`2026-09-10`）：原始读取 `80` 条，窗口内新增 `2` 条，合并后固定子表共 `10` 条，二级详情完整数 `2`（新增记录），两店状态均为 `ok`，阶段耗时 `159.610s`；可见“店铺”列只写配置显示名“冬豚”“北蓉”，内部 `store_a/store_b` 未泄漏。
- [x] 固定 `Feedback差评汇总`（Sheet ID `41u25y`）严格回读 `A1:I11` 九列顺序：`店铺、日期、评级、订单编号、评论、订单商品编号、ASIN、SKU、获取时间戳`；目标子表已置于最后。经全范围空值确认，旧 `Feedback????`（`3lCGeQ`）和默认空白 `Sheet1`（`f5aa85`）均已删除。
- [x] 最终只读验收：18/18 商品子表新版 A:V 表头、行数、ASIN 集合、源快照有效行顺序和 H:V 覆盖均通过；Feedback 9 列、10 行、店铺显示名、末尾位置均通过；通知回执 `20260910_160228_notifications.json` 记录8名协作者成功、0失败。HTML 归档/服务保持关闭，既有 `htmls` 历史文件未删除。

## 2026-09-10 AC标识证据增强与15:30任务临时暂停（最新）

- [x] 根据用户提供的页面样式线索复核 AC 徽章结构；样式文本本身不作为业务证据。针对 Amazon 现代页面将可见 AC 徽章可能位于开放 Shadow DOM、或由 `.mvt-ac-badge-*` / `.ac-badge-*` 多个节点拼接的情况，浏览器快照同一轮递归采集可见徽章文本，并强制校验 `#acBadge_feature_div` 的当前 ASIN 绑定。
- [x] AC 证据仍排除 `a-popover-preload`、`aria-hidden`、不可见节点、推荐/赞助/其他 ASIN 区域；无法同时证明可见、文本精确为 `Amazon's Choice` 且属于请求 ASIN 时保持 `❌` 或页面门禁下的 `-`，不把 CSS 样式或隐藏解释文案当作存在。
- [x] 新增离线回归：现代徽章文本拆分、Shadow DOM 证据绑定及错误 ASIN 拒绝；前端规则版本已由 v13 升级为当前 `2026-09-10-v14`，新增同ASIN双标志冲突回归。
- [x] 完整回归：`338` 项 unittest 全部通过，`compileall` 与 `git diff --check` 通过；其中新增旧前端规则缓存/weekly bundle 拒绝回归。
- [x] 按用户要求仅禁用 Windows 计划任务 `\\AmazonDaily_1530`；读回 `Enabled=false`。`AmazonDaily_0730` 未修改。此为运行态临时暂停，调度脚本中的工作日15:30定义保留，恢复前不得误报下午任务已执行。
- [x] 本轮不读取或写入生产固定结果表；AC代码、测试和文档变更仍在当前修复分支，待恢复15:30前再做一次隔离样本验收。

## 2026-09-10 测试表字段、Feedback顺序与前端标志规则更新（此前记录）

- [x] 仅在用户指定的隔离测试表 [GA6PsnlcjhTGsqtBocdcVct7n2e](https://wit0jhu6kvu.feishu.cn/sheets/GA6PsnlcjhTGsqtBocdcVct7n2e?sheet=JMa2c) 执行云端结构调整；固定生产结果表 `Epads8MQkhkuBctjl3lcqLUvnCg` 未读取写入、未改名、未删除子表。
- [x] 测试表18个业务子表（PD/XD/CPD/PDF）逐表读取 `A2:V2` 并确认22列表头已完全匹配当前 `RESULT_HEADERS`：L=`价格一致性`、N=`商品主图`、O=`品牌故事`、P=`前端尺寸`、Q=`BSR`、R=`父ASIN发散`、S=`环保标`、T=`AC标`；不存在HTML列。
- [x] 用户确认将P列可见标题由`前端尺寸是否一致`简化为`前端尺寸`。仅调整列头文案，尺寸一致性判定、内部键`size_consistent`、bundle证据及A:V列位置保持不变；已同步代码、SPEC、REVIEWS和本测试表并完成写后读回。
- [x] 对测试表完全空白的 `Sheet1`（原ID `b82298`）执行整表空值预检后删除；未发现名为 `Feedback????` 的遗留子表，因此没有扩大删除范围。
- [x] 测试表新增固定9列表头的 `Feedback差评汇总`（Sheet ID `49WYs8`），位置读回为最后一个子表（index 18）；本次没有伪造反馈业务行，等待后续正式Feedback任务写入。
- [x] 代码将 `store_a`/`store_b` 保留为内部采集键，Feedback可见“店铺”列改由配置显示名输出：`冬豚`、`北蓉`；幂等键仍稳定，重复运行不会因显示名转换产生重复行。
- [x] 发布器兼容旧结果：若历史9列表中仍残留可见值 `store_a`/`store_b`，写入前在内存中转换为配置显示名并重建同一幂等键，避免升级后的首次运行重复追加；内部键不泄漏到可见列。
- [x] （此前记录）前端规则版本从 `2026-09-10-v11` 升至 `2026-09-10-v12`。BSR只接受当前请求ASIN绑定的商品详情表；AC只接受当前ASIN绑定、可见且文本精确为 `Amazon's Choice` 的商品badge，并排除推荐/隐藏节点；BSR与AC改为相互独立的存在性检查，不再因同一DOM同时出现两者把ASIN判为非法；该规则随后由本文件顶部的 v13 Shadow DOM 证据增强取代。
- [x] 离线回归：完整 `unittest` 套件共 `334` 项通过（退出码0）；新增推荐卡AC排除、B0DQTFFRCN当前商品AC回放、旧店铺占位名迁移和真实显示店铺名/幂等回读覆盖，规则变更前后均无失败。

云端核验记录：18/18业务表 `header_ok=true`；Feedback `A1:I1`严格9列、`index=18`；剩余子表标题共19个，顺序末尾为 `Feedback差评汇总`。本节为测试表结构变更证据，不代表已运行一次新的全量价格或Feedback采集。

## 2026-09-10 生产全量运行与 Feedback 首次发布（最新状态）

- [x] 生产工作区与 GitHub `origin/fix-codescan-20260826` 保持同分支同步（运行时合并基线 `ffcd6ea`）；运行前仅在生产配置开启 `feedback.enabled`，未复制或提交 Secret。
- [x] 以隐藏窗口执行 `app/main.py --weekly-run --confirm --scheduled-slot weekday_0730`，运行 `20260910_121408`，周期 `seq-4`、模式 `weekday_steady`；价格 18 个子表均完成抓取，固定结果表基础 A:G 同步 719 行、H:V 写入 489 行、阻断 230 行，批次明确记为 `partial`。
- [x] CA 7 个 CPD 子表已包含在同一批次并完成实时抓取，结果中保留 `CAD=170` 与身份门禁阻断证据；不得把 CA 阻断误报为成功。
- [x] Feedback 两店串行首次 7 日窗口（`2026-09-04`～`2026-09-10`）完成：两店各 2 页、原始 80 条、评级 1–3 星窗口内 10 条、二级详情 10/10 完整、两店状态 `ok`，阶段耗时 390.344 秒。
- [x] 固定结果 Spreadsheet `Epads8MQkhkuBctjl3lcqLUvnCg` 的 `Feedback差评汇总`（`41u25y`）写入 10 条，`A1:I11` 写后整表回读通过；状态账本已推进。空白 `Feedback????`（`3lCGeQ`）不使用。
- [x] 统一通知一次性发送给 8 名应用协作者，成功 8、失败 0；完整墙钟耗时 5858.296 秒（约 97.64 分钟）。
- [ ] 后续验收：在下一次工作日 07:30 运行确认 `incremental_3d` 窗口、10 日留存清理与重复合并；继续跟踪 CA `identity_mismatch`，必要时按 bundle 逐条优化导航，不放宽身份门禁。
- [ ] HTML 归档/局域网服务仍按当前要求关闭；保留既有 `htmls` 历史文件，不在价格任务中重新下载 HTML。

证据：`outputs/daily_runs/2026-09-10/20260910_121408_weekly_bundle.json`、`20260910_121408_weekly_summary.json`、`20260910_121408_delivery.json`、`20260910_121408_notifications.json`、`outputs/feedback/20260910_121408/`。

## 2026-09-10 飞书新资源统一管理权限规则

> 本节记录当前有效规则；历史记录中“只给快照授权”“固定结果表保留既有权限”等旧口径仅作为当时证据，不覆盖本节。规则针对本开发项目使用的当前 Feishu 应用，未默认批量改历史云端文件。

- [x] 当前应用身份核验：使用项目凭证换取 tenant token 后，只读查询应用通讯录，确认“周成业”对应当前应用维度 `open_id` 为配置中的 `ou_68e7d2af96255c1eeb1eea8021c80ea4`；未使用 BDLD 或 lark-cli 的跨应用人员 ID。
- [x] 统一策略配置：新资源自动授权开启，成员类型固定 `openid`，权限固定 `full_access`（管理权限）；Secret、tenant token 和 Authorization 不写入代码、日志或配置文件。
- [x] 创建/复制入口接入：`FeishuClient.create_spreadsheet()` 与通用 `copy_file()` 在拿到资源 token 后自动调用统一授权入口；返回对象附带脱敏的授权结果，调用方失败时不能继续标记 ready。
- [x] 授权证据增强：先读取成员列表，缺失时写入，再重新读取并确认目标 `member_id/member_type/perm`；支持成员权限列表分页，回读失败或权限不一致时 fail-closed。
- [ ] 历史资源补授权：未执行。若要处理已有表格、文件或文件夹，需要另行给出资源清单和“逐项补授权”确认，不从本次新资源规则推断历史资源已完成。
- [ ] 真实新建/复制资源验收：待下一次明确的隔离测试资源创建后，用当前应用回读资源权限并保留 token、资源类型、成员权限和时间证据；不把本地单元测试当成云端验收。

## 2026-09-08 生产目录与开发目录隔离

> 生产根目录正在被Windows任务计划程序使用；本节用于防止后续本地改动直接影响正在运行的代码。

- [x] 发现并确认`D:\projects\amazon_daily`是指向生产根目录`D:\projects\amazon_daily_structured_20260821`的Windows Junction，不是独立副本；以后禁止在该路径开发。
- [x] 创建独立开发副本`D:\projects\amazon_daily_dev_20260821`，基于当前Git工作区复制代码和文档，排除生产`.env`、`outputs`、`htmls`、`data`、`tmp`、`.venv`、`.workbuddy`和`.codex`；开发副本未安装Windows计划任务。
- [x] 开发副本为调度入口增加稳定的`scheduled_slot`参数/环境变量，不能用实际启动时间推断周一07:30与周一15:30；离线覆盖StartWhenAvailable延迟补跑和人工启动场景；四条Windows任务已于2026-09-11在生产根目录重新安装并按本文件顶部记录回读。
- [ ] 增加发布前真实路径校验：解析开发路径、生产路径和Junction/符号链接，路径相同或解析后相同必须拒绝；发布白名单不得包含`.env`、`outputs`、`htmls`、`data`、`.venv`。
- [ ] 增加生产发布脚本和回滚证据：取得运行锁、确认没有价格进程、备份到`outputs/code_backups/{release_id}`、复制并校验代码文件、保留生产运行产物，发布失败自动停止而不是覆盖运行状态。
- [ ] 开发副本先完成离线回归和只读飞书检查；通过后再人工批准发布到生产目录。开发副本不得执行正式`--weekly-run --confirm`、不得安装/修改`AmazonDaily_0730/1530`。
- [ ] 为正式`weekly-run`与旧兼容入口增加布局路由门禁：日常任务只能调用A:O兼容入口或A:V发布器，旧`sync_base_data`/`write_six_columns`不得写入固定结果表；补充误调用回归并在发布前回读表头。

## 2026-09-08 换周时点：周一早间沿用上周、周一下午切换本周

> 本节已将用户确认的时段规则写入SPEC；代码和真实调度验收尚未因文档更新而自动完成。实现前不得把当前入口仍按“每次选择最新链接”的行为描述为已符合本规则。

- [x] 明确业务规则：周一07:30使用上一周已经固化的`period_id`/manifest/完整快照；周一15:30重新读取登记表并切换到更高的最新有效序号；周二至周五07:30/15:30沿用周一15:30确认的本周周期。
- [x] 更新`docs/SPEC.md`第1、1.1、16、16.1、18.1、19.2、19.3节，补充来源选择、快照创建/复用、失败回退和通知字段规则。
- [x] 开发副本增加时段感知的来源选择器，输出`source_period_id`、`selection_mode`（`monday_carryover`/`monday_switch`/`weekday_steady`）和登记行号；周一07:30禁止选择最新本周链接；真实云端双时段仍待验收。
- [x] 调度器向Python传入稳定的`scheduled_slot`（`monday_0730`/`monday_1530`/工作日槽位），补跑保持原计划槽位；人工运行标记`manual`；Windows四任务已于2026-09-11重装并回读参数。
- [ ] 周一07:30实现上一周期manifest/快照存在性、结构和权限校验；缺失或不可读时安全停止，不读取可变原表，不回退到任意旧周期。
- [ ] 周一15:30要求登记表出现比上一周期更高的有效序号并完成源Token、完整副本、结构校验和manifest固化；无新序号、复制504/超时、权限或结构失败时保留上一周固定结果并通知周成业。
- [ ] 周二至周五保持本周周期不变；发现新的更高序号或同序号换URL时只登记`pending_period_change`并告警，不在非换周时点静默切换。
- [x] 更新运行通知、weekly bundle、summary、manifest和日志，明确记录执行时段、`source_period_id`、`selection_mode`、源快照Token（脱敏）及选择原因；2026-09-11补齐manifest顶层来源字段和Feedback最终状态，真实周一早晚通知文案仍纳入下方跨周在线验收。
- [ ] 增加离线回归：登记表已有新旧两行时周一07:30选旧、周一15:30选新；无新行时下午安全停止；周二发现新行不自动切换；同序号换URL拒绝；断点恢复不改变已固化来源。
- [ ] 完成真实验收：至少覆盖一个周一早间和同日周一下午批次、随后一个工作日早晚批次，分别记录run_id、source_period_id、selection_mode、固定表写入行数、阻断行数和完整耗时；通过后再将本节实现项勾选完成。

## 2026-09-08 新增需求：前端检查与Feedback差评子任务

> 本节是实施任务清单，SPEC中的新增内容目前只代表目标规格，不代表代码已经实现或云端已经验收。

### 最新开发/只读验收进度（2026-09-10）

- [x] 修复真实紫鸟 CLI `.cmd` 多行脚本截断、中文页面常量编码失真、CLI 更新通知/评论签名风险误报、详情返回不生效和Shadow DOM空节点异常；加入列表/详情有界渲染重试、分页上限前置停止和详情后按页码恢复，并补充回归测试。
- [x] 真实单条详情—返回探针通过：详情字段可回读，重新访问 Feedback Manager 后 marker、20行当前页和唯一分页按钮恢复正常；未写入飞书。
- [ ] 冬豚首次7日只读探针已启动但因历史分页较长主动停止，当前只能记为`blocked/未完成`；北蓉正式流程、两店完整窗口、业务行写入和状态推进继续保持未执行。
- [x] 固定 Spreadsheet `Epads8MQkhkuBctjl3lcqLUvnCg` 已创建唯一子表 `Feedback差评汇总`，Sheet ID `41u25y` 已登记到 `config/config.json`；`A1:I1` 回读与严格9列表头完全匹配。本次业务数据写入数为0，模块仍保持禁用。
- [x] 两个店铺都完成单条低星详情只读复核：详情标记、订单路由身份、订单商品编号、ASIN、SKU均可回读；无登录、验证码或风控信号，且每个店铺完成后关闭上下文。
- [x] 详情选择器登记为`text:订单内容`、`data-test-id:order-id-label`、`label:订单商品编号`、`label:ASIN`、`label:SKU`；标签值去除展示冒号，订单号支持详情路由回退并继续做身份比对。
- [x] 真实页面确认分页控件是唯一可见`kat-link`文本`下一个 >`，内部链接位于Shadow DOM；代码按前缀识别并在“有数据但无分页控件”时fail-close，新增离线回归覆盖该阻断。
- [x] 增加`page_date_order=newest_first`和安全日期边界：页内日期缺失/排序异常阻断，整页早于窗口起点时停止历史分页；增加探针专用`max_detail_attempts`，默认0不限制生产详情读取，探针达到上限时安全关闭，不推进状态。
- [x] 2026-09-10 受限环境和正常Windows权限分别核验紫鸟CLI：受限环境Keychain不可见；正常用户权限下项目级CLI `doctor`、Keychain、Bridge、客户端登录和两店列表通过。真实结构诊断回读20行当前页、两个标记候选和无登录信号；修复可见标记过滤后单店探针进入详情链路，但因探针详情上限主动阻断，未计为正式成功。
- [x] Feedback定向离线回归已扩展为30项，全部通过；没有写入业务行，`feedback.enabled=false`保持。
- [x] 2026-09-10 两店各一页容量只读探针通过：两店均20行、低星候选各6行，日期范围和分页按钮状态已记录，单页耗时约22秒；未点击订单、未翻页、未写表。
- [ ] 2026-09-10 整条写入验收被飞书凭证门禁阻断：当前开发副本未提供`FS_APP_SECRET`，认证返回`invalid param`；不得自动复用其他项目`.env`中的Secret。待用户提供当前项目授权的飞书环境变量/凭证入口后，继续首次7日双店写入回读、状态推进、3日增量、10日清理和07:30任务验收。
- [x] 2026-09-10 首次7天双店真实只读完成：`feedback_readonly_full_20260910`，窗口`2026-09-04`至`2026-09-10`，两店各2页/40行，窗口内4+6条，详情10/10完整，边界页均为2，总耗时390.156秒；无写入、无状态推进。
- [x] 2026-09-10 后续3天双店真实只读完成：`feedback_readonly_incremental_20260910`，窗口`2026-09-08`至`2026-09-10`，冬豚2页/40行/2条/详情2/2，北蓉1页/20行/0条，总耗时142.234秒；无写入、无状态推进。
- [x] 2026-09-10 本地发布替身验证通过：首次10条写入、同输入幂等重跑、1条过期记录清理、9列写后回读和内存状态推进均通过；真实`Feedback差评汇总`只读确认表头正确、实际业务行0。
- [x] 2026-09-10 按用户授权从生产目录凭证入口完成飞书只读认证和固定目标回读：`Feedback差评汇总`表头精确匹配、实际非空业务行0；Secret未复制/落盘，未备份、清空、写入生产表，未推进状态。
- [x] 全局紫鸟 CLI 已在用户授权后完成只读连通性复核：`doctor`、Keychain/API认证、ZClaw Bridge、客户端登录状态和`store list --all`均通过；确认两店为`26782671389969`（冬豚）和`26686718929338`（北蓉）。
- [x] 两店已分别打开Seller Central并到达准确地址`https://sellercentral.amazon.com/feedback-manager/index.html`；两店都确认【最新反馈】、固定5列表头和20条当前页数据，未出现登录、验证码或风控信号。基础`/feedback-manager`空壳地址已列为不可用来源。
- [x] 已登记主表真实选择器并适配Amazon KAT组件：星级从`kat-star-rating[value]`读取，订单号从`kat-link`及其Shadow DOM链接读取，点击目标使用唯一订单链接；代码改用`domcontentloaded`加有界等待，避免Feedback SPA的`networkidle`不收口。
- [x] 受控单条低星详情探测已成功进入订单详情路由；只读确认详情页有`订单商品编号`、`ASIN`、`SKU`标签及订单身份回退路径，详情配置已登记，但尚不能据此勾选F4/F10。
- [ ] 下一步仍需完成两店正式多页/首次7日只读、二级详情批量回读、9列业务行写后整表回读、近10日清理和07:30任务验收；在此之前保持`feedback.enabled=false`，不写入业务行、不安装独立计划任务。

- [x] 在开发副本完成商品结果表N:T七个固定前端列、U/V时间戳和Amazon链接的本地布局模型；前端列显示`✅`/`❌`/`-`，bundle保留原始状态。旧A:P/A:O迁移、写前备份、写后整行回读和尾行清理属于后端/发布分支，不在本分支执行真实云端写入。
- [x] 新增前端检查模型和bundle字段，内部统一输出`pass`/`fail`/`unknown`；页面404、导航失败、身份不一致等整页门禁时七项均为`unknown`，表格显示`-`，禁止把缺证据写成通过。父子ASIN发散按明确业务标准实现：页面正常且存在至少一个子体/变体ASIN为`pass`，页面正常但零个子体/变体为`fail`，无法确认变体区域为`unknown`。
- [x] 在同一商品页面DOM和同一浏览器Tab中完成七项检查，禁止为每项检查新增导航；记录expected、observed、reason、evidence_locator、抓取时间和`frontend_check_rule_version`。N列主图与O列`From the brand`品牌故事图片必须独立输出。
- [x] 实现尺寸预期值读取和规范化比较；BSR、环保标志和Amazon's Choice不读取周报预期或上一批值。BSR与AC先分别按当前商品容器输出原始状态；旧 v14 的互斥覆盖已由当前 v15 撤回，当前同页证据分别发布。
- [ ] F1：为两个店铺分别登记Seller Central【反馈管理器】URL、非敏感店铺标识、凭证引用和目标上下文；只允许从【最新反馈】区域读取，禁止接入商品Review、Q&A或前台评论。两个店铺必须使用独立会话并串行处理，不得混用页面、分页游标、订单详情或下载状态。
- [ ] F2：实现窗口状态账本和日期门禁。无有效成功检查点时首次回看运行时间往前7个自然日；首次窗口两店均完成边界读取、合并和写后回读后，后续每次回看往前3个自然日；结果表按反馈日期只保留近10个自然日。窗口统一使用`Asia/Shanghai`，部分失败不得把7日窗口推进为3日窗口。
- [ ] F3：实现后台慢速读取和风控门禁。参考`D:\projects\T2_BDLD_weekly_20260827`的串行、保守随机等待、页面稳定后读取和风险立即停机原则；按页面显示的【下一个】按钮翻页，确认页面内容/分页状态变化后再继续。遇到登录失效、验证码、风控、页面异常、分页无变化或订单身份不一致时停止当前店铺、关闭上下文、保存证据，不得连续重试轰炸，再独立尝试另一店铺。
- [ ] F4：在【最新反馈】中解析店铺、日期、评级、订单编号、评论；筛选评级小于等于3的记录，缺失或无法解析评级不得默认合格。对每条合格记录点击订单编号进入二级页面，校验订单身份并读取订单商品编号、ASIN、SKU；二级详情失败不得猜测其他订单字段，主反馈可保留、详情字段留空并标记本地`partial`以便重试。
- [ ] F5：固定`Feedback差评汇总`目标子表身份并改为严格9列表头：`店铺、日期、评级、订单编号、评论、订单商品编号、ASIN、SKU、获取时间戳`。两店结果写入同一子表，按固定店铺顺序上下连续分组、共用一个表头，不插入第二表头或合并单元格；目标表不追加内部幂等键、`run_id`或状态列。
- [ ] F6：实现内部幂等和近10日清理。优先使用`店铺 + Seller Central稳定feedback ID`；没有稳定ID时使用`店铺 + 日期 + 评级 + 订单编号 + 评论内容哈希`，键和降级原因只写本地审计。重复读取更新同一逻辑行；写入前备份目标子表，按反馈日期删除早于10日窗口的行，写入后按9列整表回读。日期缺失或无法解释的记录不得静默写入窗口。
- [ ] F7：保存`outputs/feedback/{run_id}/`证据和耗时。至少包含两店来源URL、店铺状态、窗口起止、页码、【下一个】按钮状态、原始读取数、评级合格数、二级详情尝试/完整数、写入数、过期删除数、失败原因、`started_at`、`finished_at`、`elapsed_seconds`和每店铺耗时；凭证、Cookie、Authorization和完整敏感响应不得落盘。无论成功、partial、blocked还是锁冲突，都必须有日志收口。
- [ ] F8：扩展weekly bundle、delivery、summary、notification和manifest统计：`feedback_rows_seen`、`feedback_rows_eligible`、`feedback_rows_detail_complete`、`feedback_rows_written`、`feedback_rows_expired_deleted`、两店状态、两店耗时、窗口类型（`initial_7d`/`incremental_3d`）、Feedback子表回读结果和整个Feedback阶段耗时。Feedback失败不能覆盖或回滚已验证的价格结果。
- [ ] F9：实现离线回归：评级1/2/3保留、评级4/5及缺失评级排除；首次7日与后续3日窗口切换；近10日过期清理；两店串行和单店失败继续；三页分页与【下一个】无变化门禁；订单二级详情身份校验；重复合并；表头严格9列；店铺上下分组；主反馈保留但详情partial；风控/登录/验证码立即停止；凭证脱敏；运行耗时和异常日志落盘。
- [ ] F10：完成真实验收：先单店只读小批，再第二店只读小批，再两店首次7日只读与二级详情小批，随后固定子表9列写入/整表回读和近10日清理，最后验证后续3日增量与定时任务。每阶段保存独立`run_id`、窗口、店铺状态、页数、读取/筛选/详情/写入数量、起止时间、墙钟耗时和风控/阻断证据；未完成这些证据前不得把Feedback任务标记为完成或写入正式生产结果。
- [ ] 价格任务仍需扩展weekly bundle、delivery、summary、notification和manifest统计：`frontend_checks_written`、七项检查状态计数以及上述Feedback独立统计；两个模块可以并行开发，但同一线上结果表的正式写入必须经过统一运行锁和单一发布门。
- [ ] 实现前不得把本次新9列表头或Feedback数据写入飞书；不得把现有`seller_feedback.py`中旧的`<3`规则、A:L表结构、`code`字段或离线替身测试当作本次新模块已验收。实现后同步README、REVIEWS、操作手册/当前业务规则的跳转职责和部署配置说明。
- [ ] 实现前不得把新增列或Feedback写入飞书；不得把当前价格任务的历史HTML或旧检查值当作新增结果。实现后同步README、REVIEWS、操作手册/当前业务规则的跳转职责和部署配置说明。

### 本轮本地开发进度（2026-09-08）

- [x] 开发副本增加 `frontend_checks.py`：七项检查复用同一份商品DOM快照，输出 `pass`/`fail`/`unknown`/`not_applicable`、观察值、原因、定位和独立规则版本；页面门禁及价格解析门禁失败时写入 `unknown`。
- [x] `CrawlResult`、缓存和本地CSV保留前端检查证据；weekly bundle/summary/通知开始记录 `source_period_id`、`scheduled_slot`、`selection_mode` 和前端状态统计。
- [x] 结果发布器本地切换到 A:V，当前顺序为A:G源字段、H:M价格/币种、N:T前端勾叉、U/V时间戳/链接；识别旧 A:P/A:O 布局，写前备份、旧列迁移和整段回读逻辑已加入；尚未对真实固定结果表执行迁移验收。
- [x] 2026-09-09 已在开发副本将 `seller_feedback.py` 重构为本次`<=3`、严格9列、二级订单详情合并、首次7日/后续3日窗口、近10日留存、幂等和写后回读核心；新增 `seller_feedback_browser.py`，按登记选择器串行操作两店并对【最新反馈】、【下一个】、订单身份和风控信号 fail-close。当前只完成离线假桥接回归，真实Seller Central会话/选择器/固定Sheet ID尚未验收，不能勾选F1-F10完成。
- [x] 2026-09-09 参考项目级紫鸟 CLI 只读复核：使用 `C:\Users\Administrator\.workbuddy\binaries\node\versions\22.22.2\ziniao-cli.cmd` 的 `doctor` 和 `store list --all`，Keychain/API认证、ZClaw Bridge 和客户端登录状态通过，返回冬豚 `26782671389969`、北蓉 `26686718929338` 两店；全局 npm CLI 的 Keychain 缺失仅作为错误路径记录。没有打开店铺、没有访问 Seller Central、没有写入飞书；真实Feedback验收仍待页面身份、选择器和固定Sheet ID门禁。
- [x] 2026-09-09 前端实时只读小样本：Amazon.com `B0CLNJH915` 的 URL/标题身份一致，主图、当前选中尺寸、`From the brand`标题、当前商品可见AC和环保文本均已在实时页面核对；`B0BNDLPW1L`、`B0G5Y2TJQM` 当前为`Page Not Found`，按 v10 保留整页`unknown`边界；没有写入后端或固定结果表。该条只记录已完成的小样本，不替代下方 US/CA 多样本和 A:V 云端验收。
- [x] 2026-09-09 Feedback管理器单店只读探针：冬豚店铺可打开，项目级 CLI 以 `domcontentloaded` 成功到达 `https://sellercentral.amazon.com/feedback-manager`；脱敏 DOM 诊断未发现页面标题、【最新反馈】或【下一个】，可见文本仅389字符，未触发登录/验证码/风控标志。已关闭店铺，未点击订单、未翻页、未读写飞书；页面结构未确认，不能勾选F1/F3/F4/F10。
- [x] 增加显式 `scheduled_slot`、周一早间沿用、周一下午切换、工作日稳态 pending 记录，以及调度包装器向Python传递槽位；尚未重新安装并实测四条Windows计划任务。
- [x] 2026-09-08 开发副本实测当前登记序号4：只读发现新快照19个子表、18个业务映射（US=11、CA=7），排除`BI源数据`；ASIN/商品链接审计有效720、无效0、辅助标签跳过294。随后创建独立快照副本（Token仅在日志中脱敏保存），原周报未写入。
- [x] 前端单条实测：US `B0C5R56QTF` 使用新快照完成`amazon.com`/USD/90210/ASIN一致性门禁，尺寸`2.5x8`与页面`2.5' x 8'`匹配；首次样本暴露导航/语言误报后，收紧全局容器、尺寸结构和单位比较并升级前端规则`2026-09-08-v2`。CA首条`B0D9NT9JQN`保留真实`identity_mismatch`，N:T显示`-`且bundle为`unknown`，未绕过重试、未写飞书。
- [x] 修正`--limit`按有效/无效源行合并后的原表行号截取，避免`source_data_invalid`记录使前端样本超出限制；以上真实样本均为dry-run，仅保存本地bundle/CSV/缓存证据。
- [x] 读取历史离线HTML并收紧前端选择器：主图使用`#imageBlock_feature_div`/`#landingImage`，品牌故事使用`#aplusBrandStory_feature_div`/`data-feature-name=aplusBrandStory`且强制精确标题与同模块图片，尺寸优先使用`#inline-twister-expander-header-*`，先校验`#title_feature_div[data-csa-c-asin]`/`#ASIN[value]`或页面URL ASIN页面主商品身份，身份锚点全部缺失时七项均为`unknown`，BSR使用当前ASIN绑定的`prodDetails`详情表并允许折叠表格、排除推荐轮播，父子ASIN使用`#inline-twister-expander-content-*`下的`li.inline-twister-swatch[data-asin]`，环保使用同一当前商品卡片且要求 ATF 模块显式存在并匹配`data-csa-c-asin`的叶子图标与`1 sustainability feature`文本，AC使用当前ASIN绑定的可见实际badge而不是隐藏说明弹窗；同一ASIN同时存在BSR和AC时当前 v14 两列均判定`fail`并记录冲突；尺寸规则补充`8'X10'`与`8 x 10 ft`的共享单位等价比较并忽略`Rectangular`后缀，早期规则版本为`2026-09-10-v11`。
- [x] 2026-09-10 尺寸判定修复：发现旧 bundle 的 `expected` 仍是未求值的`BI源数据`公式文本，且旧规则无法把`2.5'X8'`与页面`2'6\" x 8'`识别为同一尺寸；新增公式源值解析、共享尾部单位等价和英尺小数/英尺加英寸换算。18个隔离业务子表只重算P列并逐表回读：719个商品行中`✅` 481、`❌` 0、`-` 238；没有改价格、SKU、尺寸C列或其他风控列。规则版本为`2026-09-10-v11`。
- [x] 用历史离线HTML完成各站点/布局的选择器覆盖清单和前端反例回归；离线样本只用于规则验证，不能替代当次实时页面结果。紫鸟/ZClaw实时US/CA采集由后端/发布分支负责，不作为本分支前端完成条件。
- [x] 依据业务反馈调整可见列：前端七列改为`✅`/`❌`/`-`，详细`pass/fail/unknown/not_applicable`仅保留在bundle；时间戳和Amazon链接移动到U/V最后两列，并同步旧A:P/A:O迁移逻辑。
- [x] 原“前端隔离在线表验收”已确认仅使用历史离线HTML，不能作为实时验收依据；其结果保留作选择器回归证据，不再作为线上前端完成证明。
- [x] 前端实时隔离验收：已按原入口`app/run.py --weekly-run --dry-run --force-fetch`（单表门禁另加`--sheets PD03 --limit 1`）逐行打开实际商品链接，由原流程内部复用`run_fetch`/`AmazonBrowser`完成同页七项检查；原bundle已写入新测试表并逐范围回读。不得另起独立浏览器/CDP路径，生产结果表未写入。历史HTML只作fixture。全量结果和技术异常明细见`docs/REVIEWS.md`的“2026-09-10 原入口实时全量结果”；异常存在，所以此项完成的是实时验证执行，不代表生产合并批准。
- [ ] 后端/发布分支完成A:V真实最小批云端写入回读、两个店铺Seller Central只读分页和全量验收后，再按统一发布门验收；该项不阻断本分支前端规则、离线回放和本地bundle完成。

## 2026-09-07 临时全量补跑：seq-4（15:48 启动）

- [x] 从周报链表 `HwxpwCnZ7iV1o5klIGbc8wJHnrd` 读取有效登记，选择当前最新有效周期 `seq-4`，源周报为 `DxPrsBw0Rh16TTtTymmcOpelnEg`；本批固定使用该源快照，不跨周期混写。
- [x] 修复两处源表兼容性问题后完成18个业务子表（11个US、7个CA）的真实 Amazon 抓取；运行 `20260907_154854`，从15:48:54到17:44:42，调度墙钟 `6948.963s`（约115.82分钟），调度日志以 `END exit=0` 收口。
- [x] 固定结果表继续复用同一 Spreadsheet Token `Epads8MQkhkuBctjl3lcqLUvnCg`，本批写入 H:O 366 行、基础 A:G 同步719行，353行进入阻断/恢复清单，云端写入无失败回报；结果表名称同步为 `Amazon周报前端价格捕捉_2026-W37_20260907_154854`。
- [x] 本批状态为 `partial/degraded`：逐行状态 `ok=337`、`identity_mismatch=182`、`parse_error=139`、`crawl_error=32`、`source_data_invalid=29`；币种为 `USD=549`、`CAD=170`。技术异常率24.8%，未将异常行当作售罄或零价。
- [x] 应用协作者通知8人成功、0人失败；通知回执与本地证据已落盘。HTML归档未参与本轮价格任务，历史HTML未删除。
- [x] 证据：`outputs/daily_runs/2026-09-07/20260907_154854_weekly_bundle.json`、`20260907_154854_weekly_summary.json`、`20260907_154854_delivery.json`、`20260907_154854_notifications.json`、`outputs/scheduler_logs/2026-09-07_154853_310_2652.log`、`outputs/weekly_runs/seq-4/weekly_manifest.json`。

## 2026-09-07 15:30 副本创建超时修复

- [x] 定位实际失败边界：计划批次 `20260907_1530` 在 Amazon 抓取前创建周报副本时收到飞书 Drive `504 Gateway Timeout`；没有生成该批次抓取产物，固定结果表未被本批次改写，管理员异常通知已送达。
- [x] 修复 `FeishuClient.copy_file` 的瞬态错误处理：对 408/429/5xx 和传输超时最多退避重试3次；每次重试前及最终失败前按精确名称回查根目录，若服务端已完成复制则复用唯一副本，避免重复创建周报快照。
- [x] 加固 `bin/scheduled_run.ps1`：捕获调度包装器异常、保留 Python 退出码，并在 `finally` 中必写 `END` 行，避免 stderr traceback 使调度日志无法收口。
- [x] 临时补跑在源快照发现阶段发现兼容性问题：`PD05` 的合法表头为 `ASIN\n(...)`，旧检测误报 `未知 Marketplace`；未进入 Amazon 抓取和固定结果写入。
- [x] 修复 `find_asin_header`：兼容带换行/括号说明的 ASIN 表头，仍拒绝 `ASIN_CODE`；对临时快照只读验证为21张表、18张业务表映射、3张辅助表排除、0未知、0重名。
- [x] 回归验证：新增“504后副本已存在可恢复”和“传输超时后重试成功”测试；快照相关10项测试通过，全量离线回归 `271/271` 通过，Python 编译和 `git diff --check` 待本轮命令复核。

## 最新生产基线与迁移准备（2026-09-02）

> 当前状态以本节为唯一交付结论。下方的 8 月阶段记录仅保留当时的实施证据；计划任务已于本轮在目标 Administrator 账户重新安装并回读，旧记录不替代本轮验收。

- [x] 全新源快照全量实跑：`20260902_121541`，登记周期 `seq-3`，重新创建第5代周报快照（`Udylsp...Tnmf`）并继续写入同一个固定结果表（`Epads...LVnCg`）；18个子表、719行基础数据均处理完成。
- [x] 本轮价格任务关闭HTML归档：`html_archive_enabled=false`，无HTML下载/写入，价格抓取与HTML保持独立。
- [x] 全量结果（当时旧状态口径）：507 `ok`、178 `crawl_error`、29 `source_data_invalid`、5 `parse_error`；USD 549、CAD 170；H:O 已回读写入536行，183行进入阻断/恢复清单，批次状态 `partial`，完整耗时 3685.516 秒（约61.43分钟）。旧口径技术异常率为26.5%，不能把异常行当作售罄或零价。
- [x] 状态纠偏：上述178条原标为`crawl_error`的记录均为最终ASIN与请求ASIN不一致。新增`identity_mismatch`独立状态，继续重建Tab重试和逐行阻断，但不再把源链接/站点重定向问题计入浏览器网络技术异常；诊断证据同样保存。
- [x] 在线确认：US `B0DRKD4LQC`连续3次新Tab仍跳转到`B0BY1Z43FP`；CA `B0BNDLKP54`在邮编验证/CAD证据正常时仍跳转到`B0D9NT9JQN`。两者均证明为Amazon实际重定向/源映射缺失，不接受落地页面价格。证据在`outputs/poc_resources/r1_7_us_B0DRKD4LQC.json`、`r1_7_ca_B0BNDLKP54.json`。
- [x] 一次性通知范围：本轮使用 `--notify-manager-only`，通知回执仅包含周成业1个 `open_id`，成功1、失败0；该参数不改变默认协作者名单，后续计划任务不带此参数即恢复原协作者通知。
- [x] 证据：`outputs/daily_runs/2026-09-02/20260902_121541_weekly_bundle.json`、`20260902_121541_weekly_summary.json`、`20260902_121541_delivery.json`、`20260902_121541_notifications.json`、`outputs/snapshots/20260902_121541/source.json`。
- [x] 历史 Docker PoC 实机构建与短启动验收（2026-09-02，方案已取消）：Docker Desktop Linux 引擎曾生成 `amazon-daily:latest` 并完成容器内编译/短启动健康检查；该记录只保留用于追溯，当前不再交付镜像、Compose 或容器调度。
- [x] Windows计划任务目标账户重新安装并回读（2026-09-02）：以 `chinami-coui675\\administrator` 执行 `bin\\schedule.bat --install`，退出码0。`AmazonDaily_0730`、`AmazonDaily_1530`均为 `Ready`、`Hidden=True`、`Execute=wscript.exe`，参数为 `//B //NoLogo bin\\hidden_ps1.vbs bin\\scheduled_run.ps1`；触发器为周一至周五，下一次分别为 2026-09-03 07:30、2026-09-02 15:30，最近已运行任务返回码均为0。任务不经过可见 cmd/BAT 窗口。

## 当前执行索引：源链接保留与导航结果防护（2026-09-02）

- [x] 修复 `tab.get()` 返回 `False` 时仍继续读取页面的问题；现在直接记录 `navigation_failed` 并结束本次尝试，不解析旧 DOM。
- [x] 修复 `doc_loaded()` 超时被静默忽略的问题；异常或显式返回 `False` 统一记录为 `navigation_timeout`，不再读取 `location.href`、价格或促销。
- [x] 新增两项回归：导航失败不得调用 `run_js`，文档加载超时不得调用 `run_js`。
- [x] 链接优化：源表合法URL保存为`source_product_url`并优先请求，保留`?th=1`/`?psc=1`等参数；源URL与ASIN不一致时拒绝，纯ASIN回退标准链接；快照、缓存和诊断均保留源链接与实际请求链接。
- [x] 初始化兼容：Amazon 地址组件首次回读为`Update location`时，`setup()`最多重试3次、每次间隔2秒，连续失败才阻断。
- [x] 验证：`PYTHONPATH=app; .venv\\Scripts\\python.exe -m unittest discover -s tests -p 'test_*.py'`，269/269 通过（命令耗时约1.4秒，含Docker打包、环境覆盖、身份不一致分类/写入门禁回归）；本条历史记录不替代真实在线验收。
- [x] 在线单点：CA `B0D9NT9JQN` 通过，`amazon.ca`、CAD、邮编验证、最终 ASIN 一致，耗时约18.6秒；未写飞书。
- [x] 在线单点：US `B0C5R56QTF` 首次两次遇到地址回读瞬态失败（`postal_input_not_found`/`postal_not_observed:Update location`），随后重试恢复；修改后再次实测 `amazon.com`、USD、90210 `visible_exact`、最终 ASIN 一致，商品耗时18.381秒，未写飞书。
- [x] CPD批量只读验证（2026-09-02 11:58:43）：CPD03/17/05/25/39/33/52共170行，30 ok、130 identity_mismatch、1 parse_error、9 source_data_invalid；CAD=170，技术异常率81.4%，耗时约688.7秒，未写飞书。130条仍落到相邻有效ASIN，且本轮快照的170条`source_product_url`均为空，尚未验证真实周报原始链接参数的改善效果。
- [x] 一次性通知范围：新增`--notify-manager-only`，仅对当前正式`--weekly-run`生效，不改变默认应用协作者名单；回归验证仅发送`feishu_manager_open_id`。
- [ ] 下一步用 CPD03/CPD17 做真实源链接对照，逐行记录源链接、请求链接、最终URL/ASIN和耗时；并发只作为后续变量，不作为当前根因假设。

## 当前执行索引：无控制台调度修复（2026-09-02）

- [x] 定位终端闪现原因：PowerShell的`-WindowStyle Hidden`只能隐藏内层PowerShell；若旧计划任务或人工入口先经过`cmd.exe`/BAT，外层控制台仍可能出现。
- [x] 新增`bin/hidden_ps1.vbs`，使用GUI子系统`wscript.exe //B //NoLogo`启动隐藏、非交互PowerShell并等待原始退出码；`scheduled_run.bat`和`start_html_server.bat`同步改用该启动器（BAT被手工双击时外层cmd仍可能短暂闪现，计划任务不再经过BAT）。
- [x] 更新`bin/schedule.ps1`：新安装的`AmazonDaily_0730/1530`直接执行`wscript.exe`，不再经过可见BAT/cmd窗口；PowerShell和Python仍保持原日志、锁和退出码链路。
- [x] 验证：`schedule.ps1`、`scheduled_run.ps1` PowerShell语法通过，`git diff --check`通过；当前沙箱禁止CScript执行（Access denied），因此未将本机WScript运行视为已验收。
- [x] 历史阶段回读（2026-09-02早期环境）：曾在用户 Windows 主机回读到`AmazonDaily_0730`与`AmazonDaily_1530`为`Ready`、`Hidden=True`、`Execute=wscript.exe`，参数指向`hidden_ps1.vbs`→`scheduled_run.ps1`；旧的BAT/cmd任务定义已被替换。本轮已在目标账户重新安装并再次回读，当前启用状态以本文件顶部的本轮验收记录为准。

## 当前执行索引：9月1日兼容性修复（2026-09-01）

- [x] 复现并定位今日失败：新周报新增`Sheet20`销售/导出辅助表，因含ASIN但无价格业务表头被旧规则误判为未知Marketplace，导致快照完成后在映射阶段退出；新增通用辅助表识别，仅当缺少“正常售价/目标成交价”时排除，具备价格表头的未知命名仍阻断。
- [x] 异常可诊断性：正式入口异常处理现在先将完整 traceback 写入 `outputs/logs/run_*.log`，再发送管理员通知，避免只留下成功通知的一行日志。
- [x] 隐藏终端兼容：`scheduled_run.bat`、`start_html_server.bat`补充`-NonInteractive -WindowStyle Hidden`；计划任务仍为隐藏 PowerShell 直接执行，手工启动中心保持可见。
- [x] 只读复现命令未抓Amazon、未写飞书，确认错误为`RuntimeError: 存在未知 Marketplace 的含 ASIN 子表: Sheet20`。
- [x] 修复后只读映射验收：当前seq-3快照共20张表，映射18张（US=11、CA=7），`BI源数据`与`Sheet20`共2张辅助表排除；命令耗时13.578秒，未抓Amazon、未写飞书。
- [x] 版本交付：修复提交`0d4474d`已推送`fix-codescan-20260826`与`production-closeout-20260827`；生产工作区仅保留用户既有`.workbuddy`修改及未纳入本次交付的Docker草稿文件。
- [ ] 修复后的正式批次需验证20张源表中18张价格业务表被映射、Sheet20被排除，CA结果、固定结果写入、通知和完整耗时；不以只读复现替代线上验收。

## 当前执行索引：生产级最终收口（2026-08-27）

- [x] 停止新增HTML：正式诊断仅保存截图与JSON，移除位置PoC失败时的setup.html落盘；独立归档功能仍默认关闭，既有htmls和debug历史文件不删除。
- [x] 换周时效门禁：新增持久化registry_freshness账本，正式新批次对同一登记序号/URL按首次发现计龄，默认超过8天停止；重复读取不能续期，同序号替换URL停止，明确恢复既有run_id不受影响。
- [x] 开机补跑收敛：早晚任务同时补跑时统一进程锁只允许一批执行；另一批退出75由调度包装器记录为正常跳过并返回0。日志名增加毫秒和PID，消除同秒覆盖。
- [x] 容量收口：旧维护同步、旧列迁移、ASIN映射及源表读取均按飞书元数据真实容量分页，不再存在A3:A2000、N2:P2000或A1:O500硬截断。
- [x] 新增5项针对性回归并完成253/253离线测试；覆盖登记过期与不可续期、诊断不写HTML、2003行ASIN写入和调度重叠语义。测试均使用临时目录与替身，未抓Amazon、未写飞书。
- [x] 暂存与生产验证：253/253通过（暂存墙钟2.675秒、生产墙钟2.135秒）；60份Python AST、8份入口文档链接、scheduled_run.ps1与schedule.ps1语法、固定容量/HTML落盘残留扫描及git diff --check均通过。全部使用替身，未抓Amazon、未写飞书。
- [x] 生产部署：只读确认两个价格任务Ready、HTML任务Disabled，仅有两个无关BDLD端口服务；取得weekly_scheduler.lock后部署16份文件，逐文件SHA-256一致。旧文件备份于outputs/code_backups/final_production_closeout_20260827，历史HTML/debug及用户.workbuddy修改均未改动。
- [x] Git版本控制：生产代码提交`f9f5b86`（Complete production delivery safeguards），已推送`production-closeout-20260827`并将`fix-codescan-20260826`快进至同一提交；最终证据文档另作后续提交。
- [ ] 下一次真实工作日计划批次作为最终在线验收：记录隐藏启动、登记时效、CA结果、固定表写后回读、通知和完整墙钟；不为收口提前触发额外全量。

## 当前执行索引：生产全量后收口（2026-08-27）

- [x] 核对真实批次20260827_073003：调度07:30:01启动、08:36:38结束，完整墙钟3997.270秒；724行中542写入、182阻断，8名协作者通知均成功。内部登记和固定结果指针均为seq-2；登记表只读预检发现199行中只有2条有效链接，当前确为序号2，不是通知模板硬编码。
- [x] 周期可读性：内部seq-N继续作为不可变资源身份；通知“周期”和结果表名称改用run_id日期对应ISO周并附登记序号，例如2026-W35（登记序号seq-2）。不修改历史manifest，不用显示文字冒充登记表新增链接。
- [x] CA分析：170行=28成功、116 crawl_error、17 parse_error、9源数据无效；116条crawl_error主要为identity_mismatch，多个1～3秒失败落到同一上一页ASIN。重试前改为销毁并重建当前Tab，修复后仍保留URL/#ASIN/邮编/域名/币种门禁；出口和风控待在线验证。
- [x] 飞书token：tenant token不再永久缓存，读取expire/expires_in并提前5分钟刷新，保证长任务结束时通知可重新认证；增加过期token回归。
- [x] HTML正式停用：代码默认、config.json和模板的归档/必需门禁/服务均为false；价格入口仍强制false。2026-08-27停用AmazonDaily_HTML_Server、停止PID 30284/32068，回查8765监听0、防火墙允许规则Disabled、htmls目录仍存在；不删除历史文件或独立工具。
- [x] 隐藏调度：安装脚本改为直接启动隐藏、NonInteractive PowerShell，调度日志统一UTF-8并保留原生退出码。首次安装实测暴露PowerShell参数Action与局部变量action大小写冲突，旧任务未被覆盖；已改为taskAction并重新安装。最终回读两任务Ready/Enabled、Hidden=true、Execute=powershell.exe、WindowStyle Hidden、StartWhenAvailable=true、IgnoreNew、DaysOfWeek=62、EndBoundary=null，工作日07:30/15:30不变，未立即运行。
- [x] 验证与部署：暂存248/248通过（框架1.232秒、命令墙钟1.977秒），58份Python AST、8份入口文档链接和两份PowerShell语法检查通过。部署18份文件并逐文件SHA256核对，备份outputs/code_backups/production_closeout_20260827；正式目录248/248通过（框架1.393秒、命令墙钟2.256秒），git diff --check通过。所有测试使用替身，未抓Amazon或写飞书。
- [ ] 修复后的首次CA在线样本、15:30全量、ISO周通知/改名和隐藏调度实际验收；必须记录run_id、行数和完整耗时，不用离线测试替代。

## 当前执行索引：完整优化与缺口收口（2026-08-26，v4）

本节覆盖下方旧版v3与212项测试记录。以下为本轮完整清单；已完成表示代码及离线回归完成，不替代真实云端验收。

- [x] C26-01 促销证据：Coupon/Code/Save与主价共用DOM树，排除隐藏、推荐、评论、脚本、订阅及二手区域；不再使用跨控件文本窗口，多个冲突值拒绝计算；浏览器实际CSS隐藏状态写入只读页面克隆供解析。
- [x] C26-02 计算边界：校验有限值、非负价格、折扣比例及优惠金额上限；异常状态不得重新计算成成功，零容差保持为零；源价格非法同样阻断。
- [x] C26-03 页面身份：等待完成后同一次脚本读取URL、页面ASIN、邮编与HTML，重新验证迟到跳转、站点、币种和当前页面邮编。加拿大完整六位必须全匹配，不能用前五位掩盖末位错误。
- [x] C26-04 统一互斥：手工、Windows定时、恢复及维护CLI共用Python进程锁；占用时退出75，不操作云端；PowerShell只包装日志，不重复锁定。跨进程锁回归通过。
- [x] C26-05 防旧批覆盖：独立latest_run登记；发布、分段写入及改名核对周期/run_id/副本/固定表身份；固定表发布指针不再提前移动；同秒run_id碰撞加微秒后缀。
- [x] C26-06 持久化与工作线程：唯一临时文件、fsync及串行替换；同表缓存快照串行化；获取Tab、等待及缓存失败保留已抓结果，最终bundle负责交付证据；最终manifest失败保留已回读的行数，不归零。
- [x] C26-07 恢复一致性：v4/cache5/bundle2；校验规则、容差、整体时间、每条价格真实采集时间以及基础行有序指纹，禁止重存旧价格续期或修改副本后混用旧价格。
- [x] C26-08 完整读取：按真实行列容量分页、保留绝对行号；解除2000总行数和O列边界；链接审计与源行解析统一支持裸ASIN、URL/富链接；已登记空表新增数据时拒绝旧映射；备份读取完整行容量。
- [x] C26-09 通知回执：按run_id、业务内容和收件人保存回执；相同结果不重复发成功者，失败者/新增协作者可重试；业务结果改变可再次通知；本地路径仍仅周成业可见。
- [x] C26-10 入口与文档：更新SPEC流程图、职责树、规则、恢复和锁；配置默认/实际/模板版本一致；README和文档导航同步，三份旧中文文档保持单一跳转职责。
- [x] 新增35项净回归（212→247）；不是只重跑旧测试。覆盖上述反例，包括页面迟到跳转、不同促销控件串值、错误CA末位、超过2000行及O列之外表头、恢复跨周、缓存并发和通知失败重试。最终统一发现与解析的前10行/富文本表头规则，247项通过（框架1.321秒/命令2.107秒），PowerShell调度脚本语法通过。
- [x] 迭代记录：212项旧夹具适配后通过（框架0.913秒/进程1.590秒）；235项通过（1.090秒/1.827秒）；239项通过（1.066秒/1.775秒）。期间新增并发测试暴露Windows替换竞争并修复；每条时间校验暴露旧夹具空timestamp并修正。245项首轮暴露宽列表头扫描及测试run_id碰撞，修正后245项通过（1.184秒/命令1.878秒）。补已写进度保护后246项通过（框架1.258秒/命令2.050秒）。所有测试使用临时目录和API/浏览器替身，日志中的发送与写入均为模拟。
- [x] 历史HTML离线回放：2026-08-25 CA B0G5Y2TJQM为72.99（0.921秒）；2026-08-24 US B0C5R56QTF为37.99并排除二手36.09（1.407秒）；合计进程2.689秒。没有把历史文件当作当前在线抓取。
- [x] 2026-08-26 15:19前已部署32份文件：取得weekly_scheduler.lock并确认无价格进程，原文件及暂存SHA256核对通过；备份outputs/code_backups/complete_fixes_20260826，部署命令0.660秒。未更改07:30/15:30计划时间，未启动全量或操作飞书。
- [x] 正式目录复测2026-08-26 15:19:04开始：247/247通过（框架1.260秒）；58份Python AST解析与文档本地链接0失效，检查及回归合计1.891秒，含git diff --check命令墙钟2.212秒。diff检查通过，仅现有LF/CRLF提示。最终暂存同组检查247项通过（框架1.236秒，检查及回归1.875秒）。
- [ ] 实际US/CA最小样本、最小子表、全量/换周与通知验收；每次记录真实run_id、起止和完整耗时。此轮未启动在线任务，不用毫秒级离线测试冒充全量用时。剩余边界只维护在REVIEWS。

## 当前执行索引：审查缺陷修复（2026-08-26）

- [x] R26-01：启动中心最小验证改为单条dry-run，全量改为weekly-run --confirm；默认旧full_flow与旧push-only入口拒绝误用，README/CLI同步。
- [x] R26-05/06：导航、等待和脚本读取裁剪剩余预算，禁用底层重试与额外refresh/rebuild，返回超时拒绝价格；首页初始化30秒；价格关闭HTML仍每商品等待1～3秒，归档开启不重复等待。
- [x] R26-07～09：主价DOM边界、隐藏/脚本/推荐/划线/二手区排除；取消全局兜底；强制邮编、最终域名和币种证据；冲突为parse_error，售罄须当前availability证据。
- [x] R26-10/11：新建Sheet登记pending身份支持临时故障后的空表恢复；未知空表拒绝。恢复要求schema、规则、容差、时间有效；规则2026-08-26-v3、cache schema5、bundle schema2，旧证据保留但不可发布。
- [x] 阶段回归：第一次191项中1项旧测试缺少新增bundle元数据（框架0.476秒、进程1.045秒）；第二次208项发现CA$被误匹配为A$（0.458秒、1.008秒），已修正词边界并通过17项专门回归（0.050秒、0.616秒）。随后210项通过（0.459秒、1.215秒）。
- [x] 最终暂存回归：212/212通过，框架0.566秒、命令墙钟1.157秒、进程1.224秒。新增21项测试，覆盖推荐/隐藏/脚本/二手价、US/CA币种、邮编、404、冲突、售罄、预算、恢复、菜单与价格独立等待；全为本地假对象，无真实API。
- [x] 已有HTML离线回放：CA B0G5Y2TJQM提取72.99；US B0C5R56QTF提取37.99，排除USED区36.09。最终分别0.625秒、0.859秒，进程1.754秒。首次回放发现二手购买区重复主价，已增加排除规则和回归测试。这些是历史存档价格，不是当前在线实测或邮编证明。
- [x] SPEC流程图、配置版本、规则、恢复、目录、CLI和README同步；REVIEWS仅保留真实验收/驱动硬超时/云端创建身份不确定边界。
- [x] 正式部署20个文件，取得weekly_scheduler.lock，原文件SHA256未变化检查及发布后SHA256一致性核对均通过；备份outputs/code_backups/review_fixes_20260826。部署前只读确认两个Python进程均为HTML服务、无价格进程；未停止服务或更改计划任务。
- [x] 正式目录部署后回归：2026-08-26 14:47:03.421～14:47:04.434，212/212通过，框架0.471秒、命令墙钟1.010秒、进程1.197秒；git diff --check通过（0.225秒）。本轮未触发真实Amazon抓取、飞书写入/消息发送或重新计算现有云端结果。
- [ ] 真实US/CA最小样本、最小子表、全量与换周验收；本轮不提前触发抓取、飞书写入或通知，不声称已修正云端历史行。

以下每批A:G刷新索引为此前实施记录，其中“旧入口/超时/节奏尚未修复”已由本节覆盖；日期历史不删除。

## 当前执行索引：每批刷新A:G（2026-08-26）

> 本节覆盖下方历史“同周不刷新A:G、复用整周旧快照”的设计。固定的是七个字段的列位置，不是内容；早晚每个新批次都取当前周报最新副本。下方带日期的验证历史不删除。

- [x] 每批快照：正式weekly-run先分配run_id，再复制登记表选中的最新原始周报；同周两次为两份不同快照。保留固定结果Token及权限，不创建结果Spreadsheet；每份新快照授予周成业管理权限。
- [x] 恢复隔离：--resume/--run-id只恢复当前已登记批次，同批不重复复制；旧批manifest另存runs目录。固定登记缺失、同序号改源URL、旧批覆盖新快照均阻断；dry/fetch-only只读当前源表，不创建云端资源。
- [x] 完整行交付：每次重新发现子表并提取A:G，按ASIN组合本批H:O；新增、删除、重排和目标价/SKU更新一起发布，清理旧尾行，P为空；空业务表同样清理旧行。不会先清空结果再等待抓取。
- [x] R26-02～04收口：正式运行/恢复身份检查、每表ASIN集合完整性、逐段回读与真实成功计数；单表发布失败继续其他表。技术异常行保留本批基础字段、清空旧价格并计阻断；新增base_rows_written辅助审计。原失败复现及解决范围保留于本条，不再作为REVIEWS未解决项。
- [x] 新增13项专用回归，覆盖同周新快照/同批恢复、固定身份缺失、dry-run只读、新增删除重排、目标价/SKU更新、漏ASIN、部分失败计数、故障后继续、技术失败旧价格清理、空表、未知布局、源URL变更、旧批覆盖和只读恢复边界（部分场景在同一测试内）。
- [x] 首轮定向24项于0.278秒结束，1个断言失败、2个测试/兼容性错误；修复权限返回Mock、period_id兼容和Decimal数值断言后，24项通过（0.347秒）。后续全套194项通过（0.502秒），移除重复导入导致的重复收集并新增边界后最终191项通过（2026-08-26 14:15附近，0.478秒，命令内墙钟1.090秒、进程1.349秒）。全部为受限沙箱、内存飞书替身和临时目录，不是真实全量。
- [x] SPEC流程图、字段、快照生命周期、恢复、CLI和产物目录同步；REVIEWS保留本次范围外的旧人工入口、超时及商品间节奏问题。
- [x] 2026-08-26 14:16部署9份变更文件到正式项目，并逐文件SHA256一致性核对；更新期间取得计划任务运行锁，未启动抓取/写飞书。原文件备份于outputs/code_backups/ag_refresh_20260826_141659。
- [x] 正式目录沙箱复测发现旧test_flow有4处向生产outputs写模拟摘要的路径泄漏（191项、0.465秒），写入被沙箱阻止；已修复测试隔离，将OUTPUT_DIR及缓存/CSV全部重定向到临时目录并自动还原。隔离修复后191项通过（1.028秒、进程1.742秒）；未以放宽权限来放行模拟数据写入。
- [x] 最终部署后复测（2026-08-26 14:18）：正式目录191/191通过，框架0.461秒、命令内墙钟1.087秒、进程1.294秒；git diff --check通过，文档本地文件链接失效0处。测试未启动真实浏览器、未操作飞书；定时任务入口和执行时间未改。
- [ ] 下一次真实正式运行验收：同周改动A:G能够体现、固定链接不变、云端完整行回读及通知送达；本次不提前触发Amazon全量或飞书写入。

## 历史索引（2026-08-26文档整合与代码复核）

> 当前业务只以SPEC为准，当前代码问题只以REVIEWS为准。下方所有带日期的旧实施记录保留为历史；旧“仅下午”“每周新建结果表”“HTML依赖”“8月31日截止”和自然日14批计划不再自动执行。不要从历史未勾选项直接启动生产任务。

- [x] 完整归档整合前SPEC、操作手册及已采纳REVIEWS；统一当前SPEC的流程图、配置来源、目录、A:O布局、固定结果身份、价格/HTML独立、工作日双时段和通知规则。
- [x] 根README与docs/README只保留入口及职责；三份中文旧文档只跳转，不再维护平行规则。历史目录附明确“不可用于当前操作”说明，未删除旧验证记录。
- [x] 逐项核对正式运行/恢复、换周发布、列迁移、价格计算、CPD邮编/币种、通知及Windows脚本。发现6组未解决问题，已登记REVIEWS R26-01～06；本次未修改生产逻辑。
- [x] 2026-08-26 11:25附近完整离线回归178/178通过，框架0.374秒、命令内计时0.958秒、进程墙钟1.132秒；不代表新增边界已覆盖或真实飞书写入通过。
- [x] 四项内存复现全部确认当前缺口：ready周期缺失fixed登记被接受；同序号源URL变化仍进入缓存交付；2条源商品仅1条结果仍complete且blocked=0；第一表已写但第二表失败后written_rows=0。复现逻辑0.031秒、进程墙钟0.730秒。探针位于本次工作区临时目录，使用测试替身和临时文件，未联网或修改生产数据。
- [x] Windows只读回查：AmazonDaily_0730与AmazonDaily_1530均Ready，DaysOfWeek=62、WeeksInterval=1、EndBoundary为空，统一scheduled_run.bat入口，Interactive、StartWhenAvailable=false。此时下一次为2026-08-27 07:30和2026-08-26 15:30；回查墙钟1.766秒。本次不变更任务、不立即补跑。
- [x] 两个PowerShell调度脚本语法检查通过，git diff --check无错误；结果不掩盖启动中心旧入口问题。
- [x] 文档发布后本地文件链接检查0处失效，SPEC三组代码/流程图围栏闭合；首次diff检查发现5处新增文末空行，已规范化后复查。相关文件复制回项目前均通过apply_patch编辑；原版文档完整归档，生产代码未改动。
- [ ] 取得修复指令后处理REVIEWS R26-01～06并补反例测试；本次“核对代码”不等于已修复。
- [ ] 首次真实无HTML列迁移、固定表下一登记周期完整发布、逐人通知送达和一周双时段稳定性验收；需要实际run_id和耗时，不用离线结果代替。

取消/关闭的旧待办：新周新结果表的全员自动共享已随固定结果表设计取消，无需新增授权；陈家俊230013发送失败已在后续实际重发记录中解决。下方保留原始过程，不再视为当前待办。

## 2026-08-26 通知与结果布局变更

- [x] 每天两次调度纠偏：用户确认工作日每天两次，恢复07:30并保留15:30。Windows回读两个任务均Ready、DaysOfWeek=62（周一至周五）、EndBoundary=null；下次分别2026-08-27 07:30:00和2026-08-26 15:30:00。仅修改现有任务触发器及启用状态，执行路径不变、没有即时补跑或新建Codex自动任务。`bin/schedule.ps1`和SPEC流程图、操作手册同步；PowerShell语法通过，变更与回查墙钟6.044秒。此前“仅下午/晨间禁用”的记录作为已纠正历史保留。

### 固定结果链接纠偏（覆盖下方旧“新周新结果表”设计）

- [x] 按用户确认，固定结果Token为`Epads8MQkhkuBctjl3lcqLUvnCg`。本地登记`outputs/weekly_runs/fixed_result.json`；自动换周只复制源周报，不再调用创建结果Spreadsheet接口，不修改固定结果表协作者权限。不存在“给新结果表重新授权”步骤。
- [x] 新周先准备快照/映射和完整抓取bundle，发布时再备份并整行更新同一固定表。复用现有Sheet ID；先替换完整新行再清理旧尾行，不在抓取前清空。保护固定Token和当前周期，禁止历史缓存覆盖新周期，局部子表批次不得执行全表换周。
- [x] 回归：177/177通过（0.327秒，进程1.013秒）；新增固定表整行发布、原Sheet ID复用反例后178/178通过（0.412秒，进程1.266秒）。固定表初始化中断重试断言创建结果表调用0次、URL/Token不变、仅对新快照调用管理授权。测试均为离线；本次未在云端新建/改名/改数据/改权限。
- [ ] 下一次新登记周期的固定表发布仍需真实运行验收，不能把离线测试等同于云端跨表原子切换；现有表未提前修改。

### 价格独立、自动换周与验证后交付修复

- 最终补充回归：缓存恢复改为默认使用当前manifest全部子表后，177/177再次通过（0.327秒，命令墙钟0.962秒）。

- [x] 正式价格入口和缓存恢复强制使用不含HTML的运行配置；Windows价格入口及安装/移除脚本不再管理HTML服务或防火墙。现有独立HTML服务状态未改。
- [x] 自动准备新登记周期：完整副本、结果表、映射、链接审计、A:G全字段回读；默认处理新周全部映射子表并动态分配US/CA。旧周表不改，新周链接随通知给出；同序号改源链接仍停止，不静默污染原快照。初始化中断复用generation和已保存Token，进程退出自动释放OS锁。
- [x] 原子保存抓取bundle先于云端写入；保留首次原始备份；富文本链接标量化；迁移与H:O写入逐段回读，最多200行/段；只把回读成功计入写入数。子表/范围故障继续其余安全范围，改名失败不阻断结果保存或通知，交付检查点见`{run_id}_delivery.json`。
- [x] 离线验证记录（2026-08-26）：初版169/169通过（0.285秒）；新增换周/HTML不可用/改名故障5项定向通过（0.047秒）；后续174/174通过两次（0.295、0.298秒）；补齐锁恢复、首次备份保护、A:G非ASIN字段损坏反例后177/177通过（10:43:14附近，测试0.342秒，命令墙钟1.009秒）。两个PowerShell调度脚本语法和`git diff --check`通过；均未做真实云端写入。
- [x] 真实云端只读验证：18表已写622行与原bundle逐字段比较0差异，631个富文本链接成功标准化（10.562秒）；首次只读脚本因错误导入在0.555秒停止，未访问写接口，修正导入后通过。
- [x] 18表725行完整内存演练：只读加载云端A:V，在隔离内存副本运行真实写入器；622行回读成功、103行原业务阻断、迁移失败0、所有A:G保持一致、旧P清空，耗时6.203秒（进程6.683秒）。这是内存演练，不冒充云端迁移或重新抓取验收。
- [ ] 下一次15:30正式云端迁移/回读和通知验收；新周第一次真实资源创建及切换仍待新登记序号出现后验证。现有旧表未提前修改，下午任务未暂停。
- [x] 已取消（不是已实施授权）：旧“新周结果表自动向所有应用协作者授予查看权限”方案随固定结果表设计废止；不创建新结果表、不改变其既有权限。原先因未获共享授权而暂停的过程保留在历史说明，不是当前待办。

### 此前实施记录（保留）

- [x] 最新调度变更：用户取消8月31日截止并要求工作日固定执行。仅更新现有`AmazonDaily_1530`为每周一至周五15:30，周末不触发、无截止日期；回读Ready、DaysOfWeek=62、WeeksInterval=1、EndBoundary=null、NextRunTime=2026-08-26 15:30:00。0730仍Disabled，执行入口和其他设置不变，未立即启动任务。`bin/schedule.ps1`安装规则同步为工作日无截止且不恢复晨间任务；PowerShell语法解析通过，变更及验证命令墙钟2.869秒。此项取代下方历史截止日期，历史记录保留。

- [x] 恢复下午Windows任务：仅启用`AmazonDaily_1530`，回查State=Ready、NextRunTime=2026-08-26 15:30:00；0730保持Disabled；未立即启动任务、未创建Codex调度。保留EndBoundary=2026-08-31T00:00:00，明确不覆盖8月31日下午。
- [x] 下午无HTML列写入就绪核对：`--weekly-run --confirm`使用`weekly_result.py`既有迁移逻辑；结果表先备份再将N:P左移为币种/Amazon链接/空白，后续写H:O。本机`html_archive_enabled=false`、`html_archive_required=false`；相关12项离线测试全通过（0.023秒），启用与测试命令总墙钟1.824秒。本次未提前改云端列，下午真实迁移仍待运行验收。

- [x] 用户授权后向当前应用协作者补发同版真实结果通知：实时名单8人，陈家俊此前已成功接收，本次去重跳过；其余7人API均返回message_id，失败0，调用墙钟8.464秒。批次`20260826_070010`，本次所有正文与陈家俊一致，均不含模拟提示和本地路径；不改变日后周成业可见本地路径的规则。消息回执：周成业`om_x100b67d022e990b8b48d749a0316ea0`；王梦婷`om_x100b67d022f970acb48d846937a4057`；刘婉婷`om_x100b67d0228b9c8cb39c65c1f25b5d7`；兰雅`om_x100b67d0229b24a4b32cb56ca378273`；展帅`om_x100b67d022aaa0bcb32135ef53c7210`；谢琳莎`om_x100b67d022b5cca8b246acaf54ac35e`；祝慧敏`om_x100b67d022448cb0b03b3a595fc43e0`。仅验证发送成功，不代表已读；未启动新抓取或更改云端结果表。

- [x] 陈家俊通知权限修复后重发验收：用户确认可用范围已生效后，仅重发真实批次`20260826_070010`的分段富文本通知；不含模拟提示或本地数据路径。发送成功，message_id=`om_x100b67d028e87cacb39777d98ba62fa`，调用进程耗时1.765秒。此前230013失败记录保留，陈家俊单人送达已验证；其他成员不据此视为已验收。

- [x] 后续补充：分段富文本、具名表格/说明链接、分秒耗时；本地数据仅发配置中的周成业，其他人默认删除整行，正式正文无模拟提示。完整离线回归166项通过（2026-08-26 09:44，测试框架耗时0.270秒，进程墙钟0.881秒）。
- [ ] 陈家俊真实发送验收：2026-08-26核实联系人后，使用已完成的`20260826_070010`真实统计仅向陈家俊发送新版富文本（无本地路径），API返回HTTP400/code230013 `Bot has NO availability to this user.`，未送达、无message_id；调用进程耗时2.234秒。需管理员将陈家俊纳入应用可用范围并使其生效，再重发；未自动扩权或向其他成员发送。

- [x] 新模板仅向周成业预览：基于`20260826_070010`的真实结果，message_id=`om_x100b67d043b99cacb1bd816005f8f2a`；本次未群发、未改云端表名或列布局。
- [x] 统一完成模板，固定说明文档链接，取消完成通知端口行，“证据”改“本地数据”；后续正式全量和缓存恢复共用。
- [x] 后续写入改A:O：旧N:P先备份、单请求左移并清空旧P，未知表头阻断；表名含period_id与run_id且保持Token不变。
- [x] 后续动态读取应用协作者并逐人发送、去重和记录失败，不以文档分享名单代替。只读实查8人：周成业、王梦婷、刘婉婷、兰雅、陈家俊、展帅、谢琳莎、祝慧敏；本次除周成业预览外没有消息发送。
- [x] 离线验证：定向21项全部通过（0.104秒）；完整回归164项全部通过（0.288秒），命令为`PYTHONPATH=app .venv/Scripts/python.exe -m unittest discover -s tests -q`（PowerShell需先设置环境变量）。覆盖通知格式、名单去重、单人失败不阻断、发送审计和旧列布局迁移幂等性；接口使用模拟，不代表真实群发验收。`git diff --check`通过。
- [ ] 下一次正式运行实测：确认全部子表迁移为A:O、云端名称同步、逐人送达报告。该真实验收尚未运行，不能以单元测试代替。

> 历史执行准则由本文件顶部“当前执行索引”取代。先完整阅读当前SPEC；仅执行本次已授权且仍有效的任务，完成后记录验证和耗时；不得按旧待办恢复已取消需求，亦不得把REVIEWS建议直接当作已授权实现。

## Phase 0: 工程基线（2026-08-24）

- [x] Task 0.1：建立变更前 Git 基线并推送 `20260824` 分支
  - 验证：本地与 `origin/20260824` 均指向提交 `21d6d59`
- [x] Task 0.2：补齐工程规范文件
  - 内容：新增 `.gitignore`、`.env.example`、`docs/SPEC.md`、`docs/TASKS.md`、`docs/REVIEWS.md` 和 `sandbox/README.md`
  - 验证：`git status --short` 仅出现预期工程文件
- [x] Task 0.3：消除缓存测试对固定日期的依赖
  - 内容：有效缓存和过期缓存均相对当前时间构造，避免测试随日历自然失效
  - 验证：`python -m unittest discover -s tests -v`，63 项通过
- [x] Task 0.4：收敛配置来源并建立文档同步规范
  - 内容：普通配置只走 JSON；Secret只走根目录 `.env`/系统环境变量；补齐目录树、docs职责和变更维护矩阵
  - 验证：配置来源测试和完整离线测试通过；JSON模板不含 Secret 字段

## Phase R: 下一次需求变更

### Phase R1：飞书三表分离、多站点与本机归档（2026-08-24）

> 执行规则：严格按编号推进；前一任务未通过验证，不开始后一任务。PoC 只能操作明确标记的测试资源，原始周报和完整快照副本禁止写入。

每个 TASK 的统一完成标准：

1. 先运行受影响模块的离线单元测试，再运行完整离线回归测试。
2. 涉及真实飞书或 Amazon 时，严格按“只读/单 ASIN → 最小结果子表 → 单 Marketplace 小批量 → 全部子表全量”逐级验证；上一级失败不得扩大范围。
3. 每一次测试都必须记录开始时间、结束时间、墙钟耗时、测试范围、输入规模、成功/失败/跳过数量、产物路径和失败原因；禁止只记录“通过”。
4. Amazon 测试还必须记录每个 Marketplace 的 ASIN 数、P50/P95、HTML 总字节数、Captcha/429/503、技术异常率、归档失败数和风控等待/冷却耗时。
5. 飞书写入测试必须先保存目标结果子表备份，并记录 Spreadsheet Token 脱敏标识、Sheet ID、写入范围、行数和 API 耗时；原表及快照副本前后必须保持不变。
6. 全量测试每次单独生成 `run_id` 和机器可读摘要；摘要至少写入 `outputs/daily_runs/YYYY-MM-DD/{run_id}.json`，人类可读验证记录回写本 TASK。
7. 每项完成时在该任务下追加“验证记录”，包含实际命令、耗时、结果、证据路径和提交号；未完成上述记录不得勾选任务。

#### Gate A：飞书只读发现与资源创建

- [x] Task R1.1：固定周报登记表只读预检
  - 依赖：Phase 0 完成
  - 输入：`https://wit0jhu6kvu.feishu.cn/wiki/HwxpwCnZ7iV1o5klIGbc8wJHnrd`
  - 实现：新增 `weekly_registry_url` 配置及 `--inspect-weekly-registry`；只读解析 Wiki 节点、底层 Spreadsheet Token、登记 Sheet ID、表头和有效行
  - 历史R1.1规则：匹配实际 `序号/飞书链接/更新时间` 三列表；忽略链接为空的预留行，选择链接非空且序号最大的唯一行；没有新增链接时继续使用当前最大序号。该历史规则已被TASKS顶部的“周一早间沿用上周、周一下午切换本周”时点规则取代，不能直接作为当前调度实现依据。
  - 验证：覆盖富文本链接、普通链接、无有效行、非整数/重复序号、非法域名、无权限及最大序号无效但旧行有效；命令不产生任何飞书写入，也不故障回退
  - 文档：将实测 Spreadsheet Token、Sheet ID、字段名和权限结果回写 SPEC
  - 验证记录（2026-08-24）：首次环境运行因 PATH 无 Python 在 0.737s 内停止；补齐项目 `.venv` 后旧基线 66/66 通过（0.799s）。R1.1 初版定向测试 13/13（0.279s）、完整回归 76/76（0.539s）。真实表与草案结构不同，首次只读预检在 2.023s 安全停止；只读发现实际表头为 `序号/飞书链接/更新时间`，适配后定向测试 16/16（0.268s），真实只读预检通过：`Sheet1/c1fcd1`、有效链接序号 1、第 2 行、API 1.328s、命令总耗时 1.816s，全程无飞书写入；最终完整回归 79/79 通过（0.533s）。提交号待本阶段统一提交时补记。

- [x] Task R1.2：完整快照副本创建 PoC
  - 依赖：R1.1
  - 输入：固定登记表选择的当前周报；2026-08-24 实测底层 Token `WDOHs...ZnKL`（旧链接 `FPLxs...nSc/0GudVD` 不再覆盖登记表）
  - 实现：通过飞书 API 创建带测试标识的完整副本，重新获取副本 Token，并校验子表清单、行列数和关键表头
  - 保护：原表全程只读；复制失败、结构不一致或权限不足立即停止；不得启动 Amazon 抓取
  - 验证：原表哈希/元数据前后不变；测试副本完整且可读；记录准确 API、权限范围、Token 类型和父目录行为
  - 验证记录（2026-08-24）：新增副本定向测试 6/6 通过（0.002s）；中途完整回归 82/82 通过（0.124s），最终完整回归 85/85 通过（0.062s）。真实 PoC 前三次均在复制前因大型表 v3 元数据 HTTP 500/ReadTimeout 安全停止，未创建资源；切换 v2 metainfo 和批量关键区域读取后，复制 API 成功创建一个根目录 TEST 副本，但首次即时校验遇到异步 `server error`。随后从 Drive 根目录找回同一副本，未重复创建，并通过有限退避完成校验：原表 `WDOHs...ZnKL`、副本 `RMWPs...7nIh`，均为 sheet、19 个子表，复制前后原表及副本子表/行列容量/每表 A1:P10 一致，SHA256 `691d1b60...c1837`，最终验证总耗时 13.375s。证据：`outputs/poc_resources/r1_2_snapshot_pending.json` 与 `outputs/logs/run_20260824_1459.log`；提交号待本阶段统一提交时补记。

- [x] Task R1.3：独立结果 Spreadsheet 创建 PoC
  - 依赖：R1.2
  - 参考：`https://wit0jhu6kvu.feishu.cn/wiki/JbiQwDZXeiJan0k8ydRczfWNnFc`
  - 实现：通过飞书 API 新建带测试标识的独立 Spreadsheet；创建一个测试结果 Sheet，设置第 2 行 A:P 固定表头并读取校验
  - 保护：不得把参考表当成写入目标；不得写入原表或快照副本
  - 验证：返回独立 Spreadsheet Token、结果 Sheet ID 和可打开链接；A:P 表头、行列容量及编辑权限正确
  - 验证记录（2026-08-24）：副本/资源创建模块定向测试 8/8 通过（0.004s）；缺少 `--confirm` 的安全门验证在 1.7s 命令内安全退出且无写入；最终完整离线回归 87/87 通过（0.070s）。真实 PoC 一次通过：在应用 Drive 根目录创建唯一 `TEST_独立结果表_20260824_150420`，Spreadsheet `Ab0ws...Gnk3`；通过 sheets batch update 创建 `TEST_RESULT`（Sheet ID `2EwDSg`），仅写入并回读 `A2:P2` 固定 16 列，写入 API 0.390s、总耗时 4.641s。参考表、原周报及快照副本无写入。证据：`outputs/poc_resources/r1_3_result_spreadsheet.json` 与 `outputs/logs/run_20260824_1504.log`；提交号待本阶段统一提交时补记。

- [x] Task R1.4：每周资源生命周期与 manifest
  - 依赖：R1.3
  - 实现：新增 `--new-week`、weekly manifest、快照只读保护、结果表复用、并发锁和幂等初始化；固定登记表永久只读，资源 Token 仅写本地 manifest
  - 重建：仅允许 `--recreate-weekly-assets --confirm`；保留旧资源记录，不自动删除
  - 验证：同一 period 连续初始化两次返回相同快照和结果表；并发初始化不重复创建；缺少初始化的普通任务安全退出
  - 验证记录（2026-08-24）：新增 `weekly_assets.py`，实现 manifest 原子写入、周期排他锁、精确名称中断恢复、Token 写入保护、generation/history、显式重建和普通任务 `business_ready` 门禁。定向测试最终 5/5 通过（0.026s），覆盖并发锁、重复初始化复用、只允许结果表写入、重建保留历史及缺失/未映射 manifest 安全退出；最终完整离线回归 92/92 通过（0.086s）。无 `--confirm` 命令安全退出。真实 `--new-week --confirm` 首次创建 `seq-1` generation 1：正式快照 `GQJgs...Fnec`、正式结果表 `UWLds...AnNf`，15.859s；第二次只读校验后 `reused=True`，Token 完全一致、无重复资源，10.641s。manifest 状态/快照/结果均为 ready，快照 readonly、结果 readwrite，敏感键扫描无 App Secret、tenant token 或 Cookie。真实日常 `--dry-run --limit 1` 门禁在登记表读取后因 `business_ready=false` 于 10.6s 内退出，未启动 Amazon 或业务写入。证据：`outputs/weekly_runs/seq-1/weekly_manifest.json` 与对应运行日志；真实重建未执行，避免无必要新增云端资源，离线测试已验证 generation 递增和 history 保留。提交号待本阶段统一提交时补记。
  - 权限增强记录（2026-08-25）：新增`feishu_manager_open_id`和`ensure_permission_member`，每次创建或复用快照、独立结果表均幂等授予指定人工账号容器级`full_access`；缺少配置或授权失败时初始化不得ready。固定参考表协作者反查确认周成业Open ID为`ou_68e7d2af96255c1eeb1eea8021c80ea4`。现有正式快照`GQJgs...Fnec`和结果表`UWLds...AnNf`已补授权并回查为`openid/full_access/container`，两次均为首次新增；完整回归148/148通过（0.174s）。
  - 权限规则补强（2026-09-10）：发现复用`fixed_result.json`时旧逻辑仅标记`fixed_result=true`而未执行权限回查，导致已存在的结果表可能只有应用自身权限。现已改为创建、复制和复用固定结果表统一调用`ensure_permission_member(..., openid, full_access)`并回读；新增固定结果回归测试，定向测试22/22通过。当前前端验证表`TEST_前端真实页面验证_20260910_083515`已对周成业补授并回查为`openid/full_access`，权限列表中应用本身与周成业均存在。

#### Gate B：数据映射与 US/CA 抓取

- [x] Task R1.5：快照子表发现与结果表映射
  - 依赖：R1.4
  - 实现：从完整快照副本枚举全部子表、表头、数据范围和样例类型；确定进入结果表的 US/CPD 子表、有效行规则及 A:G 字段映射
  - 输出：保存脱敏发现报告；将准确子表名、Marketplace 路由和字段映射写入配置模板与 SPEC
  - 验证：每个配置子表能映射到唯一结果 Sheet；缺列、重名、空表和未知子表均有确定处理结果
  - 验证记录（2026-08-24）：新增 `weekly_mapping.py` 与 `--discover-weekly-mapping`，定向测试 3/3 通过；命令互斥门验证通过；配置解析检查通过（18表、US 11、CA 7）；阶段完整离线回归 95/95 通过（0.116s）。真实发现先后纠正三项错误假设：不得只扫描 A:Z，不得将缺少 ASIN 表头的业务命名 tab 直接排除，且飞书 batch range 返回标识不能用于多个 Sheet 的 ASIN 计数。最终按实际容量扫描并对有表头 Sheet 逐表读取：正式快照19表、18个同名业务映射（US11、CA7），仅排除 `BI源数据`；PD03 非空154/初步商品112，PD63 非空49/初步商品29，其余16个业务表为空模板并保留 `mapped_empty`。未知表0、结果名重复0、飞书写入0。`sheet_profiles` 已与18个`sheets`一一对应；manifest `mapping_ready=true`、`business_ready=false`。证据：`outputs/discovery/seq-1_sheet_mapping.json`、manifest与日志；提交号待本阶段统一提交时补记。
  - `seq-2` 验证记录（2026-08-25）：修复 direct Sheets 周报源和 batch 返回标题/sheet_id 错配；表头改为逐 Sheet 前10行读取。完整回归150/150通过。真实发现19表、映射18表（US11/CA7），全部18个业务表均有数据；链接审计有效725、无效0。证据：`outputs/discovery/seq-2_sheet_mapping.json`、`seq-2_product_links.json`。

- [x] Task R1.5a：正式全量恢复单浏览器四 Tab
  - 实现：`workers=4`，`run_fetch` 从配置创建四个 worker，并向同一个 `AmazonBrowser` 传入四个 Tab；每个 worker 在解析、归档校验及1～3秒等待后才释放 Tab。
  - 验证：单 Tab 不重复获取、异常重建不暴露和四 worker 配置回归通过；2026-08-25 单 Tab 全量批次 `20260825_101629` 在完成 PD03 前后停止，已落盘103份 HTML 共2,657,089,655字节，随后使用同一 run_id 恢复，已完成缓存不得重新下载。

- [x] Task R1.5b：全量逐行收口与明确结果通知
  - 实现：批次异常率只触发 degraded 告警，不再让安全行整批零写入；每行仍由 ASIN身份、币种、归档和 run_id 门禁独立决定写入或阻断。最终通知区分“完成/部分完成（需恢复）”，包含写入数、阻断数、结果表及证据位置。
  - 验证记录（2026-08-25）：完整回归150/150通过。批次 `20260825_101629` 经 `weekly-push-only` 将548行写入并逐行回读，177行独立阻断（172条 `identity_mismatch`、5条 HTML归档不可用）；报告为 `outputs/daily_runs/2026-08-25/20260825_101629_weekly_push.json`。周成业已收到“部分完成（需恢复）”通知及结果表、阻断数和证据位置。
  - 404顺序修正（2026-08-25）：明确 `Page Not Found` 标题优先于最终URL身份校验；正常商品页才比较路径 ASIN，查询参数忽略。既有172份诊断标题复核为明确404共0份，因此本批次阻断统计不因该修正改变。

- [x] Task R1.5c：HTML局域网只读服务与批次级通知
  - 实现：参考 `D:\projects\BDLD\src\serve_folder.py`，新增固定 `htmls` 根目录、路径Token、隐藏路径阻断、健康端点和状态文件的只读 ThreadingHTTPServer；正式 N 列使用局域网HTTP URL。Windows登录任务自动启动服务，Private网络防火墙开放TCP 8765，计划抓取前强制健康检查。
  - 通知：取消初始化、发现、审计、A:G同步、子表、PoC和dry-run阶段通知；正式全量仅最终汇总一次，异常额外通知一次，均包含完整耗时和端口状态。
  - 验证记录（2026-08-25）：服务监听 `0.0.0.0:8765`，健康检查通过；实际局域网IP `192.168.108.49`，现有HTML HEAD返回200且Content-Length与文件一致；Windows任务 `AmazonDaily_HTML_Server` 为Ready，防火墙规则 `AmazonDaily HTML LAN 8765` 为TCP/8765。

- [x] Task R1.6：MarketplaceProfile 与 ASIN/商品链接标准化
  - 依赖：R1.5
  - 实现：建立 US/CA Profile；支持从纯 ASIN、超链接文本和公式超链接提取 ASIN；生成受控 `amazon.com`/`amazon.ca` 标准链接
  - 验证：覆盖合法、大小写、空白、恶意域名、跟踪参数、无效 ASIN 和 CPD；所有有效行均生成 P 列标准链接
  - 验证记录（2026-08-24）：新增 `product_links.py`、US/CA MarketplaceProfile、严格域名/跨站保护、纯 ASIN/普通 URL/飞书富文本/公式提取、标准 URL 生成和 `--audit-product-links`。定向测试最终 7/7 通过（0.002s），命令互斥门通过；最终完整离线回归 102/102 通过（0.110s）。真实只读审计 2.250s（命令墙钟约43.8s，主要为飞书响应等待）：PD03 商品112、PD63商品29，共141个有效标准 `amazon.com` URL；无效商品0；62个成本、广告、颜色、利润等非商品标签明确跳过。CA 7表本周为空，`amazon.ca`/CAD 的纯 ASIN、URL、富文本、公式和跨站拒绝均由离线测试覆盖，真实 CA 页面留给 R1.7。manifest `link_rules_ready=true`、`business_ready=false`，飞书写入0、Amazon访问0。证据：`outputs/discovery/seq-1_product_links.json` 与 manifest；提交号待本阶段统一提交时补记。

- [x] Task R1.7：复用抓取器支持 US/CA 上下文
  - 依赖：R1.6
  - 实现：参数化域名、语言、邮编、Cookie、代理和币种；按 Marketplace 分批且初始单并发；加入单 ASIN 绝对 deadline、异常 tab 重建、归档成功后的随机 1～3 秒下一商品等待，以及 Captcha/429/503/连续失败时随机 60～180 秒冷却
  - 配置：新增 `post_archive_delay_min/max` 和风险冷却配置；迁移或废弃旧 `delay_min/max`，禁止两套延迟重复执行
  - PoC：确认加拿大邮编、出口要求、主价格、Coupon/Code/Save 选择器和 CAD 页面证据
  - 验证：US 历史基线不回归；CPD 样例使用 amazon.ca/CAD；慢加载、卡死和重试不突破总时间预算
  - 验证记录（2026-08-24）：抓取器按 MarketplaceProfile 复用，US/CA 分别使用 `amazon.com`/`amazon.ca`、`en-US`/`en-CA`、90210/`M5V 3A8` 与 USD/CAD；每个 Marketplace 强制一个活动 tab。加拿大地址弹窗的分段邮编输入已适配；因 Amazon.ca 导航栏会截断末位字符，CA 采用专属可见前五位校验，US 仍要求完整邮编，二者有反向单测。旧 `delay_min/max` 已从默认值及两份 JSON 移除，新增归档后 1～3 秒等待和风险 60～180 秒冷却；风险冷却会被单 ASIN 绝对 deadline 截断。独立只读 PoC（不写飞书、不归档 HTML）同一 ASIN `B0BN5CJFCX`：US `status=ok`、USD、邮编验证通过、20.467s；CA `status=ok`、CAD、`visible_prefix5` 验证通过、21.392s。抓取器定向测试 8/8 通过；最终完整离线回归 107/107 通过（0.091s）。证据：`outputs/poc_resources/r1_7_us_B0BN5CJFCX.json`、`outputs/poc_resources/r1_7_ca_B0BN5CJFCX.json`；HTML 归档及其后等待仍由 R1.9 验证，未在本 PoC 重复导航或提前实现。

- [x] Task R1.8：币种模型与跨币种保护
  - 依赖：R1.7
  - 实现：模型、缓存、CSV、manifest 和摘要增加 `currency_code`；US=USD、CA=CAD；跨币种或币种未知禁止一致性比较
  - 验证：USD、CAD、未知币种和错误币种组合测试通过；O 列只允许 USD、CAD 或明确空值
  - 验证记录（2026-08-24）：`CrawlResult.currency_code` 默认改为空值，真实抓取按 MarketplaceProfile 明确赋值；新增 `currency_error` 状态，US/USD、CA/CAD 才允许一致性比较，未知币种及 US/CAD、CA/USD 等组合保留解析值但 `match=-`、`price_diff=null`，不换算汇率。缓存 schema 升级至 3，签名及缓存 manifest 纳入 Marketplace、商品 URL 和币种，旧的无币种缓存自动失效；缓存恢复完整还原上述字段。CSV 新增 Marketplace、币种、标准商品 URL，金额折扣单位跟随 USD/CAD，百分比仍为 `%`；每日摘要新增 `currency_counts`，周 manifest 的链接审计写入固定币种模型。定向测试 39/39 通过（0.268s），最终完整离线回归 110/110 通过（0.105s）。O 列实际飞书写入仍由 R1.14 与固定 A:P 布局一并实现，本任务只完成数据模型和保护门。

#### Gate C：单文件 HTML 归档

- [ ] Task R1.9：同 tab 自包含 HTML PoC
  - 依赖：R1.8
  - 实现：从已加载 DrissionPage tab 生成唯一自包含 `.html`；不重复导航；解析 HTML 仅计算内存哈希；文件原子落盘并生成 manifest
  - 已知证据：独立 SingleFile CLI 对 `B0BN5CJFCX` 两次在 `Runtime.evaluate` 断开，返回码 0 但无文件；不得只按退出码判定成功
  - 验证：US/CA 各至少三种页面状态断网打开，标题、ASIN、价格、促销、主要图片和核心布局可见且无外部资源请求；记录 P50/P95 耗时与体积并重定 `per_asin_timeout`；证明下一个 ASIN 只能在文件落盘校验及随机 1～3 秒等待后开始
  - 阶段记录（2026-08-24，尚未完成 Gate）：固定官方 `single-file-cli@2.1.3`，通过 `tools/singlefile` 导出 hook/main/zip 脚本；商品导航前向当前 DrissionPage tab 注入 hook，页面解析后在同一 tab 调用 `getPageData()`，并以浏览器 Blob 下载落盘，DevTools 只回传小状态对象，避开此前大 HTML 经 `Runtime.evaluate` 回传导致的连接断开。US/CA 正常商品 `B0BN5CJFCX` 已通过原子落盘、HTML/ASIN/最小体积/SHA-256/活动外部资源检查与离线 Chromium 验收：US 54,432,503 bytes、归档15.202s、断网加载图片284、价格25.59、网络资源0、归档后等待1.508s；CA 14,936,737 bytes、归档8.672s、断网加载图片214、价格78.63、网络资源0、归档后等待1.025s。明确不存在的 `B0ZZZZZZZZ` 形成真实 `page_not_found`：US 80,330 bytes/0.407s/等待2.118s，CA 63,044 bytes/0.405s/等待1.348s；断网标题均为 `Page Not Found`、归档源码含目标 ASIN、网络资源0。归档最小体积已按状态区分：正常/售罄100KB，明确404为10KB。首次 US 文件被验证器误报56项 `data-src` 惰性元数据，实际 `src` 均为 data URI；验证器已限定为浏览器会主动加载的属性并增加反向单测。`B000000000` 实测为正常商品，不能伪记售罄。当前两站各覆盖 `ok + page_not_found`；仍需真实 `sold_out`、促销/核心布局细化、P50/P95 与 deadline 重定后才能勾选 R1.9。
  - 性能阶段记录（2026-08-24，最近秩，小样本）：`outputs/poc_resources/r1_9_archive_stats.json` 汇总 US 9 份归档，耗时 P50/P95=9.420s/15.202s、体积 P50/P95=32,819,003/57,924,629 bytes；CA 4 份归档，耗时 P50/P95=7.780s/9.047s、体积 P50/P95=14,712,749/14,936,737 bytes。多个历史/公开不可售候选当前均实测为 `ok`，未伪记为售罄。`B0DH2X9XTN` 暴露 Amazon 自定义播放器 `<div poster=https://...>`；SingleFile 视频选项没有移除该活动外链，现于价格解析完成后、归档前移除任意元素的 `poster` 属性，其他活动外链仍严格阻断。由于缺真实 sold_out 且统计样本偏小，暂不据此重定生产 deadline 或勾选任务。
  - sold_out 查找记录（2026-08-24）：本周/历史样本及 Amazon Seller Central 公开案例 `B09PVJVS15`、`B09PVHFYQS`、`B09PVGGRV1`、`B09Q5VQ7NS`、`B0DH2X9XTN`、`B0BNBRZ7NY`、`B0FH63B8D7`、`B0CWXT3NDV`、`B083WMVTTX`、`B001AHHTOM` 在当前 90210 上下文均实测为 `ok`。为避免为了不可控库存状态持续请求 Amazon，停止候选盲测；真实 sold_out 必须在后续最小子表或七天运行中自然出现时由同一归档链自动留证，禁止修改 DOM、用历史文案或把404伪装成售罄。R1.9 在此之前保持未完成。
  - 正式链接入记录（2026-08-24）：`run_fetch` 已在商品导航前向同一 tab 注入 SingleFile，页面状态为 `ok/sold_out/page_not_found` 时写入受管日期/run_id/Sheet顺序/行顺序路径、相邻 manifest 和批次 manifest，随后生成真实 `file:///` URL并等待1～3秒；失败也执行相同等待且不生成 URL。导航、解析和归档共用原 `per_asin_timeout` 绝对预算，商品间等待在该 ASIN预算外但会阻塞下一商品。`CrawlResult` 与缓存 schema 4 保存路径、URL、哈希、体积、归档耗时/状态/错误和等待；恢复时若成功页归档文件已不存在则缓存整表失效，禁止拼接旧 URL。正式链成功/失败编排与缓存专项7/7通过；接入 manifest 每日链后完整离线回归140/140通过（0.163s）。正式快照最小 dry-run `20260824_175155` 处理 PD03 首条 `B0C5R56QTF`：`ok/USD`，HTML 30,129,933 bytes、归档9.531s、归档后等待2.457s、批次总耗时46.922s；缓存schema 4、相邻/批次manifest和真实本机URL齐全。Chromium断网验收标题/ASIN/价格可见、图片293张、外部网络请求0。未写飞书。仍缺真实 `sold_out`，任务保持未完成。

- [ ] Task R1.10：MHTML 对照与失败策略 PoC
  - 依赖：R1.9
  - 实现：使用 CDP `Page.captureSnapshot` 生成 MHTML 对照，比较完整度、耗时、体积和可读性；明确自包含 HTML 失败时是否阻止整行写回
  - 验证：使用与 R1.9 相同样例；默认用户交付仍只有普通 `.html`，MHTML 不进入长期归档
  - 文档：将最终失败策略写回 SPEC，清除对应“待确认”
  - 阶段记录（2026-08-24）：新增 `app/mhtml_compare.py`，在同一已加载 tab、一次导航内先生成普通自包含 HTML，再调用 CDP `Page.captureSnapshot(format=mhtml)` 生成临时对照；MHTML 使用 MIME/ASIN/最小体积/SHA-256 和原子落盘校验，默认在比较报告写完后删除，不进入五日归档，也不作为 N 列回退。原子捕获专项测试3/3通过，最终完整离线回归132/132通过（0.238s），PoC目录确认无遗留 `.mhtml`。正常页 `B0BN5CJFCX`：US HTML 29,767,637 bytes/8.889s，MHTML 17,767,049 bytes/3.391s（体积比0.5969）；CA HTML 14,665,100 bytes/9.187s，MHTML 5,638,101 bytes/1.250s（0.3845）。真实404 `B0ZZZZZZZZ`：US HTML 99,030 bytes/0.375s，MHTML 103,792 bytes/0.046s（1.0481）；CA HTML 73,944 bytes/0.375s，MHTML 78,040 bytes/0.030s（1.0554）。SPEC 已固化失败策略：`html_archive_required=true` 时归档链任一校验失败阻断该行整组 H:P，MHTML 仅诊断。由于依赖任务R1.9仍缺真实 `sold_out`，尚不能使用完全相同的三状态样例，本任务保持未完成。

- [x] Task R1.11：归档目录、命名、五日保留与磁盘保护
  - 依赖：R1.9
  - 实现：配置 `html_archive_root`、日期/run_id/Sheet顺序/ASIN命名、manifest、路径边界、磁盘阈值和五个自然日清理
  - 验证：临时根目录模拟六天和每天两批数据，只删除超期目录；目录穿越、联接越界、空间不足和部分文件写入失败均安全处理
  - 验证记录（2026-08-24）：新增 `ArchiveStorage`；当前本机根目录配置为 `D:\projects\amazon_daily_structured_20260821\htmls`（已加入 Git 忽略），模板使用可迁移示例。路径固定为日期/run_id/三位Sheet顺序/五位行顺序_ASIN，所有动态片段白名单清洗且最终解析路径必须留在根目录。默认保留5个自然日、最低40GB、归档必需；六天×每天两批模拟只删除第六天的一个日期目录，非日期目录不动。磁盘检查针对根目录所在卷；根目录或日期目录为符号链接/Windows Junction 时拒绝；manifest 同目录临时文件原子替换，替换失败清理临时文件。归档存储定向测试8/8通过；最终完整离线回归122/122通过（0.135s）。未对真实配置根执行清理，也未删除任何用户文件。

- [ ] Task R1.12：本机 HTML `file:///` URL
  - 依赖：R1.11
  - 实现：仅对已落盘且校验成功的 Windows HTML 绝对路径生成 URL 编码后的 `file:///` URL；不启动 HTTP 服务或开放端口
  - 验证：中文、空格、盘符和特殊字符路径正确；N 列指向真实文件；记录飞书桌面端点击行为及复制打开回退方式
  - 阶段记录（2026-08-24）：已实现仅对受管根目录内、真实存在的 `.html` 生成 `Path.as_uri()` URL；不存在、非HTML或越界路径全部拒绝，中文、空格和 `#` 编码测试通过。尚未写入飞书 N 列，也未完成飞书桌面端点击/复制打开实测，因此任务保持未完成。

#### Gate D：独立结果表与端到端运行

- [x] Task R1.13：独立结果表 A:G 初始化与同步
  - 依赖：R1.5、R1.4
  - 实现：按映射创建/复用结果子表；从完整快照副本筛选并写入 A:G；写前保存本地备份；源行无效时保留可诊断结果
  - 验证：新增、删除、重排、空行、重复 ASIN、缺字段和多子表测试通过；原表和快照副本前后不变
  - 验证记录（2026-08-24）：新增 `app/weekly_result.py` 和显式命令 `--sync-weekly-result-base --confirm`；所有源子表必须在任何写入前完成快照 Token、Sheet ID、重复 ASIN和字段预检，写入目标受 manifest Token 门禁保护。专项测试 5/5、最终完整离线回归129/129通过（0.152s）。正式 `seq-1` 运行 `base-20260824-172752` 创建/复用18个结果子表并逐表备份、写入、回读校验；PD03 112行（保留无效源行1）、PD63 29行（保留无效源行2），其余16个空模板写入标准 A:P 表头，共141行，耗时125.640s，manifest `business_ready=true`。事后只读结构核验确认原周报和完整快照 SHA256 均与初始化 manifest 一致，二者仍相等；独立结果 Spreadsheet 当前19个子表（18个业务结果表及1个创建时默认表），所有业务映射均已登记结果 Sheet ID。首次真实尝试因 `.env` 为目录而在认证阶段停止，第二次因大表v3元数据超时而在预检阶段停止，两次均无飞书写入；已增加固定凭证文件兼容和v3→v2元数据回退。
  - `seq-2` 验证记录（2026-08-25）：独立结果表 `Epads8MQkhkuBctjl3lcqLUvnCg` 已创建/复用全部18个结果子表并同步 A:G 共725行，逐表回读通过，耗时132.672s，周成业收到带结果表位置的完成通知。

- [x] Task R1.14：H:P 九列同批写回
  - 依赖：R1.10、R1.12、R1.13
  - 实现：H:M 写六个价格校验字段，N 写本机 HTML URL，O 写币种，P 写 Amazon链接；九列必须来自同一 ASIN 和同一 run_id
  - 保护：布局预检失败、技术异常比例超阈值、目标价缺失超阈值或跨币种时禁止受影响写回；`push-only` 不得跨批拼接
  - 验证：mock 覆盖 ok、invalid、missing、push-only、断点恢复和风险拦截；真实环境先测试结果表单 Sheet，再全量
  - 阶段记录（2026-08-24）：新增 manifest 驱动 `--weekly-run`，源 Token 只取本周完整快照、目标 Token 只取独立结果表；旧静态 `full_flow` 不用于正式验证。`write_weekly_result_columns` 固定写 H:P 九列，要求结果表 A:P 表头、ASIN行、结果 Sheet ID、独立目标 Token 和每条 `run_id` 全部匹配；归档失败或 `currency_error` 行不写，其他连续行合并 Range。新增 `--weekly-push-only --run-id ... --confirm`，不抓取、不刷新A:G，只接受当前周期/快照/结果Token一致、schema 4、HTML存在且SHA-256一致的明确批次缓存或同批原子 weekly bundle；bundle 同时保留正常、页面异常、技术失败和 source-invalid，禁止跨批拼接。写前备份，写后逐行回读。专项测试及最终完整离线回归144/144通过（0.174s）。单行批次 `20260824_175155` 已写回 `B0C5R56QTF` H:P 1行。多行批次 `20260824_180420` dry-run耗时72.297s，bundle含2条ok及1条source-invalid、2份HTML均存在；随后正式写入并回读PD03 H:P 3行、阻断0行，备份、bundle和push报告均存在。N/P被飞书规范化为URL富文本且链接值与bundle一致，source-invalid 行N为空并保留诊断。首次单行严格回读因未兼容富链接结构而失败但实际值正确，修正后幂等验证通过。限定运行现已在limit后保存精确快照，避免恢复签名与缓存行集不一致。本阶段当时尚缺技术失败、断点恢复和多子表真实写入，后由下一条完成记录收口。
  - 完成记录（2026-08-24）：多子表批次 `20260824_180917` 对 PD03、PD63 完成真实抓取和 H:P 写后回读，共5行、阻断0。全量批次 `20260824_181228` 覆盖本周全部非空结果子表：PD03有效111行、PD63有效27行及3条源数据无效行；耗时3827.687s（63.795分钟），138个有效页面均为USD，首次成功归档133份自包含HTML、总计3,204,368,773 bytes，首次写入133行、阻断8行。8项分别暴露 Windows 临时文件锁及 Amazon Ember字体/AdChoices精灵图CSS外链；修复为浏览器退出后受管临时目录重试清理、仅对白名单非核心CSS资源剥离且未知外链继续阻断。恢复批次 `20260824_192207` 仅重抓8个明确ASIN，耗时238.562s，8/8为`ok`，归档约169MB，临时文件遗留0；逐文件断网Chromium验收均显示ASIN、价格与238～304张图片，网络请求均为0。`--weekly-push-only` 随后写入并回读8行、阻断0。最终只读核验独立结果表共141个实际ASIN行：138个有效行H:P完整，3个source-invalid行保留H:M诊断、N为空、O为USD、P为标准Amazon URL；PD03=112行、PD63=29行，其余16个模板为空。最终完整离线回归147/147通过。证据：`outputs/daily_runs/2026-08-24/20260824_181228_weekly_summary.json`、`20260824_192207_weekly_bundle.json`、`20260824_192207_weekly_push.json`，以及对应 `htmls/2026-08-24/<run_id>/`。

- [ ] Task R1.15：日志、摘要、失败恢复与每日两次调度
  - 依赖：R1.14
  - 实现：统一 run_id；记录分站、币种、归档、磁盘、清理、飞书写回和脱敏配置；日志按天保留；计划任务每天两个固定时段无交互执行
  - 验证：连续两次模拟运行产生独立可追溯记录；中断后恢复不重复创建周资源、不跨 run_id 写回、不丢失已完成归档
  - 阶段记录（2026-08-25）：调度按用户最终确认改为北京时间每日07:30和15:30、截止2026-08-31。两个Codex自动任务已暂停，Windows任务`AmazonDaily_0730/1530`已安装并回读为Ready，下午下次运行2026-08-25 15:30、上午下次运行2026-08-26 07:30；任务以Administrator“仅用户登录时运行”，不依赖GPT界面。新增无交互`bin/scheduled_run.ps1`，使用文件独占锁防止上一批未结束时重复启动，执行manifest驱动的`--weekly-run --confirm`并记录独立调度日志；持锁专项验证返回75且未启动抓取。安装入口`bin\schedule.bat --install`重复执行成功。计划任务禁止创建/重建周资源，技术失败只允许按失败ASIN生成独立恢复run_id并用weekly-push-only补写。当天07:30已过，手动补跑正式全量批次`20260825_085431`已启动；R1.15仍需补齐增强摘要并通过连续两次计划运行验证后才能勾选。

- [ ] Task R1.16：本机单批全量验收
  - 依赖：R1.15
  - 实现：按 SPEC 第 19.3 节从只读检查逐级执行；先选择数据量最小的一个结果子表跑通完整 A:P 和 HTML 流程，再扩到单 Marketplace，最后执行一次完整 US/CA 全量批次
  - 验证：最小子表、单 Marketplace 和全量三个阶段分别有独立 run_id 与耗时记录；全部配置子表完成 A:P、HTML、CSV、日志、manifest、备份和清理；输出单批验收报告并关闭阻断缺陷
  - 阶段记录（2026-08-24）：已完成最小单行、PD03多行、PD03+PD63多子表和一次本周全部非空子表的全量实跑；全量主批次与8项恢复批次的时间、HTML体积、飞书写回及离线证据已记录在R1.14。由于本任务依赖的R1.15日志/调度尚未完成，且本周CPD子表当前无数据，R1.16仍保持未完成，禁止用本次阶段结果跳过依赖门。

#### Gate E：稳定性与最终部署

- [ ] Task R1.17：本机一周全量稳定性验收
  - 依赖：R1.16
  - 实现：连续至少七个自然日、每天两个全量计划批次，覆盖全部已配置 US/CA 子表、A:P、HTML归档、五日清理、日志和失败恢复
  - 验证：14 个计划时段均有最终成功且可审计的 run_id；每批记录开始/结束时间和总耗时；失败必须修复并补跑；输出成功率、单批/P50/P95耗时、HTML体积、磁盘峰值、抓取异常、写回和清理汇总
  - 阶段记录（2026-08-25）：稳定性窗口固定为2026-08-25～2026-08-31，每天07:30、15:30。首个07:30时段由`20260825_085431`于08:54手动补跑，其余13个时段交由Windows任务计划程序。只有每个时段最终成功、证据齐全并完成至少一次受控失败恢复演练后才计入14/14；当前计数须等待首批完成，不提前记成功。

- [x] Task R1.18：Docker PoC 与部署说明（按 2026-09-11 决策取消）
  - 处理：不再实现或维护 Dockerfile、Compose、容器入口、容器 cron、镜像迁移和 Linux Feedback bridge；对应文件与专用静态测试已删除。
  - 交付边界：生产只使用 Windows 宿主机本地 `.venv`、Chromium/紫鸟会话和隐藏计划任务。历史 Docker 构建记录保留在本文件和 `REVIEWS` 的历史段落，仅作追溯，不计入当前支持矩阵。

> Phase R1 完成定义：R1.1～R1.17 按各自验收证据完成；R1.18 已取消，不再作为部署前置条件。当前生产部署唯一支持 Windows 宿主机本地路径。

## 折扣准确性修正（2026-08-25）

- [x] Task D1：促销证据限定到当前商品购买区
  - 根因：Coupon 通用正则扫描整页，把加拿大样本 `B0CLNRF9P3`、`B0D965FRJ4` 评论中的 `30% off coupon` 当作当前商品优惠，覆盖了真实的 `Save 10% at checkout`，导致类型、折扣值和最终价一起错误。
  - 实现：Coupon 要求结构化 Coupon 控件锚点，Saving 金额仅在同一控件取值；Code 要求购买区 alert/promotion 锚点；规则版本升级为 `2026-08-25-v2`，禁止复用旧解析缓存。
  - 验证：新增评论 Coupon、评论 Code、远端无关 Saving 金额三个反例；解析/计算定向回归 42/42 通过。用已归档真实 HTML 离线复核：上述两个误判样本均修正为 `code=10%`、无 Coupon；真实加拿大 Coupon 样本 `B0G6YKCH9B` 仍正确识别 `Apply 10% coupon`。
  - 全套离线回归：共运行157项，其中153项通过；4项 `test_flow` 因测试用例共享秒级 run_id、并发写同一个 summary 文件发生 Windows `PermissionError`，与本次解析变更无关，定向相关测试全部通过。该既有测试隔离问题需单独修正后再作为全套 Gate 证据。

- [x] Task D2：交付批次暂停 HTML 下载
  - 用户决定先交付价格与折扣数据，后续再恢复 HTML。
  - 已安全中止带归档批次 `20260825_140115`；其已生成的139份 HTML 保留，不删除、不写作完整批次。
  - 当前运行配置关闭 HTML 归档及归档必需门禁；N列允许为空，H:M、O:P及其他批次产物继续正常生成和写入。

- [x] Task D3：2026-08-26 07:00一次性正式运行
  - 已创建Windows任务`AmazonDaily_20260826_0700`，Schedule Type为One Time Only，Next Run Time为2026-08-26 07:00，状态Ready且Enabled。
  - 任务调用`bin\scheduled_run.bat`，不依赖GPT界面；运行时使用当前无HTML交付配置。
  - 限制：Logon Mode为Interactive only，届时电脑必须开机且Administrator保持登录。
