# 项目文档导航

| 文件 | 唯一用途 |
|---|---|
| [SPEC](SPEC.md) | 当前业务、流程图、架构、配置、操作和验收规则 |
| [TASKS](TASKS.md) | 当前任务、验证耗时和历史实施证据 |
| [REVIEWS](REVIEWS.md) | 当前尚未解决的代码审查意见 |
| [历史目录](history/README.md) | 被替代的旧版文档，仅用于追溯 |

阅读顺序：SPEC → TASKS当前索引 → REVIEWS未解决项。完整目录树及文档维护矩阵见SPEC第4节，正式命令和Windows调度见第18～19节。

当前生产入口按SPEC执行周一07:30沿用、周一15:30切换、周二至周五稳态运行；本地工作区与 GitHub `origin/fix-codescan-20260826` 保持同分支同步（合并基线为 `ffcd6ea`）。2026-09-10 的隐藏窗口全量运行已验证18个价格子表和两个Seller Central店铺Feedback，Feedback固定子表 `41u25y` 写入10条并通过9列回读；价格批次仍以 `partial` 状态保留 CA 身份阻断证据。HTML归档与局域网服务当前关闭，既有历史HTML不删除；当前隔离测试表 `GA6PsnlcjhTGsqtBocdcVct7n2e` 已完成22列表头校准、空白遗留表清理，并把 `Feedback差评汇总` 置于最后（详见TASKS/REVIEWS最新节）。前端规则版本为 `2026-09-10-v12`，BSR/AC按当前商品独立存在性判断；后续工作重点为下一次07:30的3日增量窗口和CA异常收敛，详见TASKS与REVIEWS。

[操作手册](操作手册.md)、[当前业务规则](当前业务规则.md)、[交付清单](交付清单.md)仅保留旧链接跳转，不单独维护规则。代码/配置变化同步SPEC及相关模板，实施和测试写TASKS，问题解决后关闭REVIEWS条目；不要在多个文档复制同一套业务说明。

生产/开发目录边界、Junction风险和发布顺序见[SPEC第20.1节](SPEC.md#201-生产目录与开发目录隔离)；不要把`D:\projects\amazon_daily`当作独立开发目录。
