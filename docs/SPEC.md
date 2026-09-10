# SPEC：Amazon 周报前端价格捕捉任务

> 当前规格，更新于2026-09-09。只维护本文件这一套业务口径。实现差距必须明确记录在[REVIEWS](REVIEWS.md)，测试与历史证据在[TASKS](TASKS.md)；不得把规格要求当作已经通过真实验收。
>
> 旧版已完整保存到[历史目录](history/README.md)，其中“每周新建结果表”“HTML门禁”“仅下午执行”“8月31日截止”不再是当前规则。

## 1. 目标与边界

每周一至周五北京时间07:30、15:30执行价格任务，每天两次、无截止日期、周末不执行。不依赖GPT界面；依赖Windows开机、交互账号登录和可用网络。换周不是两个时段都立即切换：周一07:30为“上周延续批次”，必须读取上一周已经固化的周报快照；周一15:30为“本周切换批次”，才读取固定登记表中的最新有效周报并建立新的快照。周二至周五两个时段沿用周一15:30已经确认的本周周期，除非人工明确执行换周维护。

固定链接登记表仍是周报来源控制面，但“登记表最新链接”和“本时段应使用的来源周期”不是同一个概念。周一07:30可以只读检查登记表，却不得因为登记表已经出现本周新链接而提前切换；周一15:30必须重新读取并验证最新有效链接，若没有比上一周期更高的有效序号则安全停止并通知周成业，不得静默继续使用上一周。每个正式来源周期建立一个只读完整快照，重新发现业务子表、提取A:G、抓取Amazon实时价格并完成前端检查，同时从两个店铺的Seller Central Feedback管理器读取评级小于等于三星的feedback，最后写入同一个固定结果Spreadsheet及其固定Feedback子表。固定的是字段位置和结果链接，不是A:G的数据。明确恢复已有run_id时才复用原批快照，禁止混用新基础数据和旧价格。

Feedback是独立的工作日早间子任务：周一至周五北京时间07:30运行，周末不运行；价格任务仍按本SPEC既定的07:30和15:30节奏执行。Feedback不能因为价格任务在15:30运行而重复采集，不能另启一套绕过统一运行锁的并发写回任务。

价格任务与HTML独立：正式 `--weekly-run` 和 `--weekly-push-only` 强制关闭HTML捕获、HTML必需门禁和HTML服务依赖；默认配置、实际配置及模板均关闭归档与服务。既有HTML只读留存，不清理、不改名、不提交Git；8765服务和自启动任务停用。代码与手工脚本保留，日后如要恢复必须作为独立功能重新验收。禁止使用旧HTML替代实时价格。

### 1.1 端到端流程图

凡涉及数据来源、换周、列布局、抓取、交付或通知的变更，必须同步更新此图和对应章节。

```mermaid
flowchart TD
    A[工作日07:30和15:30或人工启动] --> A1{取得统一进程运行锁?}
    A1 -- 否 --> A2[退出码75；不抓取、不写飞书]
    A1 -- 是 --> B[读取固定周报链接登记表]
    B --> B1s{当前是否周一07:30?}
    B1s -- 是 --> C0[选择上一周期已固化的manifest/快照；只读检查登记表，不切换到最新链接]
    B1s -- 否 --> C[选择链接非空且序号最大的唯一记录]
    C --> C1{当前是否周一15:30?}
    C1 -- 是 --> C2{存在比上一周期更高且有效的新序号?}
    C2 -- 否 --> X0[保存“本周链接未就绪”并通知周成业；安全退出]
    C2 -- 是 --> C3[锁定本周最新链接并创建新快照]
    C1 -- 否 --> C4[沿用周一15:30已确认的本周manifest/快照]
    C0 --> C5[校验上一周期快照仍可读]
    C3 --> C5
    C4 --> C5
    C5 --> C6{来源周期与时段规则一致?}
    C6 -- 否 --> X[保存错误并通知周成业；安全退出]
    C6 -- 是 --> C7{登记首次发现未超过8天且URL未被同序号替换?}
    C7 -- 否 --> X[保存错误并通知周成业；安全退出]
    C7 -- 是 --> D{登记与资源校验通过?}
    D -- 否 --> X[保存错误并通知周成业；安全退出]
    D -- 是 --> E{明确恢复当前run_id?}
    E -- 是 --> F[验证批次身份、缓存版本和有效期；复用本批快照和映射]
    E -- 否 --> G{本时段需要建立新来源快照?}
    G -- 是 --> G1[复制已选来源周报；新快照添加周成业管理权限]
    G -- 否 --> G2[复用已确认周期快照；不跨周期刷新输入]
    G1 --> H
    G2 --> H[枚举全部业务子表；通用销售/辅助ASIN表按表头排除并留证]
    H --> I[按容量分段提取A:G；登记源指纹和latest_run身份]
    F --> J[按子表Marketplace抓取；一个浏览器最多四个商品Tab]
    I --> J
    J --> K[PD等使用amazon.com和USD；CPD使用amazon.ca和CAD]
    K --> L[原子读取URL和ASIN及邮编和页面DOM；主价、促销和前端检查共用页面证据]
    L --> L1[检查主图/品牌故事图、尺寸、BSR、父子ASIN、环保标志和AC标志]
    L --> L2[每个商品结束后等待1至3秒；不依赖HTML]
    L1 --> L2
    L2 --> M[逐表保存bundle、缓存和日志；不等待HTML]
    M --> N[验证最新批次身份、源指纹及ASIN集合；旧批次禁止发布]
    N --> O[备份固定表；按ASIN组合A:G、H:M、N:T及U:V并发布完整行]
    O --> P[清理旧尾行；未知布局停止；登记本批新建表以支持空表恢复]
    O --> Q[写后回读核对；记录成功与阻断]
    P --> Q
    M --> FB0{当前为Feedback工作日07:30槽位?}
    FB0 -- 否 --> R
    FB0 -- 是 --> FB[串行进入店铺A的Seller Central反馈管理器]
    FB --> FB1[在最新反馈区域按日期窗口读取；仅保留评级<=3]
    FB1 --> FB2[点击每条合格反馈的订单编号进入二级详情，读取订单商品编号、ASIN、SKU]
    FB2 --> FB3[确认当前页下一个按钮及页面身份后继续店铺A分页]
    FB3 --> FB4[关闭店铺A上下文，串行进入店铺B并重复同一流程]
    FB4 --> FB5[合并两店结果、按反馈日期只保留近10天、写入同一Feedback子表]
    FB5 --> Q2[回读Feedback子表并记录独立状态与耗时]
    Q --> R[尝试同步表名；固定Token和URL保持不变]
    Q2 --> R
    R --> S[全量一次通知所有应用协作者；本地路径仅周成业可见]
    S --> T[逐人原子保存回执；同结果不重复群发；问题另通知周成业]
```

跨子表不是云端原子事务；每段回读后计入成功，局部写入失败保留已成功范围并继续其他子表，不承诺瞬时全表切换。真实运行待验收项见REVIEWS。

### 1.2 当前开发分支的前端范围

本开发分支只负责商品页面前端检查：从当次打开的商品`product_url`实时DOM快照中确认当前商品身份，并提取主图、`From the brand`品牌故事图片、尺寸、BSR、父子ASIN、环保标志和Amazon's Choice七项结果，最后映射到N:T的`✅`/`❌`/`-`。历史离线HTML只用于选择器、身份门禁和反例回归，不得作为本次运行输入，也不得冒充线上价格或外部采集结果。隔离验证工具必须调用与原生产流程一致的`app/run.py --weekly-run`（内部复用`AmazonBrowser`/`run_fetch`）真实导航链路，并把实际请求链接、最终页面URL和页面状态留在本地证据中；不得另起一条独立浏览器/CDP探测路径。

紫鸟/ZClaw真实店铺浏览器、Seller Central/Feedback、周报副本创建、生产固定结果表A:V写入与生产云端回读属于后端/发布分支，不是本分支的前端实现前置条件。前端分支允许在隔离测试表中使用普通Amazon实时商品页验证`product_url`和规则，但不得写生产表；后端分支接入时必须复用本分支定义的身份门禁、字段证据和`unknown`语义。前端单元测试、离线HTML回放和本地bundle验证不能替代实时商品页验证。

## 2. 运行环境与依赖

Windows、Python 3.10+、Chromium/DrissionPage。Python依赖以 `config/requirements.txt` 为准。HTML封装依赖仅用于独立HTML功能，不是正式价格运行前提。

飞书应用需要读取登记表、Wiki和周报，复制原表、管理新快照协作者、读取及编辑固定结果表和必要时添加业务子表，以及查询应用协作者并发送消息。应用可用范围与文档访问权限是两件事，不自动扩大任一范围。

### 2.1 飞书资源统一授权规则（当前有效）

- 本项目当前应用创建、复制或复用为交付目标的飞书云端资源，统一授予配置中的周成业 `openid/full_access`；在飞书界面对应“可管理/管理权限”。资源类型不限于 Spreadsheet，未来通过同一客户端创建的 Drive 文件或文件夹也必须在拿到 token 后调用同一授权入口。
- 自动策略由 `feishu_auto_grant_generated_resources=true`、`feishu_manager_open_id`、`feishu_generated_resource_member_type=openid` 和 `feishu_generated_resource_perm=full_access` 共同控制。`open_id` 必须是当前应用维度，不能从其他应用或用户态 CLI 直接复用；当前配置已按本应用通讯录核验周成业身份。
- `create_spreadsheet()`、通用 `copy_file()` 以及复用固定结果表的周资源初始化在拿到或确认资源 token 后都必须自动授权；`ensure_permission_member()` 会先读已有权限，必要时写入，再重新读取成员列表确认目标成员、成员类型和 `full_access` 均存在。只返回“写接口成功”但回读不到权限时，流程失败关闭，不把资源标记为 ready。
- 子表、单元格、图片等内容继承其所属云端资源的文件级权限；添加 Sheet 或写入内容不另建第二套人员权限名单。未来新增资源创建/上传入口必须复用 `ensure_generated_resource_access()`，否则不得接入正式交付。
- 该规则不自动扫描或批量修改任意历史资源；但凡资源被登记为当前应用的交付目标，初始化或写入前必须显式补授权并逐项回读。历史资源的补授权仍需给出明确资源范围和审计证据，不能由新资源规则推断已完成。

新增的前端检查沿用商品详情页的同一浏览器会话和页面证据，不为每一项检查重复打开商品页。新增的后台Feedback采集使用两个店铺各自的Seller Central会话/凭证，和零售商品页会话分开；凭证只从运行时Secret注入，不写入快照、日志、bundle或Git。两个店铺必须串行处理，一个店铺的浏览器上下文、页面状态、分页游标和订单详情不能带入另一个店铺。单店铺失败只将Feedback子任务标记为partial/blocked，并在安全关闭当前店铺上下文后继续另一店铺；不回写价格行，也不把后台失败计入价格技术异常率。实现参考BDLD项目的慢速串行、随机等待和风险立即停机原则，但不复制其店铺身份、凭证或业务字段。

## 3. 配置与来源

- 非敏感配置：`app/config.py` 默认值 → `config/config.json`；模板为 `config/config.example.json`。
- 敏感配置：根目录 `.env`，兼容本机 `.env/飞书凭证.txt`；同名系统环境变量优先。
- `.env.example` 仅列出 `FS_APP_SECRET`，不是第二套业务配置。JSON不得包含真实Secret或 `feishu_app_secret` 配置项。
- 兼容凭证文件为两个非空行，App ID必须与JSON一致；不要同时维护多份本地Secret。
- 正式任务的子表列表及Marketplace来自本批最新快照发现结果；旧静态sheets/sheet_profiles不是全量范围上限。
- 前端检查中只有尺寸一致性需要使用当批周报的尺寸预期；尺寸比较必须把`8'X10'`与`8 x 10 ft`、`2.5'X8'`与`2'6\" x 8'`视为同一尺寸，并允许页面附带`(Rectangular)`等非尺寸描述；父ASIN发散按页面子体关系判断，其余图片、品牌故事、BSR、环保和Amazon's Choice指标均只判断当前页面是否存在，不与周报字段匹配。
- 历史`htmls/`和诊断HTML只用于前端检查的离线样本、选择器和证据定位匹配；不得把历史HTML当作当前运行页面，也不得用历史HTML直接生成本批N:T结果。
- Feedback任务需要两个店铺的非敏感标识、各自Seller Central反馈管理器URL、凭证引用、固定目标子表身份和页面节奏配置；反馈管理器URL必须明确指向后台【反馈管理器】页面，不得改用商品Review、Q&A或前台评论页面。凭证引用只保存在本机Secret配置，店铺标识、脱敏来源URL和目标Sheet ID写入manifest用于审计。
- Feedback窗口与留存配置固定为：首次成功运行回看最近7个自然日；后续成功运行回看当前运行时间往前3个自然日；结果表按反馈日期仅保留最近10个自然日。窗口日期统一使用`Asia/Shanghai`，首次窗口只有在两个店铺都完成到达窗口边界或明确记录了安全终止原因后才推进为后续3日窗口；部分失败不得把未完成的首次7日窗口伪装成增量窗口。
- Feedback调度固定为周一至周五07:30（`Asia/Shanghai`），建议复用现有07:30工作进程和统一运行锁；不得创建与价格07:30任务互相并发写同一子表的第二套无锁任务。手工运行必须显式标记为`manual`，不得伪装成定时槽位。
- `outputs/weekly_runs/fixed_result.json` 是固定云端资源身份登记，不是重复的配置来源；部署迁移必须保留。
- `weekly_registry_max_age_days` 默认8天：正式新批次按本机持久化的首次发现时间阻止长期沿用旧登记链接；只读检查和明确恢复既有run_id不改此账本。

新增配置时同步代码默认值、模板、SPEC及测试；Secret还需同步环境变量模板和脱敏测试。禁止在日志、manifest或Git中保存Secret、tenant access token、Cookie和Authorization。

## 4. 目录结构与文档职责

```text
amazon_daily_structured_20260821/
├── README.md                       最短入口与文档导航
├── 启动中心.bat                    旧人工菜单，当前限制见第18节
├── .env.example                    Secret变量模板
├── app/
│   ├── main.py                     CLI编排、抓取、交付、通知
│   ├── config.py                   配置加载与验证
│   ├── models.py / pricing.py      结果模型与Decimal计算
│   ├── feishu.py                   飞书API、源行解析与本地备份
│   ├── weekly_registry.py          登记表选择与URL校验
│   ├── registry_freshness.py       登记链接首次发现账本与过期门禁
│   ├── weekly_assets.py            周资源、manifest、固定表身份和锁
│   ├── weekly_mapping.py           全部子表发现与映射
│   ├── product_links.py            ASIN、域名与Marketplace
│   ├── weekly_execution.py         价格专用配置与每批快照准备
│   ├── weekly_result.py            基础行发布、列迁移与写后核对
│   ├── frontend_checks.py          商品详情页图片、尺寸、BSR及标志检查
│   ├── seller_feedback.py          两店铺Seller Central低星feedback窗口、去重、合并与固定子表发布
│   ├── seller_feedback_browser.py  官方ziniao-cli页面适配、慢速分页和详情风控门禁
│   ├── result_notification.py      通知模板、收件人过滤与发送
│   ├── cache.py / exporters.py     快照缓存、CSV
│   ├── runtime_state.py            统一进程锁、独立临时文件与持久化原子JSON
│   ├── publication_guard.py        最新批次登记及发布/改名防过期门禁
│   ├── sheet_io.py                 按表容量分段读取，保留绝对行号
│   ├── diagnostics.py              异常截图与JSON证据，不新增页面HTML
│   ├── amazon/                     浏览器Tab、页面解析和选择器；price_evidence.py限定主价DOM与币种证据
│   └── html_*.py / archive_*.py / offline_verify.py / mhtml_compare.py
│                                    独立HTML捕获、留存、服务和验证
├── config/                         本机JSON、模板及requirements.txt
├── bin/                            安装、人工运行、Windows调度和HTML服务脚本
├── docs/
│   ├── SPEC.md                     唯一当前规格，含流程、操作与验收
│   ├── TASKS.md                    实施任务、验证耗时及历史证据
│   ├── REVIEWS.md                  当前未解决评审意见
│   ├── README.md                   文档阅读导航
│   ├── 操作手册.md                 旧路径跳转，不复制操作规则
│   ├── 当前业务规则.md             旧路径跳转，不复制业务规则
│   ├── 交付清单.md                 旧路径跳转，不复制验收规则
│   └── history/                    已被替代的文档，仅供追溯
├── tests/                          离线单元与流程测试
├── sandbox/ / tools/               探针与辅助工具，非正式入口
├── htmls/                          可选HTML文件，默认归档根
├── outputs/                        运行证据及必须备份的资源状态
├── data/                           本地数据
└── tmp/                            临时文件
```

不为目录形式将app迁移为src。运行产物不写入源码、配置或docs目录。不把outputs整体视为可删除缓存：fixed_result.json、weekly manifests、交付记录和备份必须保留。

文档同步：需求/流程/字段先改SPEC，实施和耗时写TASKS；未解决问题写REVIEWS，解决后移入TASKS并清除待审项。目录或入口变化同步两个README及旧路径导航。历史记录明确标注已替代，不重新当作操作指令。用户提供的工程规范手册为参考，不复制成另一份项目SPEC。

## 5. 字段与结果布局

结果表表头第2行，商品数据从第3行开始。当前商品结果布局为A:V，共22列；不存在HTML业务列。A:G是本批周报基础字段，H:M是价格结果（币种位于M），N:T是新增前端检查结果，U/V固定为时间戳和Amazon链接。检查列不改变价格列的顺序，也不把检查失败当作价格成功。

本次将“前端查找表/推送表”解释为固定结果Spreadsheet中的商品结果子表；不另建一份平行结果Spreadsheet。若后续指定独立Spreadsheet，必须先登记其Token、Sheet ID、表头和权限，再单独修订本SPEC和发布门禁。

| 列 | 字段 | 来源或含义 |
|---|---|---|
| A:G | ASIN、SKU、尺寸、正常售价、本周折扣形式、本周折扣%、目标成交价 | 每次正式运行从本时段选定的`source_period_id`固化周报副本重新提取；固定列位，不固定内容。周一07:30使用上一周期，周一15:30及之后使用本周周期 |
| H | 展示价格 | 当前商品主购买区价格 |
| I | 折扣类型 | 页面证据决定的四类优惠之一 |
| J | 折扣值 | 百分比或金额，按第6节类型解释 |
| K | 最终价格 | 根据展示价格及有效优惠计算 |
| L | 一致性检查 | 最终价格与目标成交价的比较 |
| M | 币种 | US为USD、CA为CAD |
| N | 商品主图是否存在 | 当前商品主图区域存在有效图片来源时写`✅`；明确缺失写`❌`。只判断主图是否存在，不把品牌故事图片混入本列 |
| O | From the brand品牌故事图片是否存在 | 当前商品的A+ `#aplusBrandStory_feature_div`/`data-feature-name=aplusBrandStory`模块必须同时出现精确标题`From the brand`和该模块内有效图片时写`✅`；模块为空、标题缺失或图片缺失写`❌`。本列与N列独立，便于看出是哪一项缺失 |
| P | 前端尺寸是否一致 | 页面当前选中商品尺寸与当批周报预期尺寸规范化后相同时写`✅`，不一致写`❌` |
| Q | BSR标志是否存在 | 当前ASIN所属的`#prodDetails`/`#productDetails_feature_div`详情表存在`Best Sellers Rank`字段、且当前ASIN没有同时出现AC时写`✅`；当前ASIN详情表明确没有，或BSR与AC同时出现导致ASIN不合法时写`❌`；允许读取当前商品折叠详情表，但不读取导航、推荐或其他ASIN区域 |
| R | 父子ASIN发散检查 | 页面正常且明确列出至少一个子体/变体ASIN时写`✅`；页面正常但没有任何子体/变体时写`❌` |
| S | 环保标志是否存在 | 当前商品存在完整商品级 Climate Pledge Friendly 标志链时写`✅`；仅有空占位、品牌可持续文案或不完整容器时写`❌` |
| T | Amazon's Choice标志是否存在 | 当前ASIN对应的`#acBadge_feature_div`内存在可见实际badge节点、节点文本精确为Amazon's Choice且当前ASIN没有同时出现BSR时写`✅`；隐藏说明弹窗、空占位、ASIN不一致、不存在或与BSR同时出现导致ASIN不合法时写`❌` |
| U | 时间戳 | 本条抓取/计算时间；不是表名更新时间 |
| V | Amazon链接 | 本商品标准URL |

检查列的内部状态仍统一为`pass`、`fail`、`unknown`、`not_applicable`，但结果表只显示`✅`、`❌`或`-`：`pass`显示`✅`，`fail`显示`❌`，`unknown`和`not_applicable`显示`-`。这样表格便于业务查看，同时不把“证据不足/不适用”伪装成失败；bundle和本地诊断保留原始状态、观察值、原因和定位。N列主图与O列`From the brand`品牌故事图片独立输出。BSR与Amazon's Choice先分别提取，但业务规则要求同一当前ASIN不能同时存在两者；若两者同时存在，判定该ASIN不合法，Q和T均为`fail`并在原因中保留该冲突。Page Not Found、身份不一致、导航失败、币种错误等整页门禁发生时，N:T全部显示`-`并在bundle中保留`unknown`，价格列仍按第17节阻断规则处理。检查证据写入bundle和本地诊断，至少包含检查名、观察值、状态、原因、页面ASIN/URL、抓取时间和规则版本。

旧系统A:P中M为时间戳、N为HTML、O为币种、P为Amazon链接；旧A:O中M为时间戳、N为币种、O为Amazon链接。识别完全匹配的旧表头或当前A:V表头后才允许备份发布。迁移到新布局时，旧币种映射到当前M，旧时间戳映射到当前U，旧Amazon链接映射到当前V，旧HTML只备份并清理，N:T初始化为空，不得把旧HTML值当作新检查结果。新布局写入完整A:V，物理尾列不因迁移删除；未知表头不得覆盖。每批同样重新组合A:G，不能沿用旧目标价、旧SKU或旧检查结果。

ReportRow记录源行、ASIN、基础字段、目标价来源和前端检查预期；CrawlResult记录页面状态、价格证据、前端检查结果、run_id、站点、币种、页面URL和耗时。完整诊断保存在本地，不要求全部上表。

### 5.1 后台Feedback合并子表

固定结果Spreadsheet增加一个独立的Feedback子表，固定名称为`Feedback差评汇总`；首次建立或识别时必须把Sheet ID写入固定资源登记和本批manifest，后续只复用该Sheet ID，不按同名猜测或每次新建。该子表不参与A:V商品行的行数、价格技术异常率或ASIN集合门禁。

`Feedback差评汇总`的可见表头固定为以下9列，列顺序不可变：

| 列 | 字段 | 来源 |
|---|---|---|
| A | 店铺 | 当前Seller Central后台会话对应的配置店铺，不从页面自由猜测 |
| B | 日期 | 【反馈管理器】→【最新反馈】中的反馈日期；按`Asia/Shanghai`解释窗口，不擅自改写源日期 |
| C | 评级 | 【最新反馈】中的星级，仅保留1、2、3星；缺失或无法解析的评级不默认当作合格 |
| D | 订单编号 | 【最新反馈】中的订单编号，并在二级详情前后校验身份 |
| E | 评论 | 【最新反馈】中的评论原文，不改写摘要、不拼接本地内容 |
| F | 订单商品编号 | 点击对应订单编号进入二级界面后读取 |
| G | ASIN | 同一订单二级界面读取 |
| H | SKU | 同一订单二级界面读取 |
| I | 获取时间戳 | 本次实际读取该条反馈/详情的时间，使用带时区的ISO时间 |

两个店铺共用同一个子表，按固定店铺顺序上下连续分组；每组按反馈日期倒序、再按稳定的内部幂等键排序。只保留一个表头，不插入第二个表头或合并单元格；店铺列是机器读取和人工分组的唯一边界。内部幂等键不新增为可见列，优先使用`店铺 + Seller Central稳定feedback ID`；若页面没有稳定ID，使用`店铺 + 日期 + 评级 + 订单编号 + 评论内容哈希`并将降级原因写入本地审计。重复读取更新同一逻辑记录，不重复追加。

来源路径必须是每个店铺配置的Amazon Seller Central【反馈管理器】页面；目标区域是页面中的【最新反馈】。按页面显示顺序读取当前日期窗口，点击可见且身份明确的【下一个】按钮翻页；每次点击后必须确认页面内容或分页状态发生变化，按钮禁用/消失且已覆盖窗口边界时才停止。不得盲点固定次数，不得把旧DOM当作新页，也不得在页面没有变化时连续点击。

首次运行在本地没有`feedback_state.json`的有效成功检查点时，读取运行时间往前7个自然日；后续只有在上一次窗口状态为可推进时，读取运行时间往前3个自然日。窗口内每条评级小于等于3的反馈都必须尝试点击订单编号进入二级界面，读取订单商品编号、ASIN和SKU，并校验二级页面仍属于该订单。二级页面读取失败时不得猜测或填充其他订单的字段；为避免丢失反馈，主反馈字段可先写入、三级详情字段留空，并在本地记录`partial`及可重试原因，下一次3日重叠窗口优先重试，未完成的详情不计入该条完整成功。

每次发布前按反馈日期删除结果表中早于`运行时间 - 10个自然日`的记录，先备份目标子表、再写入合并后的两店结果、最后按9列整表回读。日期缺失或无法解释的记录不能静默归入10日窗口，保存到本地异常证据并标记阻断。源端当前页暂时没有返回的历史反馈不自动删除，只有本地10日留存门禁或明确的业务保留期限才允许清理。

Feedback任务独立记录`ok`、`partial`、`blocked`和`auth_error`。一个店铺遇到登录失效、验证码、风控、权限、页面结构异常、翻页无变化或二级订单身份不一致时，立即停止该店铺的后续操作、关闭其上下文并保存页码/URL/原因证据，再按独立会话继续另一店铺；不进行连续重试轰炸。两个店铺均成功但窗口内没有评级小于等于3的记录时，写入零条新增结果并记录“无符合条件数据”，不能把空结果当作接口失败。目标表不可见的内部状态、幂等键、页码、来源URL、失败原因、`run_id`和规则版本全部保存在`outputs/feedback/{run_id}/`，不扩展用户要求的9列表头。

## 6. 价格与折扣规则

只按当前商品购买区的真实页面证据分类：coupon > code > 价格折扣 > 原价调整。周报预期类型仅用于诊断，不替代页面事实。

| 类型 | 最终价格 | 折扣值 |
|---|---|---|
| 原价调整 | 展示价格 | 目标成交价减正常售价，金额 |
| code | 展示价格 × (1-code百分比) | code百分比 |
| 价格折扣 | 展示价格，不重复打折 | 主价区Save百分比 |
| coupon | 优先明确券后价；否则展示价减Saving金额；否则展示价 × (1-coupon百分比) | 对应页面优惠百分比或金额 |

计算使用Decimal、ROUND_HALF_UP，金额两位小数。同币种 `abs(最终价-目标价) <= price_tolerance` 为一致，当前容差0.50；US单位USD、CA单位CAD，不做汇率换算。

目标成交价优先使用源表已有数值；来源标记为feishu_value、excel_cached_value、local_fallback或missing。本地兜底按源字段计算：本周类型为原价调整/原价定档/原价且折扣值>1时取该绝对价格；值空或0取正常售价；0<值<1取正常售价×(1-值)；其他取正常售价。缺少必要源值标记source_data_invalid，不假造价格。

证据必须锚定当前商品主价、Buy Box或促销控件：

- Coupon来自aria-label、couponText、couponLabelText、ct-coupon-tile等控件；Saving金额与Coupon在同一控件。
- Code来自购买区alert/promotion中的Save X% at checkout等文案。
- Save%来自主价容器或priceToPay邻近savings元素。
- 评论、问答、推荐商品、脚本模板中的促销词不得参与计算。
- 主价按DOM容器边界提取，排除隐藏、推荐、脚本、划线价格及USED/REFURBISHED二手购买区节点；删除全页面首个价格兜底。主价冲突为parse_error；没有可靠主价且没有当前商品availability控件的明确售罄文案，也为parse_error，不能猜测售罄。
- 页面真实Coupon与Code同时存在时仍选Coupon。规则变化需要递增parser_rule_version并重抓或重新解析，不能继续信任旧计算缓存。

Coupon、Code、Save与主价共用DOM树及隐藏/脚本/推荐/评论/二手区排除规则。Coupon金额只能来自同一控件；多个控件优惠不一致、同一控件有多个不同有效比例/金额时标parse_error，不任选第一项。实际浏览器采样会在离线DOM副本标注CSS不可见控件，不修改页面本身。重复且一致的控件证据可去重。

比例须大于0且小于100%；优惠金额不得为负或超过展示价格，Coupon最终价须大于0且不超过展示价格；NaN/Infinity拒绝，零价格容差按严格零执行。源正常售价/目标价为负或非有限数时作为源数据异常，不生成正常价格结果。

### 6.1 前端图片、标志和关系检查

总体生产链路中的前端检查和价格解析使用同一次商品页面导航、同一ASIN身份门禁和同一邮编/站点上下文。检查不得通过另一次无预算导航绕过价格任务的节奏与风控限制；检查脚本只读取已加载页面及其可见DOM/结构化数据。历史本地HTML可先用于构造离线样本和选择器匹配，生产结果必须来自当次实时页面。当前开发分支只验收前端检查函数和快照证据，不负责启动紫鸟/ZClaw或完成生产实时采集。检查结果不覆盖H:M价格/币种字段，也不把检查失败重新分类为价格成功。

- 商品主图：当前商品主图区域（优先`#imageBlock_feature_div`及其`#landingImage`/主图节点）须存在有效图片来源，并且该图片节点能在当前商品DOM中确认、未被隐藏/推荐/其他商品上下文门禁排除。规则不把图片CSS宽高作为独立通过条件；明确缺失为`fail`，页面不可用或身份门禁失败为`unknown`。该项只判断主图存在，不判断图片内容是否与周报一致。
- From the brand品牌故事图片：只接受当前商品A+ `#aplusBrandStory_feature_div`/`data-feature-name=aplusBrandStory`模块；该模块必须有精确的标题`From the brand`，并且同一模块内至少有一个有效图片项。模块为空、只出现泛化品牌文案、标题缺失或图片缺失均为`fail`；页面不可用或身份门禁失败为`unknown`。观察值必须分别记录`heading`和`image`，不再与N列主图合并。
- 尺寸一致性：读取当前选中变体或购买区展示尺寸，统一大小写、空格、乘号、单位和英制/公制书写后与周报预期尺寸比较；同一单位写在两个数字后（`8' x 10'`）或只写在末尾（`8 x 10 ft`）视为等价，英尺小数与英尺加英寸（`2.5'`与`2'6\"`）按英寸换算后比较，混合单位仍严格比较。页面尺寸后的`Rectangular`等非数值说明不参与比较。只有存在明确预期值且页面明确选中同一变体才可`pass`；缺少预期、未选中变体或多个尺寸无法确定时为`unknown`。
- 页面商品身份：在单项字段检查前，优先读取主商品`#title_feature_div[data-csa-c-asin]`，旧布局再回退到`#ASIN[value]`，并读取页面URL中的ASIN；至少必须存在一个可验证的当前商品身份锚点。若页面主商品ASIN或页面URL中的ASIN与请求ASIN不一致，或者两类身份锚点均缺失，N:T全部为`unknown`并显示`-`，不得从页面中其他商品模块拼接部分结果。
- BSR：读取当前商品详情表的`th.prodDetSectionEntry`字段，字段文本规范化后必须精确等于`Best Sellers Rank`，祖先必须属于`#prodDetails`、`#productDetails_feature_div`或`.prodDetTable`，并且同一详情表的`ASIN`行必须等于当前请求ASIN；没有ASIN行时才允许使用该详情链上的`data-csa-c-asin`/`data-asin`作为回退。Amazon页面常把该详情表放在折叠的`.a-expander-content`中；只要该字段属于当前ASIN详情表，就计为存在，不把“折叠”误判为缺失。导航中的`Best Sellers`、推荐/广告/轮播或其他ASIN区域的文字不计入。该项只判断字段存在，不读取实时排名数值。
- Amazon's Choice：只检查当前ASIN对应的`#acBadge_feature_div`，该容器或其祖先的`data-csa-c-asin`/`data-asin`必须包含当前请求ASIN，并且其可见后代节点必须有规范化后精确等于`Amazon's Choice`的实际badge文本。`a-popover-preload`、`aria-hidden=true`、`aok-hidden`、`aok-offscreen`及其他隐藏说明文本不计入；空占位容器、ASIN不一致的变体、导航、推荐商品或其他ASIN区域不计入。
- BSR与Amazon's Choice的“唯一性”首先要求各自只能归属于当前ASIN；另外业务上二者互斥：同一当前ASIN同时存在BSR和AC时，判定该ASIN不合法，`bsr_badge`和`amazon_choice_badge`均输出`fail`，保留各自的观察值和冲突原因，不得输出两个`pass`。页面身份门禁失败时七项统一为`unknown`并显示`-`。
- 父子ASIN发散：只读取`#inline-twister-expander-content-*`变体区域下面的`li.inline-twister-swatch[data-asin]`，从这些子体节点提取ASIN并排除当前请求ASIN。至少还有一个不同ASIN为`pass`；变体区域存在但没有不同ASIN为`fail`；变体区域无法确认时为`unknown`。`#twisterPlusPriceSubtotalWWDesktop_feature_div`只属于价格汇总，不再作为父子ASIN证据。
- 环保标志：只读取当前商品完整的 Climate Pledge Friendly 商品级标志链：`#climatePledgeFriendlyATF_feature_div`必须存在非空的`data-csa-c-asin`且其值必须与当前页面ASIN一致；缺失或不一致均不得通过。该模块后代还必须同时存在`#climatePledgeFriendlyBadge`、`#CPF-ATF-Card`、`.climatePledgeFriendlyATF`触发器、`.climatePledgeFriendlyProgramName`非空文本和有效叶子图标图片；叶子图片和文本必须落在同一个当前商品的`#CPF-ATF-Card`内。推荐/广告/轮播卡片、空的 ATF/BTF/A+ Sustainability 占位模块、品牌描述中的可持续文案、页脚推广链接及单独的`eco`词不计入。明确缺失为`fail`，页面身份或证据不足为`unknown`，不与周报字段比较。

所有检查均保留`observed`、`status`、`reason`、`evidence_locator`、页面URL和同一次DOM快照的`captured_at`；只有尺寸检查额外保留`expected`。检查规则、解析选择器或标志识别方式变化时递增独立的`frontend_check_rule_version`；本轮将主图与`From the brand`品牌故事图片拆为N/O两列，要求品牌故事模块精确标题+同模块图片，要求环保模块显式绑定当前ASIN，并要求至少存在一个当前商品身份锚点，规则版本升级为`2026-09-10-v11`，新增共享尾部单位和英尺小数/英尺加英寸的尺寸等价换算，旧bundle不能在新规则下被重新解释为新检查结果。BSR、环保和Amazon's Choice不读取周报预期，也不从上一周或上一批复制。

### 6.2 后台Feedback来源和筛选

Feedback来源是两个指定店铺的Amazon Seller Central【反馈管理器】页面的【最新反馈】区域，不是商品详情页评论、Review或Q&A。筛选条件固定为评级`<= 3`，不把缺少评级的记录默认当作合格。每个店铺必须在独立上下文中从配置URL进入、校验当前店铺身份，然后按日期窗口和【下一个】按钮分页；读取到合格反馈后逐条点击订单编号进入二级界面，读取订单商品编号、ASIN和SKU。

后台采集的会话、凭证、验证码和权限检查独立于零售页面的US/CA邮编与价格浏览器。每个页面导航、翻页和订单详情访问之间使用配置化的保守随机等待，并在页面稳定、身份和目标控件确认后继续；具体等待值必须进入运行日志，不能用固定高频循环。遇到登录页、验证码、风控提示、页面异常、分页状态不变化或订单身份不一致时立即停止当前店铺，不连续重试；另一店铺仍使用全新上下文独立执行。后台任务遵守统一运行锁和日志收口，但单店铺失败只阻断Feedback子表对应范围；价格结果可以继续发布。

Feedback状态只有在两店分页、筛选、必要的二级详情访问、近10日合并、写入和9列整表回读均有证据时才更新本批成功检查点。首次7日窗口未完成时不得进入后续3日窗口。每次日志必须保存整个Feedback子任务的`started_at`、`finished_at`、`elapsed_seconds`，以及每店铺的开始/结束时间、耗时、页面数、二级详情数、原始读取数、评级合格数、完整详情数、partial/blocked数、写入数和删除的过期行数。

## 7. 交付与安全边界

正式价格流程按安全行交付，不因技术异常率超过10%而整批零写入。阈值用于告警；身份、币种、结构和run_id校验不能由force-push绕过。

1. 先保存源快照与逐表bundle，再调用交付。
2. 全局预检目标、表头、ASIN唯一性和run_id；每个选定子表的源商品集合与结果集合必须完全一致，缺失、重复或多余ASIN都在写前阻断。
3. 修改每个结果子表前本地备份；同run_id重试保留首次原备份。
4. 按本批快照顺序组合完整A:G、同ASIN的H:M价格、N:T前端检查和U:V时间戳/链接，每段最多200行；覆盖新增、修改、排序和删除后尾行清理，写后核对整段A:V，不先清空再等待抓取。Feedback子表单独按`店铺 + 稳定feedback ID`或降级哈希幂等合并，按9列固定表头上下分组并回读。
5. 只有商品A:V回读通过才计入已验证商品写入，另记base_rows_written基础字段更新数和frontend_checks_written检查列更新数。Feedback子表必须单独记录`feedback_rows_seen`、`feedback_rows_eligible`、`feedback_rows_detail_complete`、`feedback_rows_written`、`feedback_rows_expired_deleted`、每店铺状态、每店铺耗时和9列整表回读结果。某表/范围失败保留已成功数量，继续其他安全子表；同run_id可重试，不把已有成功写入全部统计为0。
6. 技术异常或币种错误仍同步本批基础字段，但清空本条价格并标记-，N:T显示`-`并在bundle中记录`unknown`和阻断原因，U/V仍写本批时间戳和请求链接。不沿用旧价格配新目标价；写入失败范围可能保留旧数据，通知提醒核对时间戳。source_data_invalid不抓取，写异常空结果和N:T=`-`。前端检查为`fail`或`unknown`不改变已经通过价格门禁的H:L；价格门禁失败时不得把N:T写成`✅`。
7. 改名失败不得阻断本地记录和完成通知，不把预期名称冒充已生效名称。

创建结果子表后立即把Sheet ID登记到本批manifest.pending_result_sheets，再执行备份与写入。临时故障恢复时仅允许该登记身份对应的空表头继续初始化，未知空表仍禁止覆盖；完成后移除待初始化标记。创建API返回前断连或本地登记落盘失败等身份不确定情况仍需人工核对，不凭同名自动认领。

latest_run.json记录最新准备批次（period、run、快照和固定结果Token），与“已发布结果”fixed_result.json分离。发布、每段写入及改名均核对最新登记与磁盘manifest；不能先把固定表指针改成旧周期再校验。只有实际写入回读通过后更新发布指针，整批通过才更新active_result。新批次缺少最新登记或恢复旧版本证据时拒绝发布并要求新抓取。

每批保存A:G源字段指纹；恢复抓取、恢复写入及发布前重读快照时核验。即使ASIN集合没变，只要SKU、尺寸、目标价等变化也不能混用旧价格。映射为空的子表若出现ASIN表头，要求重新建立批次映射。新建run_id在同秒碰撞时增加微秒后缀。

正式源数据、ASIN审计、旧尾行读取和写前备份按元数据行容量分段，每次最多2000行并自限10000单元格，补齐API裁剪的中间空行以保留真实行号；不再把2000当作已知容量表的总上限。源数据读取与表头定位覆盖真实列容量，不限O列。正式客户端元数据缺少行容量时明确停止，不静默截断。结构副本校验逐Sheet ID取样，不依赖批量返回的标题/ID前缀。

自动任务默认覆盖全部发现的业务子表。显式--sheets仅更新选定子表，不动未选表；只有覆盖全部映射且无失败/阻断才更新active_result完整批次索引。整张源子表消失时不自动删除旧结果Sheet（避免破坏链接/权限），旧Sheet不计入当前映射；当前映射内的空表会清理旧数据行。

## 8. 验证原则

业务修改先离线测试，再只读检查，再US/CA最小样本与最小子表，最后全量。每次记录测试开始、结束、耗时、范围、结果和是否真实写入。单元测试不代表真实Amazon可用、真实群发已读或新周云端切换通过。

离线命令见第18节。真实运行的总耗时包含准备、抓取和交付；通知逐人发送耗时可从送达记录/外层调度日志查看，不将采集阶段时间伪装为所有外部投递的总墙钟。

## 9. 浏览器、节奏与超时

正式配置workers=4；每次处理一个子表/Marketplace，使用一个浏览器、最多4个互斥商品Tab，而不是4个浏览器。商品不足4个时减少Tab。US/CA上下文分别初始化，不并发混用邮编。

当前配置：page_timeout=30秒、price_wait_timeout=12秒、per_asin_timeout=90秒、retry=2；风险冷却配置60～180秒。程序对Captcha、访问受限和429/503记录风险，不自动切换VPN/IP，也不绕过验证码。飞书 Drive 创建周报副本遇到408/429/5xx或传输超时时最多退避重试3次；每次重试前按精确副本名称回查根目录，避免超时后服务端已成功而客户端重复创建。

每个CLI入口（人工、定时、恢复及维护）由Python持有同一个outputs/weekly_scheduler.lock，覆盖准备、抓取、发布、通知全过程。PowerShell仅负责启动与日志，不重复占锁；已有任务时第二个入口以75退出，不改变云端状态。调度包装器必须保留Python真实退出码，并在异常和非零退出时仍写入`END`收口行；锁文件残留不代表进程仍存活，OS释放锁后可重启。

每个商品完成或失败后等待随机1～3秒再释放Tab，HTML关闭也执行；HTML开启时归档结束后只等待一次。复用post_archive_delay_min/max配置及post_archive_delay_seconds日志字段，不增加另一组重复配置。导航、身份或解析失败需要重试时必须关闭并重建当前Tab，不能让下一次请求继承上一商品页面；重建后的Tab仍由同一worker独占并最终归还池。`tab.get()`明确返回失败时必须标记`navigation_failed`并停止读取；不得在导航失败后解析旧DOM。

导航、文档/价格等待、页面脚本读取和稳定等待按剩余deadline裁剪；禁用导航内部重试，重试只由外层统一执行，每次直接重新导航，不插入额外无预算refresh/rebuild。`doc_loaded()`抛出异常或显式返回`False`时必须标记`navigation_timeout`并停止读取，不能继续读取`location.href`或价格。返回成功前复查截止时间，超时清除价格并标记deadline_exceeded。初始化首页timeout为30秒（不是30000秒）。商品间1～3秒等待不计入90秒抓取预算；浏览器驱动/操作系统失去响应仍不是进程级强制终止保障，不能宣称任何环境下墙钟严格90秒。

## 10. 多站点、ASIN和币种

| 路由 | 域名 | 币种 | 邮编 |
|---|---|---|---|
| PD/XD/PDF等US映射 | www.amazon.com | USD | 90210 |
| CPD对应CA映射 | www.amazon.ca | CAD | M5V 3A8 |

复用同一抓取、解析、计算及写入代码，不复制一套CPD爬虫。CA邮编设置使用M5V、3A8两段；只有页面实际截断为五位时允许visible_prefix5证据，若页面含完整六位则必须完整匹配，M5V3A9不能通过M5V3A8校验；US仍要求完整邮编匹配。链接审计与源行读取统一处理裸ASIN、商品URL及富链接，避免审计通过后商品被解析器静默跳过。

正式setup强制邮编验证，失败时该子表不采信价格；fetch_once也校验location_verified。正常商品页最终host必须匹配目标站点，并从主价原始文本读取币种：US$/USD与CA$/CAD不能互相替代；裸$仅在已验证站点和邮编上下文下解释。币种未知/冲突为currency_error，不参与价格比较。

ASIN提取支持纯编号、普通URL、飞书富文本链接及HYPERLINK公式。源快照发现阶段允许业务表头写成`ASIN`或带换行/括号说明的`ASIN\n(...)`，但不把`ASIN_CODE`等普通字段误认作ASIN列。显式URL先验证精确host和Marketplace，拒绝恶意子域、非Amazon和跨站URL；坏URL不能退回显示文字中的ASIN绕过检查。源单元格中的合法Amazon链接必须同时保存为`source_product_url`，并优先作为请求链接使用，以保留`?th=1`、`?psc=1`等变体上下文；纯ASIN或无显式链接时才回退标准输出：`https://www.amazon.com/dp/{ASIN}` 或 `https://www.amazon.ca/dp/{ASIN}`。源链接ASIN必须与ASIN列一致，否则该行无效并进入审计。

导航后先识别明确Page Not Found，再校验最终URL中的商品ASIN。相同ASIN附带?th=1、?psc=1不算异常，也不是邮编证据；正常页跳到其他ASIN属于identity_mismatch并阻断。明确404按page_not_found处理，不误报变体跳转。

成本、颜色、广告等非商品标签跳过并审计；看似商品但编号无效的行记录无效。全部发现到的业务子表参与映射，空模板保留映射、不制造商品；辅助BI源数据排除。新周不得用历史18表名单截断发现结果。

只有US/USD和CA/CAD的已知组合参与价格比较；未知/错误币种为currency_error，禁止写该行正常比较结论。CSV金额单位随站点，不固定USD。

## 11. 独立HTML归档

本节仅定义已保留的独立能力和恢复验收，不是价格任务前置条件。

采用同一已加载Tab生成单个自包含普通.html，解析输入仍是实时页面。SingleFile封装并原子落盘，验证HTML结构、目标ASIN、大小和SHA-256；不能仅凭退出码或扩展名判定成功。不默认二次导航商品或另开浏览器；MHTML只作诊断/对照。

离线验收：断网打开文件后，标题、ASIN、价格、优惠文案、主图和核心可见布局可读，查看不再发起HTTP(S)资源请求。登录、购物车、视频、实时推荐及服务端操作不属于静态快照保证范围。

归档命名：`htmls/YYYY-MM-DD/{run_id}/{子表顺序_名称}/{商品顺序_ASIN}.html`；manifest保存路径、哈希、字节数、时长和状态。每次独立批次用不同run_id。

保留今天及前4个日期目录，共5天；只清理归档根内过期日期目录，不跟随符号链接/Junction越界。检查实际归档卷容量。原先每批约2GB、两批/日是容量估算，不是实测保证；以实测体积留出安全余量。价格入口关闭HTML时不执行归档清理或磁盘门禁。恢复HTML需独立小表及断网验收，不自动加回结果列或价格依赖。

## 12. HTML文件访问

本机文件URL仅指向已存在、位于归档根内的普通.html；URI编码正确，不伪造下载未完成的链接。

独立局域网服务代码及脚本保留但默认关闭；历史默认端口为8765，只读访问归档根。当前自启动任务禁用、监听进程停止、防火墙允许规则保持禁用，既有HTML不删除。价格调度安装/移除不重新开启HTML服务。当前结果表和完成通知均不输出HTML链接或端口状态。不得据此自动开放公网、安装隧道或修改防火墙。

## 13. 日志、恢复证据与通知

当前parser_rule_version为2026-08-26-v4，单表缓存schema=6、weekly bundle schema=3。bundle记录源指纹、规则版本、容差、前端检查规则版本、来源时段和created_at；恢复必须匹配当前版本/容差/源指纹，且时间不在未来、不超过cache_max_age_hours（默认12小时）。同时验证每条有效商品的采集timestamp，不能通过重存文件给旧价格续期。旧schema、缺少元数据和过期结果拒绝发布，要求重新抓取；兼容恢复的schema=2记录缺少前端证据时只能恢复为`unknown`，不能解释成当前规则下的通过。不会删除旧证据。版本同时维护config.json、config.example.json及app/config.py默认值。

缓存/manifest/bundle等采用唯一临时文件、flush/fsync后原子替换；线程内替换串行化，单表增量快照的创建与写盘也串行化，避免较旧快照后写覆盖。Tab获取或增量缓存失败逐行/逐次记录，不无声终止工作线程；最终缓存失败保留采集结果交给weekly bundle持久化，bundle落盘失败则不继续发布。

通知每发送一人即保存回执。同run_id、同业务结果恢复时跳过已送达成员，仅重试失败/新成员；跨日期恢复也读取同一回执账本。正式运行可显式使用一次性`--notify-manager-only`仅通知`feishu_manager_open_id`，该开关不修改默认应用协作者范围，后续不带开关即恢复全体协作者。业务统计变化可发送更新结果。网络超时发生在服务端接收与本地回执之间时，当前不承诺严格exactly-once，须核对message_id/飞书实际送达。

产物路径以第18节为准。run_id贯穿抓取快照、缓存、CSV、bundle、交付记录、目标备份和通知回执。

当前文本日志按log_keep保留最近30个文件，不能写成“已保留30天”；按天轮转尚未实现。debug清理为7天，正式诊断只保存截图与JSON、不再新增page.html；既有诊断HTML与历史归档均保留。调度日志、bundle、备份和周资源状态没有通用自动五日删除。Feedback结果表只保留近10个自然日，但`outputs/feedback/{run_id}/`中的运行日志、分页/二级详情证据和摘要按项目证据保留策略保存，不能因结果表清理而删除本地审计。五日规则只属于独立HTML日期目录。

完成通知统一由result_notification.py生成：

- 首行：Hi，有个任务完成请查收.
- 标题：Amazon 周报前端价格捕捉任务
- 分组展示周期、run_id、起止时间、耗时、商品子表数、商品写入/阻断数、前端检查通过/异常/待确认数；正式抓取含价格技术异常率。
- 单独展示`Feedback差评汇总`的两个店铺状态、读取条数、评级小于等于三星条数、二级详情完整条数、写入条数、清理过期行数、每店铺耗时和失败店铺；Feedback失败不能被商品价格统计掩盖。
- 周期对人显示为run_id日期对应的ISO周（例如2026-W35），并附内部登记序号用于追溯；内部manifest仍使用登记表序号，不能只改通知文字伪造换周。结果表名称同样使用ISO周和run_id。
- 结果表使用实际名称与固定可点击链接；说明文档为[关于上述表格的简要说明](https://wit0jhu6kvu.feishu.cn/wiki/G531wP7WNiepV3krnrHcavqin6d)。
- 不含HTML端口行、证据字样或模拟声明；不能使用虚构数据冒充已执行。
- “本地数据”路径只对feishu_manager_open_id配置的周成业可见；其他人移除整行。
- 动态查询应用协作者、按Open ID去重并包含周成业。逐人发送，一人失败继续其他人；名单查询失败仅通知管理员并记录群发不完整。
- 一批一次全量汇总，不逐子表推送。运行/投递问题另通知周成业；发送成功不等于已读，应用协作者不自动等于文档协作者。

## 14. Docker部署边界

Docker是价格任务的可选部署方式，不改变业务流程和固定结果表身份。当前已准备可构建的PoC骨架，但开发机未安装Docker CLI，尚未把镜像构建、Chromium无头、Amazon出口或容器cron称为实机验收通过。价格任务默认关闭HTML，HTML服务/归档与价格任务保持独立。

Docker镜像必须持久化 `outputs`、`data` 和可选的 `htmls` 卷；Secret只通过运行时环境变量或未提交的`.env`注入；时区固定 `Asia/Shanghai`；cron只在容器内运行周一至周五07:30/15:30。Windows任务计划和容器cron不得同时启用同一份结果表。

为支持跨设备迁移，配置加载允许显式环境变量覆盖少量机器相关项：`AMAZON_HTML_ARCHIVE_ROOT`、`AMAZON_HTML_ARCHIVE_ENABLED`、`AMAZON_HTML_ARCHIVE_REQUIRED`、`AMAZON_HTML_SERVER_ENABLED`、`AMAZON_HTML_SERVER_BIND`、`AMAZON_HTML_SERVER_PORT`、`AMAZON_WORKERS`。未知环境变量不参与配置，避免隐藏逻辑。容器内HTML路径必须是POSIX路径（推荐`/app/htmls`），不能沿用Windows盘符。

迁移前先停止旧调度器并确认没有运行锁；复制代码及持久化卷，不复制`.venv`、缓存或临时文件；新设备先执行只读登记检查和`--weekly-run --dry-run --limit 1`，核对源快照、US/CA出口、币种、日志和健康检查后再正式写入。Amazon数据中心出口、DrissionPage/Chromium、CA邮编和网络风控仍需目标设备在线验证。完整命令、回滚和风险见 `deploy/docker/README.md`。

## 15. Secret和资源权限

App ID为非敏感配置；真实Secret只走第3节来源。不输出完整凭据用于“验证是否存在”。

每次创建或复用本批快照、固定结果表及其他应用交付资源，给配置指定的周成业添加可管理协作者并回查；不从多人名单猜管理员。文档管理权限不能绕过组织分享限制；消息可用范围由管理员维护，程序不自动扩权。

## 16. 周报登记、快照与固定结果表

固定登记入口：[周报链接登记表](https://wit0jhu6kvu.feishu.cn/wiki/HwxpwCnZ7iV1o5klIGbc8wJHnrd)。

实际字段：序号、飞书链接、更新时间。忽略链接空行，非空行序号须为唯一正整数；普通“本周切换”选择最大有效序号，但周一07:30是例外，必须选择上一周期而不是最大序号。更新时间不参与序号排序，只用于稳定性和审计。链接首次发现时间由本机`outputs/weekly_runs/registry_freshness.json`持久记录，正式新批次沿用超过配置上限（默认8天）即停止并要求登记新序号，不能靠每次读取重置年龄。最新链接无权限/无效时停止，不退回旧周；同序号更换URL同样停止。明确恢复既有run_id按其已固化快照执行，不因登记老化破坏恢复。

支持/wiki节点解析及/sheets直链，?sheet只定位页面，不限制全表枚举。允许域名与Token白名单校验先于API调用。登记表永久只读。

固定结果：[Amazon周报结果表](https://wit0jhu6kvu.feishu.cn/sheets/Epads8MQkhkuBctjl3lcqLUvnCg)，Token为Epads8MQkhkuBctjl3lcqLUvnCg。正式任务不新建替代表。商品结果子表使用A:V布局；Feedback差评汇总使用单独固定Sheet ID并与商品子表分开登记，目标表可见结构固定为9列，不把内部幂等键或运行状态追加到可见表头。来源快照的创建和复用遵循下方换周时点规则：周一07:30不创建本周新快照，复用上一周期已固化快照；周一15:30锁定登记表最新有效链接并创建本周新快照；周二至周五复用已确认的本周周期快照。相同run_id中断恢复复用已登记资源，不重复复制。快照名包含period_id/run_id及generation；在应用Drive根目录保存完整副本，记录URL/Token、校验结构后重新发现映射。

周一07:30的早间任务明确沿用上一周期数据；周一15:30完成一次本周切换后，周二至周五早晚任务均沿用本周周期。每个时段仍生成独立run_id并重新抓取价格；同一来源周期的基础A:G必须来自该周期固化快照，不得从正在变化的原始周报读取。先抓取、保存本批bundle，再备份并按完整A:V行块发布固定表；同名子表保留Sheet ID，仅新增业务表或固定Feedback子表首次登记时添加Sheet，清理旧尾行。数据、价格和前端检查来自同一run_id；Feedback合并结果单独以店铺和内部稳定feedback键发布，不把旧批价格或旧检查结果拼入新基础字段。恢复仅接受当前批次，旧批manifest保存在runs目录用于追溯，不作为回滚入口。

表名在成功发布数据后尝试更新为`Amazon周报前端价格捕捉_{period_id}_{run_id}`；仅基础字段成功更新或空表发布也同步名称。通知和本地manifest同时记录`source_period_id`及`selection_mode`（`monday_carryover`、`monday_switch`或`weekday_steady`），避免周一早间使用上周数据时被误认为周期没有更新。固定身份及weekly manifest一起保留，固定登记缺失时正式抓取/恢复均停止。

周期准备和所有CLI使用OS文件锁，进程退出释放；.locks标记文件仍存在不等于任务运行中，不手工删锁“解锁”。Windows调度和直接CLI具有同一整批互斥。开机补跑同时命中早晚任务时仅一批取得锁，另一批退出75；调度包装器将其记为“已有批次运行，跳过”并向任务计划程序返回成功，避免假故障和重复全量。每份调度日志文件名含毫秒与PID，互不覆盖。

### 16.1 周一换周时点规则

- `period_id`（例如`seq-4`）代表周报来源周期；`run_id`（例如`20260908_073003`）代表一次执行。两者不能互相替代。
- 周一07:30的`selection_mode=monday_carryover`：只读登记表用于审计和确认控制面可用，但业务输入必须来自上一周期已经`ready`的manifest和完整快照。不得读取本周最新链接，不得创建本周新快照；上一周期快照缺失、损坏或不可读时安全停止，不回退到任意旧快照或直接读取原表。
- 周一15:30的`selection_mode=monday_switch`：重新读取登记表，要求存在比上一周期更高的唯一有效序号、合法飞书链接、可读取的Spreadsheet及可完成的副本结构校验；满足后创建本周新快照并把周成业加入管理协作者。没有新序号、链接仍是上一周期、URL无权限、复制超时或结构校验失败时，禁止使用上一周数据冒充本周，安全停止并通知周成业。
- 周二至周五的`selection_mode=weekday_steady`：复用周一15:30已经确认的本周manifest/快照，每个时段只重新抓取实时价格和前端检查。登记表如果出现新的更高序号或同序号换URL，不在非换周时点静默切换；记录`pending_period_change`并通知周成业，待下一个周一15:30或明确人工维护后处理。
- 周一15:30切换必须在同一运行锁内完成“登记表读取、周期选择、源Token解析、完整副本创建、结构校验和manifest固化”。切换成功前固定结果表不得写入本周A:G或本周价格；切换成功后才允许抓取和发布。
- 正常任务通知中必须同时显示执行时段、`source_period_id`、`selection_mode`和`run_id`。周一早间应明确标注“沿用上一周周报”，周一下午应明确标注“已切换最新周报”。
- 调度入口必须把稳定的`scheduled_slot`传给Python（例如`monday_0730`、`monday_1530`、`weekday_0730`、`weekday_1530`）；不能只用进程实际启动时间推断时段。`StartWhenAvailable`造成延迟补跑时仍按原计划时段选择来源。人工运行必须显式标记为`manual`并指定来源策略，不能伪装成周一早间或周一下午。

### 16.2 Feedback早间窗口和状态账本

- Feedback逻辑槽位为`feedback_0730`，只在周一至周五`07:30`（`Asia/Shanghai`）执行；价格任务的`15:30`运行不触发Feedback。统一运行锁已被占用时，Feedback不得另起并发写回，应记录`skipped_lock`并等待下一个定时窗口或明确人工重试。
- `outputs/feedback/feedback_state.json`只保存非敏感的窗口状态：首次窗口是否完成、最近一次可推进的运行时间、两个店铺各自的窗口边界、最后成功读取到的分页/反馈游标摘要和规则版本。凭证、Cookie、Authorization、完整订单详情响应不得写入状态账本。
- 当且仅当两个店铺都完成窗口边界读取、结果合并、近10日过滤、目标表9列回读，且没有未处理的认证/风控/分页阻断时，才将首次7日状态推进为后续3日。单店铺失败、二级详情未完成或写后回读失败时保留旧检查点，下次不得缩短为3日窗口。
- Feedback的来源URL、页面标题/区域定位、分页页码、【下一个】按钮状态、详情订单身份、抓取时间、等待时间、运行起止时间和退出原因写入`outputs/feedback/{run_id}/`；只保留脱敏后的诊断。

## 17. 页面状态与输出

| 情况 | 状态 | 当前正式交付 |
|---|---|---|
| 正常主价 | ok | 计算并写入安全行 |
| 请求ASIN被站点跳转为其他ASIN | identity_mismatch | 重建Tab并按重试规则复测；仍不一致时阻断该行，保留请求/最终URL与证据；属于源链接或站点可用性问题，不计为技术抓取异常 |
| 明确404 | page_not_found | 折扣类型及一致性为- |
| 当前商品availability控件明确售罄且无主价 | sold_out | 确认后以-输出，不当技术异常 |
| Captcha、加载失败、身份不匹配 | crawl_error | 阻断，记录原因和证据 |
| 主价冲突或无法可靠解析 | parse_error | 阻断，不能冒充售罄 |
| 站点/币种组合错误 | currency_error | 阻断，不换汇、不比较 |
| 正常售价/目标价等源字段无效 | source_data_invalid | 不抓取，写异常空结果 |
| 商品页可用但某项前端检查证据缺失 | frontend_check_unknown | 价格结果可按价格门禁交付；对应N:T显示`-`，bundle写`unknown`并记录缺口 |
| 前端检查明确不符合预期 | frontend_check_fail | 价格结果不因该项单独阻断；对应N:T显示`❌`，bundle写`fail`并进入检查异常统计 |
| Feedback单店铺认证、分页、二级详情或读取失败 | feedback_partial / auth_error | 关闭当前店铺上下文并继续另一店铺；Feedback子表标记部分完成并通知周成业，不回滚已验证商品结果 |

异常商品URL、最终页面URL、状态和原因保存在缓存/bundle；前端检查证据另保存检查名、观察值、可选expected、状态和定位信息；技术错误按配置保留截图及JSON诊断，不新增页面HTML。历史诊断HTML继续留存但不再增长。Feedback分页、评级筛选、二级订单详情、去重、近10日清理、耗时和写后回读证据保存到`outputs/feedback/{run_id}/`。周报中名为`Sheet数字`的通用销售/导出表若只有ASIN、MSKU、销量等字段而缺少“正常售价”和“目标成交价”，按辅助表排除；未知命名且具价格业务表头的ASIN表仍阻断并要求人工确认，防止新业务子表被静默漏掉。

## 18. 当前CLI、产物与故障处理

### 18.1 正式入口

启动中心选项3为PD03单条dry-run，选项4为weekly-run --confirm全量；未指定流程及旧--push-only拒绝执行，避免误入旧六列写入。工作日07:30/15:30的Windows任务使用无控制台的`wscript.exe`→`bin/hidden_ps1.vbs`→隐藏PowerShell→`scheduled_run.ps1`链路；`scheduled_run.bat`和HTML维护BAT仅保留兼容入口，完全无窗口的手工调用应直接使用同一`wscript.exe`启动器。手工启动中心仍可见，便于交互选择。

以下从项目根目录运行：

```powershell
# 离线测试
$env:PYTHONPATH='app'
.venv\Scripts\python.exe -m unittest discover -s tests -q

# 登记表只读检查
.venv\Scripts\python.exe app\main.py --inspect-weekly-registry

# 最小表验证：只读当前原表，不创建云端副本、不写飞书
.venv\Scripts\python.exe app\main.py --weekly-run --dry-run --sheets PD03 --limit 1

# 正式全量价格任务：按周一早间沿用/周一下午切换规则选择来源，刷新A:G及价格、发通知
.venv\Scripts\python.exe app\main.py --weekly-run --confirm

# 恢复同周期指定批次；会写固定表并发送通知，不重抓Amazon
.venv\Scripts\python.exe app\main.py --weekly-push-only --run-id <已有run_id> --confirm
```

启动中心选项3执行PD03单条dry-run，选项4执行weekly-run --confirm全量；无模式入口和旧--push-only拒绝执行。实际工作日07:30/15:30计划以第19节为准。

--new-week、--recreate-weekly-assets、--sync-weekly-result-base及PoC/迁移命令仅用于明确批准的维护；尤其旧基础同步命令会先清空，不能作为每日任务或自动换周前置步骤。--discover-weekly-mapping和--audit-product-links对云端只读，但会更新本地发现记录。

### 18.2 运行产物

| 路径（相对项目根） | 用途 |
|---|---|
| outputs/weekly_runs/fixed_result.json | 固定结果Token、URL和当前发布周期，必须备份 |
| outputs/weekly_runs/latest_run.json | 最新准备批次身份，防旧周期/旧run发布，必须备份 |
| outputs/weekly_runs/registry_freshness.json | 登记序号、URL及首次发现时间，正式换周过期门禁，必须备份 |
| outputs/weekly_runs/{period_id}/weekly_manifest.json | 当前run_id的原表、快照、固定结果、映射、`source_period_id`、`selection_mode`及发布状态，必须备份 |
| outputs/weekly_runs/{period_id}/runs/ | 以前批次完整manifest及逐批链接审计；历史登记不可直接恢复覆盖新批次 |
| outputs/weekly_runs/active_result.json | 最近完整验收批次索引，不代表最后一次部分写入 |
| outputs/weekly_runs/.locks/ | OS锁标记 |
| outputs/snapshots/{run_id}/source.json | 本批源数据及资源身份 |
| outputs/fetch_cache/{run_id}/ | 增量抓取缓存 |
| outputs/daily_runs/YYYY-MM-DD/{run_id}_weekly_bundle.json | 逐表结果及本次`source_period_id`/`selection_mode`，恢复主要依据 |
| 同目录的_weekly_summary.json、_delivery.json、_weekly_push.json | 汇总、交付检查点、恢复写入记录 |
| 同目录的_notifications.json | 逐人message_id或失败原因 |
| outputs/daily_runs/notification_receipts/{run_id}.json | 跨日期逐人回执账本与业务消息去重 |
| outputs/target_backups/{run_id}/ | 写前首次备份 |
| outputs/logs/、outputs/scheduler_logs/ | 程序日志、Windows入口日志 |
| outputs/csv/、outputs/debug/{run_id}/ | 逐行诊断及异常证据 |
| outputs/discovery/、outputs/poc_resources/ | 发现报告、历史探针记录 |
| outputs/feedback/{run_id}/ | 两店铺Feedback分页、筛选、去重和合并证据；不得保存Secret、Cookie或Authorization |
| htmls/（可配置） | 独立HTML文件，不参与正式价格门禁 |

### 18.3 故障处理

先保留bundle、delivery和备份，区分抓取阻断、写入失败、改名失败、通知失败。不要为了补一行重新清空整表。

本批准备失败检查登记链接、复制权限、pending资源及映射；缺失fixed_result登记从已验证备份恢复，不新建结果表。同序号改源URL需新增序号。--resume/--run-id只能继续当前已登记批次；不会刷新其输入副本。恢复时验证run_id、Token、快照和原定子表范围，新批次已替换manifest时旧运行禁止发布。

不能用Windows退出码0或通知标题单独认定完整成功；需检查delivery状态、blocked/failures和逐人回执。现存缺陷及建议修复范围集中记录于REVIEWS。

## 19. Windows调度与验收

### 19.1 部署

建立.venv并安装config/requirements.txt，按第3节注入Secret，保留本机JSON和资源登记。先离线测试、只读资源检查、US/CA最小样本、最小表，再全量。不依赖固定美国出口即可满足CA；须实际验证各站邮编和币种。

### 19.2 每天两次与周一换周

当前Windows任务由`bin/schedule.ps1`创建四条价格槽位任务：周一07:30 `AmazonDaily_0730`、周二至周五07:30 `AmazonDaily_0730_weekday`、周一15:30 `AmazonDaily_1530`、周二至周五15:30 `AmazonDaily_1530_weekday`。Feedback逻辑任务复用周一至周五07:30的两个价格槽位，在同一进程和统一锁内只执行一次；不得额外安装与之并发写`Feedback差评汇总`的独立任务。四条任务均为WeeksInterval=1、EndBoundary为空。任务直接以`wscript.exe //B //NoLogo bin/hidden_ps1.vbs bin/scheduled_run.ps1 <scheduled_slot>`启动隐藏PowerShell，再执行`app/main.py --weekly-run --confirm --scheduled-slot <scheduled_slot>`，07:30槽位额外执行`feedback_0730`阶段，不经过可见的bat/cmd窗口。旧版本若仍显示`Execute=cmd.exe`或`scheduled_run.bat`，必须重新运行安装脚本替换任务定义。

当前账号为Interactive：需电脑开机且账号已登录；不需GPT窗口。任务Hidden=true、Execute=wscript.exe、WScript批处理模式与PowerShell WindowStyle=Hidden共同保证不创建可见终端；用户不能因关闭控制台误停。StartWhenAvailable=true，开机或恢复后补触发错过时段，入口必须把原定`scheduled_slot`继续传给Python，不能按实际补跑时间重新判断来源；整批锁与IgnoreNew共同防重叠。日志使用UTF-8保存开始/结束、退出码和`scheduled_slot`。安装脚本bin/schedule.ps1创建这两条工作日任务；不操作HTML服务或防火墙。不要另装重复调度器。

调度时段与来源周期必须按第16.1节解释：周一07:30只运行上一周期的`monday_carryover`，周一15:30执行`monday_switch`并要求登记表已有更高的最新有效序号，周二至周五07:30/15:30运行`weekday_steady`并复用周一15:30固化的本周manifest。`StartWhenAvailable`导致周一07:30延迟补跑时仍保持该时段的上周来源规则；如果错过周一15:30，不能将补跑伪装成周一早间，必须记录实际`selection_mode`并在没有最新周报时停止。

周一15:30切换失败不得自动回退使用上一周结果冒充本周；应保留上一周固定结果不变，记录失败原因并只通知周成业。周二至周五发现登记表有新周期时不得无感切换，除非进入周一15:30切换窗口或明确执行人工换周维护。

单次历史07:00任务不是长期规则；本次文档审查不删除或修改其他Windows任务。

### 19.3 验收顺序

离线回归（含A:V列布局、七项前端检查、Feedback评级`<=3`、首次7日/后续3日窗口、近10日清理、二级订单详情、分页【下一个】、两店串行、风控停机、去重替身、周一早间沿用和周一下午切换规则）→ 登记表及云端只读核对 → US/CA样本及前端证据 → 最小商品子表写入 → 两店铺Feedback慢速只读分页与二级详情读取 → 固定子表9列写入/近10日清理/整表回读 → 全量 → 至少一周工作日双时段稳定性、失败恢复和“周一07:30上周/周一15:30本周/周二至周五本周”来源验收 → 再评估Docker。

价格和前端检查验收不要求HTML下载或端口；独立HTML恢复另行断网验收。真实首次A:V列迁移、Feedback首次7日窗口、两个店铺后台登录/身份核验、评级`<=3`分页、订单二级详情、固定子表9列写入和近10日清理、后续3日增量、周一早间复用上一周期、周一下午切换新周期、下一周工作日稳态复用及全员通知须留存对应实际证据，不能以离线测试打勾。所有测试耗时写TASKS。

## 20. 交付边界

Git交付源码、配置模板、依赖、脚本、SPEC/TASKS/REVIEWS和测试。Secret、.venv、原始数据、HTML、日志、缓存和备份不提交Git；“不进Git”不等于“部署可以丢弃”，运行资源登记必须通过受控备份迁移。

历史验证与未完成项只维护在TASKS，当前缺陷只维护在REVIEWS。本文件不写不断过期的“下次运行时间”或“全流程绝对没问题”结论。

### 20.1 生产目录与开发目录隔离

- 生产运行根目录固定为`D:\projects\amazon_daily_structured_20260821`。Windows任务由`bin\schedule.ps1`根据脚本所在目录解析`projectRoot`，因此计划任务只允许从该生产根目录启动。
- `D:\projects\amazon_daily`不是独立项目，而是指向生产根目录的Windows Junction；在该路径编辑文件等同于直接编辑生产代码，禁止将它当作开发副本。
- 开发工作区使用独立Git副本`D:\projects\amazon_daily_dev_20260821`。开发副本不复制生产`.env`、`outputs`、`htmls`、`data`、`tmp`或`.venv`，不安装`AmazonDaily_0730/1530`计划任务，不连接生产运行产物。
- 代码变更顺序固定为：开发副本修改 → 离线测试和只读检查 → Git提交/推送 → 取得生产锁并做文件白名单发布 → 发布后逐文件SHA-256核对 → 观察下一次计划批次。禁止在生产根目录直接编辑后依赖“撤销”恢复。
- 发布脚本必须显式接收“开发路径”和“生产路径”，解析两者真实路径并拒绝相同路径或Junction路径；默认只复制代码、脚本、配置模板和文档，禁止覆盖生产Secret、运行产物、虚拟环境和历史HTML。
- 生产发布前保留`outputs/code_backups/{release_id}`，发布期间不得执行全量抓取；若校验失败，停止发布并根据备份回滚代码，不删除固定结果、manifest、bundle或通知证据。
