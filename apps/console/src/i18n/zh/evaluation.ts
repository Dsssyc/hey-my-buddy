export default {
  noEvidence: "暂无评价数据。", summary: "归纳", noSummary: "尚无归纳。", samples: "样本", firstPasses: "一次通过", reworkedSuccesses: "返工后成功", reworkRounds: "返工轮数", failures: "失败",
  medians: "中位数", medianDeliveryShort: "交付", medianInputShort: "输入", medianCachedShort: "缓存", medianOutputShort: "输出", noMedian: "暂无记录", tokens: "{{n}} tokens",
  domainTable: "领域与任务类型", domain: "领域", noDomains: "暂无领域节点。", noSample: "无样本",
  taskTypes: { create: "新建", change: "改造", debug: "排错", verify: "验证", review: "审查", research: "调研", write: "撰写" },
  cell: "{{samples}} 个样本 · 一次通过 {{rate}} · 返工 {{rework}} 轮",
  previousSamples: "之前的样本", since: "从 {{date}} 起改指当前模型", names: "名称：", modelActions: "模型操作", chooseName: "选择名称", targetModel: "目标模型", chooseModel: "选择模型", cancel: "取消", continue: "继续", confirm: "确认构建",
  explain: {
    generation: "此后 {{name}} 的新结果计入新一代；此前的样本保留为次级证据。显示名称与目录不变。确认后记入草稿，保存后生效。",
    map: "将 {{name}} 归入所选模型 {{target}}，此后的新结果计入该模型；此前的样本保留为次级证据。显示名称与目录不变。确认后记入草稿，保存后生效。",
    undo: "撤回 {{name}} 最近一次由画像或用户修改的对应关系，恢复此前的对应。已累计的样本、显示名称与目录均不变。确认后记入草稿，保存后生效。",
  },
  profileCount: "已建画像 {{built}} / {{total}}", batchBuild: "批量构建", pendingMaintenance: "{{n}} 个微任务待维护", noMaintenanceBuddy: "请先在 Router 的维护里选择 buddy。", search: "搜索模型或推理强度", showAll: "全部", list: "评价列表", noMatches: "没有符合条件的 buddy。", providers: "{{names}} 共用", detailTabs: "评价详情", domains: "领域评价", buildFailedShort: "画像构建失败", confirmBuild: "确认构建画像", buildCount: "为以上每个模型各构建一次，共 {{n}} 次。", viewRun: "查看运行记录",
};
