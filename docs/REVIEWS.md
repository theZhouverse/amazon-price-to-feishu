# REVIEWS：当前未完成的真实验收与剩余边界

## 2026-09-10 生产全量运行与 Feedback 固定子表回读（最新）

- 本地工作区已与 GitHub `origin/fix-codescan-20260826` 同步到合并提交 `ffcd6ea`；本次仅将生产配置 `feedback.enabled` 从 `false` 改为 `true`，未复制或打印任何 Secret。
- 使用隐藏窗口运行原入口 `app/main.py --weekly-run --confirm --scheduled-slot weekday_0730`，运行编号 `20260910_121408`，来源周期 `seq-4`，选择模式 `weekday_steady`。HTML 归档与局域网服务保持关闭，既有 `htmls` 历史文件未删除，也没有产生新 HTML 下载。
- 18 个价格业务子表（US 11、CA 7）均完成实时抓取；页面状态为 `ok=489`、`identity_mismatch=196`、`source_data_invalid=29`、`parse_error=5`，前端七项累计 `pass=2285`、`fail=1136`、`unknown=1612`。固定结果 Spreadsheet 仍为 `Epads8MQkhkuBctjl3lcqLUvnCg`，基础 A:G 同步 719 行，H:V 实际写入 489 行，230 行因身份/源数据/解析门禁阻断；批次状态为 `partial`，未将阻断行伪装成成功。
- CA 子表已实际执行并写入：币种统计 `USD=549`、`CAD=170`；`CPD03` 等代表表回读均为 A:V 22 列。CA 仍存在较高 `identity_mismatch`，后续应依据本批 bundle 的请求 ASIN/最终 ASIN 证据优化站点导航，不能只看写入总数判断 CA 已完全修复。
- Feedback 独立阶段按两个 Seller Central 店铺串行执行，首次窗口 `2026-09-04` 至 `2026-09-10`（`initial_7d`），两店各读取 2 页、合计 80 条原始记录，评级 1–3 星窗口内 10 条，二级订单详情 10/10 完整；两店状态均为 `ok`，阶段耗时 390.344 秒。固定子表 `Feedback差评汇总`（Sheet ID `41u25y`）写入 10 条、范围 `A1:I11`，9 列写后回读通过，状态账本已推进；远端回读确认表头严格 9 列且 10 条非空业务行。空白遗留子表 `Feedback????`（`3lCGeQ`）未使用。
- 任务完成后只发送一次协作者通知，8 人成功、0 人失败；通知回执位于 `outputs/daily_runs/2026-09-10/20260910_121408_notifications.json`。完整墙钟耗时为 5858.296 秒（约 97.64 分钟），结果表名称已同步为 `Amazon周报前端价格捕捉_2026-W37_20260910_121408`。
- 本批可复核证据：`outputs/daily_runs/2026-09-10/20260910_121408_weekly_bundle.json`、`20260910_121408_weekly_summary.json`、`20260910_121408_delivery.json`、`20260910_121408_notifications.json`；Feedback 证据目录为 `outputs/feedback/20260910_121408/`。此前本文件中“Feedback 未启用/业务行 0”的记录是历史时间点证据，不能覆盖本节最新运行结果。

## 2026-09-09 Feedback桥接兼容与单店首次7日只读阻断（最新）

- 已修复真实紫鸟 CLI 的 Windows `.cmd` 参数边界：页面 JavaScript 统一压成单行，中文 marker/分页/详情标签改为 ASCII 运行时字符构造；CLI 更新通知不再被误判为页面风险，评论内容和内部签名也不再参与登录/验证码/风控文本扫描。新增详情页/列表页有界渲染重试、分页上限前不再继续点击，以及订单详情返回后重新访问 Feedback Manager 并恢复原页码的策略。
- 真实单条详情—返回探针已通过：详情字段长度为订单号19、订单商品编号15、ASIN10、SKU21；重新进入 Feedback Manager 后 marker、20行列表和唯一分页按钮均恢复成功。两个店铺仍保持串行、单独关闭。
- 冬豚首次7日只读流程已按真实慢速策略启动；因当前页面历史分页较长，未在无进展状态下继续翻到安全上限，已主动停止并保留 blocked 证据。该探针不是成功验收，业务行写入数为0，未启动北蓉正式流程、未写入飞书、未推进状态账本。
- 本轮浏览器定向回归为26项通过；`feedback.enabled=false`继续保持，固定子表只保留已验证的9列表头。

## 2026-09-10 Feedback日期边界与页面标记复核（最新）

- 新增`page_date_order=newest_first`登记和日期边界门禁：只有当前页日期全部可解析、顺序符合登记方向且整页早于窗口起点时才停止翻历史页；日期缺失或排序异常直接阻断，不猜测截断。生产默认`max_detail_attempts=0`表示不限制详情次数，探针可单独传入详情上限，达到上限只记录阻断并关闭店铺。
- 真实结构诊断确认当前Feedback Manager仍有20条当前页记录和两个`h3`标记候选，其中一个是不可见/响应式副本；代码已只统计可见标记，避免将隐藏副本误判为结构异常。诊断只回传URL、数量和长度，没有落盘评论、订单或详情值。
- 项目级CLI在正常Windows用户权限下`doctor`、Keychain、ZClaw Bridge、客户端登录和`store list --all`均通过；受限运行环境读不到Keychain时未改凭证、未重建配置。单店探针在页面门禁修复后进入低星详情链路，随后因探针上限安全阻断；没有写入飞书，没有推进状态账本，也没有将该探针记为成功验收。
- 定向Feedback回归从26项增加到30项并全部通过；`feedback.enabled=false`保持，正式双店窗口、业务写入/读回、10日清理、3日增量和07:30调度仍未完成。
- 两店各一页容量探针均通过：每店当前页20行、低星候选各6行，冬豚日期范围为`2026-08-27`至`2026-09-08`，北蓉为`2026-09-03`至`2026-09-07`，两店均有唯一可用分页按钮且按钮未禁用；单页读取耗时约22秒。该探针不点击订单、不翻页、不写表。
- 尝试进入飞书写入验收时发现当前开发副本运行环境缺少`FS_APP_SECRET`，认证返回`invalid param`；未从其他项目凭证入口自动复用Secret，未读取或写入目标子表。因此完整首次7日写入/回读、状态推进和后续3日验收仍被凭证门禁阻断。
- 双店首次7天真实只读运行`feedback_readonly_full_20260910`通过：窗口为`2026-09-04`至`2026-09-10`，总耗时`390.156s`；两店各读取2页/40行，窗口内分别4条和6条，详情分别4/4和6/6完整，均在第2页安全达到日期边界；没有写飞书或推进状态。
- 后续3天真实只读运行`feedback_readonly_incremental_20260910`通过：窗口为`2026-09-08`至`2026-09-10`，总耗时`142.234s`；冬豚读取2页/40行并得到2条详情2/2，北蓉读取1页/20行且窗口内0条，均安全达到日期边界；没有写飞书或推进状态。
- 使用本地Feishu替身完成发布链路回归：10条首次写入、相同输入重复运行、注入1条过期记录后的10日清理、9列写后回读和内存状态推进均通过。真实固定目标仍只读确认`Feedback差评汇总`表头完全匹配且实际业务行0；按用户要求没有对生产表做备份、清空或写入。
- 用户明确授权后，仅在当前只读进程中使用生产目录`.env\飞书凭证.txt`完成飞书认证；固定Spreadsheet/`Feedback差评汇总`子表身份、9列表头和实际非空业务行0均已回读确认。Secret未复制到开发目录、仓库或日志；本轮没有调用备份、清空、写入或状态保存接口。

## 2026-09-09 前端实时商品页只读小样本

- 使用 Amazon.com 直接商品页做只读 UI 核验，未登录、未输入凭证、未点击购买或提交控件。`B0CLNJH915` 当前 URL 与商品标题 ASIN 对应，主图、当前选中尺寸`4' x 6'`、可见`Amazon's Choice`、`1 sustainability feature`和`From the brand`模块标题均出现在当前商品页；页面未出现登录、验证码或风控阻断。页面中的推荐商品也单独出现`Amazon's Choice`/sustainability文案，不能拿来作为当前商品证据，这与当前模块范围门禁一致。
- `B0BNDLPW1L`和`B0G5Y2TJQM` 当前 Amazon.com 直接返回`Page Not Found`；按照 v10 页面门禁，这两条应整页七项`unknown`、结果表N:T显示`-`，不得从历史HTML、推荐卡或页面残留拼接部分结果。历史HTML中`B0G5Y2TJQM`的主商品锚点仍指向`B0GZZH77J1`，离线规则已验证同样会整页阻断。
- 本次只读核验没有读取或写入周报、Feedback或其他后端数据，也没有把实时页面结果写入固定结果表。当前前端分支的离线规则、身份门禁、七项状态和N:V本地映射已经完成；紫鸟/ZClaw真实店铺浏览器、后端实时US/CA采集、周报副本创建以及A:V云端写入/回读属于后端/发布分支，不作为本分支前端完成条件。

## 2026-09-09 前端与后端分支边界确认

- 前端分支只验收`app/frontend_checks.py`及其调用链：同一份商品HTML/DOM快照内完成当前ASIN身份确认、七项字段提取、证据定位、状态判定、同一快照时间戳和N:T勾叉映射；本地测试与历史HTML回放不打开紫鸟店铺、不读取Seller Central、不写入飞书。
- 紫鸟/ZClaw店铺浏览器、实时页面采集、周报副本、A:V云端写入/回读、Feedback和生产调度均由另一个后端/发布分支承担。后端接入时只能消费前端bundle中的`pass`/`fail`/`unknown`和证据字段，不能放宽当前ASIN身份门禁，也不能将`unknown`改写为`fail`或`pass`。

## 2026-09-10 前端隔离在线表测试（已废止为实时验收依据）

- 使用开发副本的历史源快照`outputs/snapshots/20260908_155743/source.json`和离线HTML根目录进行前端规则验证；没有打开真实Amazon商品链接。该运行只能作为离线选择器回归，不能证明实时页面匹配或完整运行完成。
- 新建独立测试 Spreadsheet：[TEST_前端规则验证_20260909_233023_frontend](https://wit0jhu6kvu.feishu.cn/sheets/Q7X9scETJhkrm2t6xiVcpXxdnBg)。测试表包含`TEST_SUMMARY`、18个业务子表和创建时自带的空白`Sheet1`；固定结果表、Feedback表和生产Token均未写入。
- 先测试`PD03`：源行111、写入111、HTML匹配111、缺失0；七项检查累计`pass=499`、`fail=208`、`unknown/not_applicable=70`；在线写入和`A3:V113`写后回读通过，总耗时233.672秒。
- 再测试全部18个业务子表：源行690、写入690、HTML匹配532、缺失158；七项检查累计`pass=2040`、`fail=1187`、`unknown/not_applicable=1603`；18个子表逐表写入和回读均通过，总耗时1226.625秒。以上统计仅用于离线规则回放和表格写入器回归，不能解释为实时页面结果；必须以下方实时商品页验证替代。

## 2026-09-10 前端实时商品链接隔离验收（已完成执行，待生产门禁）

- 用户已明确纠正验收口径：历史`D:\projects\amazon_daily\_daily\htmls`只用于学习和回归，实际判断必须逐个读取每个ASIN的真实Amazon商品链接。
- 已修正`tools/frontend_online_test.py`：现在调用原入口`app/run.py --weekly-run --dry-run --force-fetch`（PD03最小样例等同原启动中心的`--sheets PD03 --limit 1`），只消费原流程生成的live bundle；原流程内部负责真实页面导航、身份门禁、价格链路和七项前端检查，离线HTML不参与运行时判定。测试结果再写入新建隔离Spreadsheet并逐范围回读，绝不调用生产发布器。
- 新工具输出标记为`source_mode=original_weekly_run_live_bundle`、`offline_html_role=fixture_only_not_runtime_input`，并保留原入口命令、bundle、日志和耗时；待PD03实时小批安全通过后再运行全部18个子表。
- 最终只读复核确认测试工作簿有20个子表、`TEST_SUMMARY`回读20行，`PD03`回读111行且每行22列A:V。V列URL按飞书富文本对象回读，测试工具已兼容`cell.link/text`并重新验证通过。
- 本地证据：`outputs/frontend_online_tests/20260909_233023_frontend/{single_report.json,all_report.json,manifest.json}`。本次只验证开发副本和独立测试表，尚未合并生产代码或修改生产结果表。

### 2026-09-10 原入口实时全量结果

- 单行门禁先按原入口`app/run.py --weekly-run --sheets PD03 --limit 1 --dry-run --force-fetch`完成：run `20260910_083340`，真实页面`ok`，1行七项`6 pass / 1 fail / 0 unknown`；隔离表：[TEST_前端真实页面验证_20260910_083340](https://wit0jhu6kvu.feishu.cn/sheets/OmQssX2XrhowNDt5fI7cH0Hjn1e)。
- 全量严格按原入口`app/run.py --weekly-run --dry-run --force-fetch`完成：run `20260910_083515`，18个业务子表、719行（690条可抓源行+29条`source_data_invalid`保留行），原始实时抓取耗时`4230.359s`；隔离表写入和回读额外耗时后总计`4288.5s`。
- 原始页面状态：`ok=483`、`identity_mismatch=166`、`crawl_error=36`、`source_data_invalid=29`、`parse_error=5`；七项前端状态累计`pass=1879`、`fail=1499`、`unknown/not_applicable=1655`。`identity_mismatch`、`crawl_error`、`parse_error`和`source_data_invalid`均保留为页面/源数据状态，不被伪装成普通字段`fail`。
- 全量隔离表：[TEST_前端真实页面验证_20260910_083515](https://wit0jhu6kvu.feishu.cn/sheets/GA6PsnlcjhTGsqtBocdcVct7n2e)。表内为默认空白页、`TEST_SUMMARY`和18个业务子表；`TEST_SUMMARY`为18行×14列，PD03为111条数据行×22列A:V，云端回读通过。生产固定结果表、Feedback表和紫鸟/ZClaw后端均未写入。
- 本地证据：`outputs/daily_runs/2026-09-10/20260910_083515_weekly_bundle.json`、`outputs/daily_runs/2026-09-10/20260910_083515_weekly_summary.json`、`outputs/frontend_online_tests/20260910_083515/{manifest.json,all_original_report.json,original_runs/20260910_083515.log}`。由于全量仍有技术异常和身份错配，本结果是实时规则/链路验收证据，不是生产合并通过证据。
- 尺寸规则修正回读：发现该 bundle 的尺寸 `expected` 仍是未求值的`BI源数据`公式文本；同时补齐`8'X10'`与`8 x 10 ft`的共享单位、`2.5'X8'`与`2'6\" x 8'`的英尺换算。依据同一批真实页面已保存的 `observed` 尺寸，仅重算隔离表18个业务子表P列并逐表回读，719个商品行最终为`✅=481`、`❌=0`、`-=238`；并同步回写`TEST_SUMMARY!G3:I20`，七项总计更新为`pass=2261`、`fail=1117`、`unknown=1655`。未修改价格、SKU、C列尺寸或其他风控列。该回写是尺寸规则的定向修复验证，不替代后续按 v11 重新抓取的完整实时验收。

## 2026-09-09 Feedback固定结果子表注册与表头回读（最新）

- 已在固定结果 Spreadsheet `Epads8MQkhkuBctjl3lcqLUvnCg` 中按精确标题创建唯一子表 `Feedback差评汇总`，回查得到 Sheet ID `41u25y`；写入后按 `A1:I1` 回读，9列表头完全匹配：`店铺、日期、评级、订单编号、评论、订单商品编号、ASIN、SKU、获取时间戳`。
- 本次只建立目标结构和表头，业务反馈行写入数为0；`config/config.json` 已登记 Spreadsheet/Sheet ID，但 `feedback.enabled` 仍为`false`，没有安装独立计划任务。凭证继续从本机安全凭证文件读取，没有写入仓库或日志。
- 该表结构回读只证明目标身份和表头可用，不等同于Feedback业务验收；两店首次7日多页抓取、二级详情批量回读、两店上下分组写入/整表回读、近10日清理、后续3日状态推进和工作日07:30调度仍保持未完成边界。

## 2026-09-09 Feedback详情与分页选择器复核（最新）

- 两个店铺均已分别完成一次单条低星详情只读复核：冬豚`26782671389969`和北蓉`26686718929338`都成功从反馈订单链接进入订单详情路由；详情标记、订单路由身份、订单商品编号、ASIN、SKU均存在，未出现登录、验证码或风控信号，两个店铺都在操作后关闭。
- 详情页稳定规则已确认并登记为：`text:订单内容`、`data-test-id:order-id-label`、`label:订单商品编号`、`label:ASIN`、`label:SKU`。标签值会去除展示分隔冒号；订单号标签回读为空时，以详情路由中的订单号作为身份回退，并继续与反馈行点击目标比较。
- 真实分页控件探针确认当前页面有唯一可见`kat-link`，显示文本为`下一个 >`，其内部真实`<a>`位于Shadow DOM；已将匹配从精确文字扩展为`下一个`/`下一页`/`Next`前缀，并保留唯一控件和页签变化门禁。若页面有数据但没有可确认的【下一个】控件，采集器现在安全阻断，不再静默把首屏当作全量。
- 本轮仍未写入`Feedback差评汇总`业务数据，没有启用模块，也没有安装独立计划任务；固定Feedback Sheet ID和9列表头已完成创建/回读，正式多页/7日窗口/近10日写后回读仍待下一阶段小批验收。

## 2026-09-09 全局紫鸟 CLI 与两店Feedback页面复核（最新）

- 用户授权后，PATH 中的全局 `C:\Users\Administrator\AppData\Roaming\npm\ziniao-cli.cmd` 已完成只读复核：版本为`1.0.7`，`doctor`通过，Keychain/API认证、ZClaw Bridge和客户端登录用户`2026ZCY`可用；`store list --all --format json`返回冬豚`26782671389969`、北蓉`26686718929338`。没有更新CLI，也没有打印或改写API Key。
- 从两个店铺Seller Central首页发现的可用目标地址是`https://sellercentral.amazon.com/feedback-manager/index.html`；基础路径`/feedback-manager`只返回空SPA壳，不能当作无数据。两个店铺均已单独打开、导航到准确地址、等待渲染并关闭，未出现登录、验证码或风控标志；两店均显示【反馈管理器】、【最新反馈】、`日期/评级/订单编号/评论/操作`表头和20条当前页记录。
- 已通过只读DOM探针确认主表选择器：`.fbm-content.katal h3`（精确文本【最新反馈】）、`.fbm-content.katal kat-table-row:not(.fb-detail-table-header)`、`kat-table-cell.col_order_date`、`kat-table-cell.col_feedback_rating kat-star-rating[value]`、`kat-table-cell.col_order_id kat-link`以及第四个`kat-table-cell`评论单元格。当前页一次探针发现6条评级1–3记录；没有翻页，也没有写入飞书。
- 已受控点击一条低星记录的`kat-link` Shadow DOM内部链接，成功进入`/orders-v3/order/<脱敏订单路由>`详情页；详情页未出现登录/验证码/风控标志。当前已确认`订单商品编号`、`ASIN`、`SKU`标签和订单详情标记，配置使用标签定位与订单路由回退；Feedback采集器仍未启用，等待固定Sheet ID和正式窗口验收。
- `app/seller_feedback_browser.py`已切换到`domcontentloaded`并由代码执行有界等待，加入KAT自定义元素、`value`属性、Shadow DOM链接和CLI店铺上下文身份门禁；`config/config.json`已登记两店非敏感ID、准确Feedback URL和主表选择器，但`feedback.enabled=false`、固定Feedback Sheet ID为空，未启动生产写入或计划任务。
- 本次只读复核没有写入`Feedback差评汇总`，没有安装/修改Windows计划任务；此前全局CLI的Keychain缺失记录属于早先探针，已被本次同一环境的成功复核更新，不需要重建凭证。

## 2026-09-09 全局紫鸟 CLI 只读预检阻断（不适用于项目级 CLI）

- 只读执行 PATH 中的全局 `C:\Users\Administrator\AppData\Roaming\npm\ziniao-cli.cmd doctor` 时，ZClaw Bridge 连通正常、客户端登录用户可见为`2026ZCY`，但该 CLI 读取不到自己的 Keychain `apiKey`（`read apiKey from keychain: keychain: item not found`）；随后 `store list --all --format json` 也因同一认证缺口停止。该结果不能外推为项目级 CLI 失败。
- 本轮没有打开任何店铺、没有访问Seller Central、没有读取或写入`Feedback差评汇总`，没有重建配置、切换profile或尝试登录。代码已将`apiKey`/`keychain`/`credential`错误单独归类为`auth_error`，方便日志和后续复盘。
- 真实Feedback验收不得混用全局 CLI；应固定使用参考项目同一项目级 CLI，并在正常本机权限下重新执行 `doctor`、`store list` 和单店页面只读检查。

## 2026-09-09 参考项目级紫鸟 CLI 只读复核

- 按 `D:\projects\T2_BDLD_weekly_20260827` 的既有实现固定使用 `C:\Users\Administrator\.workbuddy\binaries\node\versions\22.22.2\ziniao-cli.cmd`；该 CLI 的 `doctor` 在正常本机权限下通过，API Key 由本机 Keychain 读取，ZClaw Bridge 正常，客户端登录用户为`2026ZCY`。
- 同一项目级 CLI 的 `store list --all --format json` 只读返回两个店铺：冬豚 `26782671389969`、北蓉 `26686718929338`。本次没有执行 `store open`，没有访问 Seller Central，没有读取或写入`Feedback差评汇总`。
- Feedback 适配层已经把该项目级 CLI 放在全局 CLI 之前；后续真实验收必须沿用这条路径，认证状态以项目级 CLI 为准，不能因为全局 CLI 的 Keychain 缺失而阻断或重建凭证。

## 2026-09-09 Feedback管理器单店只读探针

- 使用项目级 CLI 只打开冬豚 `26782671389969`，先以 `networkidle` 导航到 `https://sellercentral.amazon.com/feedback-manager`；该等待条件未在预期时间内收口，未继续执行页面脚本，随后单独关闭店铺上下文。
- 仅重试一次技术等待条件，改用 `domcontentloaded` 后导航返回成功；等待约8秒后执行脱敏 DOM 诊断：当前 URL正确，但页面标题为空、可见文本长度仅389、【最新反馈】计数为0、【下一个】计数为0，未检测到登录/验证码/风控标志。
- 该探针结论为`blocked/structure_not_confirmed`，不能猜测选择器或把空页面当作无差评数据；本次未点击订单、未翻页、未读取评论/订单字段、未写入飞书，店铺已关闭。下一步需要先确认该会话实际渲染出的Feedback管理器页面结构，再登记选择器并从单店小批开始。

## 2026-09-09 前端图片与品牌故事列拆分

- 用户确认品牌故事定位为页面中的精确标题`From the brand`；结果表不再把商品主图和品牌故事图片合并到同一列。当前开发副本目标布局为A:V：N商品主图、O`From the brand`品牌故事图片、P尺寸、Q BSR、R父子ASIN、S环保、T Amazon's Choice、U时间戳、V Amazon链接。
- O列只有在当前页面通过ASIN身份门禁、A+ `#aplusBrandStory_feature_div`/`data-feature-name=aplusBrandStory`模块存在精确标题`From the brand`且同一模块内有有效图片时才为`✅`；N列只判断当前商品主图。两列分别保留`heading`/`image`证据，缺哪一个会在本地诊断中明确显示。
- 本地规则版本已升级为`2026-09-09-v10`：环保标志要求商品级 ATF 模块存在非空且匹配当前请求 ASIN 的`data-csa-c-asin`，缺失或不一致不得通过；页面至少要有主商品 DOM ASIN 或 URL ASIN 之一作为身份锚点，两个都缺失时七项均为`unknown`；七项检查统一保留同一次 DOM 快照的`captured_at`。离线定向测试覆盖“主图通过但品牌故事图片缺失”“品牌故事图片存在但标题缺失”“环保模块缺少当前 ASIN 绑定”“页面身份锚点缺失”等拆分反例；真实固定结果表A:V迁移/回读仍未执行，生产表不能据此视为已发布。

## 2026-09-09 Feedback核心与页面适配边界

- 开发副本已完成Feedback业务核心：评级1/2/3保留、4/5和缺失评级排除；首次窗口7个自然日、成功回读后切换3日；目标子表严格9列、两店固定顺序上下分组、重复合并和近10日清理；详情缺失不会覆盖已有完整订单商品编号/ASIN/SKU；状态账本只有在两店完成且固定子表写后回读成功时才推进。
- 已增加官方`ziniao-cli`页面适配层：店铺串行开关、反馈管理器URL、页面稳定等待、精确可见【下一个】按钮、页面签名变化、订单详情订单号回读和登录/验证码/风控/身份不一致立即停止；运行耗时、页数、两店状态和脱敏证据由本地Feedback报告记录。真实页面选择器不从历史class猜测，必须通过每个店铺登记的`selectors`配置注入。
- 当前`config/config.json`仍为`feedback.enabled=false`，没有登记真实店铺ID、反馈管理器URL、Secret引用、9列表格Sheet ID或经验证的页面选择器；因此本轮未打开紫鸟、未登录Seller Central、未读取反馈、未写入`Feedback差评汇总`，也未修改生产目录或计划任务。
- 下一步真实验收必须按F10的顺序进行：单店只读小批→第二店只读小批→两店首次7日只读和二级详情→固定子表9列写入/整表回读→后续3日增量/10日清理→工作日07:30计划任务。任一登录、验证码、风控、站点/身份、分页不变或详情订单不一致都要保留阻断证据并停止相关页面操作。

## 2026-09-08 生产目录隔离与时段识别边界

- 已确认生产根目录是`D:\projects\amazon_daily_structured_20260821`；`D:\projects\amazon_daily`只是指向该目录的Windows Junction，不是备份或开发副本。继续在后者编辑仍会直接修改生产代码。
- 已创建独立开发副本`D:\projects\amazon_daily_dev_20260821`，未复制生产Secret、运行产物、历史HTML和虚拟环境，也未安装计划任务。后续代码和文档修改应在该副本完成。
- 当前计划任务脚本根据自身位置解析项目根目录，因此生产任务仍只会运行生产根目录；但当前尚无经过验收的“开发副本→生产白名单发布→SHA-256核对→回滚”工具链，不能把开发副本修改自动视为生产发布。
- 开发副本已增加显式`scheduled_slot`、选择模式和Windows包装器参数，并有周一早间/下午/工作日稳态离线回归；四条计划任务尚未重新安装和实测，因此不能把代码存在等同于调度验收。
- 布局兼容边界：`app/feishu.py`仍保留旧的`sync_base_data`/`write_six_columns`兼容实现（H:M六列）。当前正式生产发布仍受旧入口路由门禁和真实A:V表头验收约束；开发副本的`weekly_result`已切换A:V，但未授权直接写入生产固定结果表。
- 发布边界：生产目录有运行锁和既有未提交改动，不能直接执行`git reset`、`git checkout`或覆盖`.env`/`outputs`。任何上线必须保留代码备份、逐文件校验和生产运行证据；未完成发布工具前，开发副本只允许离线测试和只读检查。

## 2026-09-08 周一换周时点规则（规格已更新，代码尚未实现）

- 用户确认的来源节奏是：周一07:30继续读取上一周已经固化的周报；周一15:30才读取周报登记表中的最新周报并完成本周切换；周二至周五两个时段沿用周一15:30固化的本周周期。
- `docs/SPEC.md`已更新流程图、周报登记、manifest、通知和Windows验收规则，并定义`monday_carryover`、`monday_switch`、`weekday_steady`三种`selection_mode`。`period_id`是周报周期，`run_id`是一次运行，不能混用。
- 当前代码入口仍以登记表最大有效序号作为普通选择结果，尚未证明能够按“周一07:30选择上一周期、周一15:30选择新周期”的时段分流；也尚未验证周一早间复用上一周期manifest、周一下午只在新序号存在时创建副本。
- 在代码实现前，不能把当前Windows两个计划任务称为已满足新换周规则。现有任务隐藏运行、每日07:30/15:30触发和统一锁是已有能力，但不是来源周期切换验收。
- 实现必须保留安全停止：周一15:30没有比上一周期更高的有效序号、登记链接无权限、源副本复制/结构校验失败时，不得把上一周数据伪装成本周；周二至周五发现新周期也不得静默切换，应告警并等待换周窗口或明确人工维护。
- 待验收证据：两个相邻有效登记序号、周一07:30与15:30的实际`source_period_id`和`selection_mode`、两次源快照/manifest身份、固定结果表写后回读、通知正文中的来源说明，以及周二至周五稳态复用记录。

## 2026-09-08 新增前端检查与Feedback范围（本地核心已实现，外部验收未完成）

- 开发副本已增加同页七项前端检查、证据字段和A:V发布/迁移逻辑；当前目标顺序为H:M价格/币种、N:T前端勾叉、U/V时间戳/链接。固定结果表的真实表头、旧A:P/A:O迁移、整行回读和历史bundle兼容尚未在线验收。在完成这些门禁前，生产目录仍按A:O价格基线，不得向云端写入新增N:T。
- 七项检查中只有尺寸需要周报预期字段；N列主图与O列`From the brand`品牌故事图片独立判断；BSR、环保标志和Amazon's Choice按当前商品页面存在性提取，但业务规则要求同一ASIN不能同时有BSR和Amazon's Choice；如果两者同时存在，Q/T均为`fail`并记录“ASIN不合法”。新增主商品`#title_feature_div[data-csa-c-asin]`身份门禁；`B0G5Y2TJQM`历史文件的页面内部ASIN锚点指向`B0GZZH77J1`，其叶子图标实际属于推荐卡`B09S3RCVJ5`，因此该文件正式应整页`unknown`，不能拿其他商品的环保标或BSR。`B0BNDLPW1L`原始HTML的`#acBadge_feature_div`和详情表确实带当前ASIN，且存在可见`mvt-ac-badge-rectangle`与BSR行；按新业务规则，这种同时存在的组合不再输出两个通过，而是两列均失败并保留冲突证据。环保标志进一步收紧为当前ASIN对应的完整`#climatePledgeFriendlyATF_feature_div[data-csa-c-asin]`→`#climatePledgeFriendlyBadge`→`#CPF-ATF-Card`→同卡叶子图标+`.climatePledgeFriendlyProgramName`链，模块 ASIN 缺失或不一致均不计入；空的BTF/A+占位、推荐轮播和泛化文案不再计入；旧父子ASIN规则可能把价格汇总容器当变体区域，已改为读取`#inline-twister-expander-content-*`下的swatch ASIN。历史本地HTML仅用于选择器和离线样本匹配，不直接生成当前结果。实时US/CA复核和真实固定表回读仍未完成。
- 两个店铺Seller Central Feedback管理器的店铺标识、登录/凭证注入方式和固定Feedback子表Sheet ID尚未完成当前环境验收；在凭证、权限、分页和幂等键验证前，不得把商品Review或Q&A当作Feedback来源。
- 前端检查已增加离线替身及bundle/summary/通知统计；Feedback已增加分页、低星、幂等合并、固定Sheet矩阵和脱敏证据替身，但两个店铺Seller Central会话/凭证引用/固定Sheet ID仍未完成当前环境验收。真实最小批之前，生产价格任务仍按A:O流程和HTML关闭边界运行。

## 2026-09-07 seq-4 临时全量补跑结果

- 已真实完成18个业务子表并成功写入固定结果表；调度日志 `2026-09-07_154853_310_2652.log` 以 `END exit=0` 收口，不能再把本轮描述为“未启动”。
- 本轮结果仍是 `partial/degraded`，不是全量无异常通过：`ok=337`、`identity_mismatch=182`、`parse_error=139`、`crawl_error=32`、`source_data_invalid=29`，技术异常率24.8%；353行被门禁阻断并进入恢复清单。
- US/CA 站点结构与币种均已进入同一结果链（USD 549、CAD 170），但 CA 仍有成组身份不一致；身份门禁必须保留，后续应使用带真实 Amazon 单元格链接的源快照做小批映射/链接质量修复，不能直接放宽写入。
- 固定结果表 Token 仍为 `Epads8MQkhkuBctjl3lcqLUvnCg`，本轮没有新建替代结果表；应用协作者8人通知成功、0人失败。HTML价格归档保持关闭，历史HTML保留。
- 证据文件：`outputs/daily_runs/2026-09-07/20260907_154854_weekly_summary.json`、`20260907_154854_delivery.json`、`20260907_154854_notifications.json`、`outputs/weekly_runs/seq-4/weekly_manifest.json`。

## 2026-09-02 全新源快照全量审查

- 已完成全新第5代源快照 `20260902_121541` 的18表719行全量抓取；507条成功、178条请求ASIN被Amazon重定向为其他ASIN、5条解析异常、29条源数据无效。身份不一致已从`crawl_error`独立为`identity_mismatch`：仍逐行阻断、保留证据、重建Tab重试，但不再误报为浏览器/网络技术异常。固定结果表实际写入536行，183行进入阻断/恢复清单，批次明确标记 `partial`，没有把异常当作零价或售罄。
- 已确认本轮只发送给周成业：通知回执 `outputs/daily_runs/2026-09-02/20260902_121541_notifications.json` 仅有1个成功 `open_id`。`--notify-manager-only` 是一次性命令开关，不持久化默认协作者配置；后续Windows/Docker正常入口不带该参数，自动恢复应用协作者范围。
- 已确认价格任务未下载HTML，bundle中没有 `html_path`；价格与HTML仍是独立生命周期。历史HTML不删除。
- 加拿大仍是主要风险边界：本轮计数为CAD 170，但CPD存在成组 `crawl_error`/`identity_mismatch`，不能认为CPD已生产级稳定；需按失败原因和源链接继续做小批恢复，不放宽最终ASIN身份门禁。
- 单点复测已确认这不是残留Tab误读：US `B0DRKD4LQC` 经3次新Tab请求后仍从 `/dp/B0DRKD4LQC` 落到 `B0BY1Z43FP`；CA `B0BNDLKP54` 在邮编验证、CAD证据正常时仍落到 `B0D9NT9JQN`。因此缺少精确源Amazon URL或跨站ASIN映射时，代码不能安全把落地商品价格归给原ASIN；需要在周报提供精确链接或增加经过人工确认的CA映射表。
- Windows计划任务已在目标 Administrator 账户重新安装并回读：`AmazonDaily_0730/1530` 均为隐藏 `Ready`、直接执行 `wscript.exe`→`hidden_ps1.vbs`→`scheduled_run.ps1`，最近返回码为0；下一步仅需观察下一次真实15:30计划批次的运行日志和结果，不再作为安装权限阻断项。

## 2026-09-07 飞书副本创建超时修复

- 15:30 计划批次 `20260907_1530` 在抓取前失败，真实 traceback 为 `HTTPStatusError: 504 Gateway Timeout`，接口为 `drive/v1/files/{source_token}/copy`；没有生成本批抓取结果目录，异常通知已送达周成业。
- 已修复 `FeishuClient.copy_file`：对 408/429/5xx 与传输超时最多退避重试3次；每次重试和最终失败前先按精确名称回查根目录，若网关已完成复制则复用唯一副本，避免重复快照。新增回归覆盖“504后已创建副本”和“传输超时后成功”。
- 同步加固 `bin/scheduled_run.ps1`：即使 Python 将 traceback 写入 stderr 或包装器本身异常，也会保留真实退出码并写入 `END` 行，避免调度日志只留下半截输出而无法判断批次是否完整结束。
- 临时补跑 `20260907_154131` 已启动并安全停止于源快照发现阶段：新快照 `PD05` 的表头为 `ASIN\n(...)`，旧的“必须完全等于 ASIN”检测将其误判为未知 Marketplace；未抓取 Amazon、未写固定结果表，异常通知已送达周成业。
- 已修复 ASIN 表头识别：允许 `ASIN` 后的换行/括号说明，同时拒绝 `ASIN_CODE` 等近似字段；对该临时快照只读复核结果为21张表、18张业务表映射、3张辅助表排除、0张未知、0个重名。

## 2026-09-02 Docker迁移审查

- 已修复Docker骨架的可运行性问题：`.dockerignore`排除 `tests` 时Dockerfile仍 `COPY tests`、系统cron文件被错误传给用户级 `crontab`、cron日志父目录未创建，以及 Compose `init: true` 与镜像内 `tini` 双重包装；当前仅保留镜像内 `tini` 作为 PID 1。
- Docker Desktop Linux 引擎上已完成真实镜像构建、容器内 Chromium/编译、使用镜像依赖的269项回归，以及短启动健康检查：`amazon-daily:latest` 为 Linux/amd64、约334.6MB，入口成功加载18个子表，健康状态`healthy`，随后已停止并清理容器。该短启动未到cron时点，未抓Amazon、未写飞书、未发通知。仍需在迁移服务器按同一出口网络进行 US/CA 只读与真实计划时段验收；迁移说明和回滚边界见 `deploy/docker/README.md`。

## 2026-09-02 导航残留页面问题

- 已修复：`tab.get()` 失败结果和 `doc_loaded` 超时不再被忽略，失败尝试不会继续解析残留 Tab 页面。
- 已补充链接审计：源表合法链接不再在读取时静默丢失，保留为`source_product_url`并优先请求；仍需线上验证标准化链接与源链接在 CPD 上的实际差异。
- 仍需线上验证：当前历史批次中的大量 `identity_mismatch` 已证明与残留页面模式一致，但修复后 CA 的真实成功率、源链接是否能消除错配以及 Amazon 出口/风控影响尚未完成在线对照。
- 已验证边界：2026-09-02 对 US `B0C5R56QTF` 首次出现地址回读瞬态失败，重试后 `90210` 以`visible_exact`验证通过；加入最多3次地址组件重试后再次实测商品页最终 ASIN、USD 证据均通过，不把一次 `Update location` 回读当成商品链接异常。
- 批量证据：2026-09-02 11:58:43 的7个 CPD 子表只读批量仍为130条 `identity_mismatch`（170行中30成功、1解析失败、9源数据无效），与历史数量基本一致；该批次源快照的170条 `source_product_url` 全为空，因此不能据此否定原始链接参数修复，必须先刷新包含真实源单元格链接的快照再复测。
- 安全边界：身份门禁不能放宽；最终页面 ASIN 与请求 ASIN 不一致时仍必须阻断，即使页面标题和价格看起来正常。

> 2026-08-27生产级最终收口审查；修复和验证记录见[TASKS](TASKS.md)，业务规则见[SPEC](SPEC.md)。此处只保留需要真实外部系统证明或无法彻底消除的边界，不把离线成功等同于线上验收。

1. 2026-08-27 07:30真实批次共724行：CA 170行中28成功、116 crawl_error、17 parse_error、9源数据无效；大量失败落到上一商品ASIN，已修复失败重试不重建Tab的问题。修复后的CA成功率、邮编、出口及风控仍需最小在线样本和下一次正式批次验证，不能先删除身份门禁。
2. 2026-08-27批次已验证固定结果表写入542行、阻断182行及8名协作者通知成功；同周A:G和全流程已有真实证据。代码已增加登记首次发现8天门禁，避免永久沿用旧链接；新登记链接出现后的无感换周、结果表ISO周改名及该门禁仍需下一批真实证据。
3. deadline已覆盖应用层受控操作和返回校验，但第三方驱动或操作系统失去响应仍可能拖长墙钟；当前没有进程级强制终止保证。不得把90秒配置称为任意故障下的硬上限。
4. 云端创建成功而响应丢失、或本地manifest落盘失败时，不能安全认领未知空表；需核对云端Sheet ID和日志后恢复，不自动覆盖同名表。
5. 通知按业务内容和个人成功回执去重，tenant token按服务端有效期提前5分钟刷新；但发送已被飞书接受、响应丢失或回执落盘失败时，仍可能重试重复，不承诺跨网络故障的严格仅一次送达。
6. 超过2000行和宽表读取已有分页回归；真实飞书超大表、目标表容量扩展、复杂公式返回形态及API限流仍需实际验收。不会仅凭替身测试宣称所有规模与公式均通过。

离线测试通过不等于“全流程永远无问题”，也不等于现有飞书历史结果已经重新计算。
