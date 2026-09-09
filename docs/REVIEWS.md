# REVIEWS：当前未完成的真实验收与剩余边界

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
- 本地规则版本已升级为`2026-09-09-v9`：环保标志要求商品级 ATF 模块存在非空且匹配当前请求 ASIN 的`data-csa-c-asin`，缺失或不一致不得通过；七项检查统一保留同一次 DOM 快照的`captured_at`。离线定向测试覆盖“主图通过但品牌故事图片缺失”“品牌故事图片存在但标题缺失”“环保模块缺少当前 ASIN 绑定”等拆分反例；真实固定结果表A:V迁移/回读仍未执行，生产表不能据此视为已发布。

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
