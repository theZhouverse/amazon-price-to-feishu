# Amazon Daily

当前正式入口、部署和运行命令见[docs/SPEC.md](docs/SPEC.md)第18～19节。目录及配置职责见第3～4节；实施状态见[docs/TASKS.md](docs/TASKS.md)，未解决问题见[docs/REVIEWS.md](docs/REVIEWS.md)。

生产部署仅支持 Windows 宿主机本地运行（项目 `.venv`、本机 Chromium/紫鸟会话和隐藏计划任务）；Docker/容器方案已取消，不在当前交付范围内。

启动中心选项3执行PD03单条只读验证，选项4执行正式周报全量；Windows工作日07:30/15:30也进入weekly-run。周一07:30沿用上一周已固化周报，周一15:30切换登记表中的最新周报，周二至周五沿用本周周期；该来源时点规则以SPEC第16.1节为准。无参数及旧push-only拒绝执行，防止误入旧六列流程。真实在线验收边界见REVIEWS。

源码在app，配置与模板在config，脚本在bin，测试在tests；完整文档导航见[docs/README.md](docs/README.md)。

生产运行目录是`D:\projects\amazon_daily_structured_20260821`；`D:\projects\amazon_daily`是指向该目录的Junction，不是开发副本。开发请使用隔离目录`D:\projects\amazon_daily_dev_20260821`，通过测试和发布校验后再按SPEC第20.1节发布到生产目录。

outputs不进入Git，但其中固定结果身份、周manifest、运行证据及写前备份必须保留并随部署迁移，不能作为普通缓存整体删除。Secret仅按SPEC指定来源注入，不复制成多份本地配置。

所有CLI与Windows调度共用运行锁；发布以latest_run登记和本批源指纹为准，旧批次不得覆盖新结果。v4不接受旧规则缓存恢复，需新建正式抓取批次。当前完整优化清单与离线验证范围见TASKS顶部，未完成的在线验收见REVIEWS。

HTML归档和8765服务当前全部关闭，既有htmls文件只读留存。Windows任务已在目标 Administrator 账户重新安装并回读为隐藏 `Ready`：直接以 `wscript.exe` 启动 `hidden_ps1.vbs`，不再经过可被误关的终端窗口。通知与固定结果表名称使用运行日期的ISO周，同时附内部登记序号供审计。

生产收口增加登记链接首次发现8天门禁；同一序号不能通过重复读取续期。开机补跑重叠时只执行一批，另一计划实例正常记为跳过；调度日志以毫秒和PID区分。正式诊断不再新增HTML文件。

周报中的通用 `Sheet数字` 销售/导出表不会因含 ASIN 被误当价格子表；正式异常会记录完整 traceback。计划任务和调度 BAT 使用隐藏、非交互 PowerShell，避免终端弹窗干扰运行。Feedback差评模块复用工作日07:30价格进程和统一锁，默认关闭；只有登记两店会话、Feedback固定Sheet ID和经过页面验证的选择器后才允许启用，未登记时不会访问Seller Central或写入`Feedback差评汇总`。
