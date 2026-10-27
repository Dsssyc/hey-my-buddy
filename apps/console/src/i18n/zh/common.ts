
/** Text shared by data reads and snapshot labels across pages. */
export default {
  concurrencyNotice: {
    title: "同时运行多个会话之前",
    body: "同时运行的会话越多，同一时间消耗的额度与请求就越多。一些订阅是按个人的日常使用设计的，超出它的条款或建议的并发，可能被限流、暂停，甚至影响账户。调高之前，请先查看你所用 harness 与订阅的条款。",
  },
  error: {
    codes: {
      REVISION_CONFLICT: "记录已更新；请刷新核对后重试。", CONFLICT: "记录冲突；请刷新核对后重试。", FORBIDDEN: "无操作权限；请重新打开控制台。",
      STALE_GENERATION: "操作资格已失效；请刷新核对负责人。", SHUTDOWN_UNCONFIRMED: "停止未确认；请稍后核对再重试。",
      STORAGE_INCOMPLETE: "清理未完成；请重试同一请求。", PLAN_EXPIRED: "计划已过期；请重新检查。",
      CONSOLE_SESSION_EXPIRED: "浏览器授权已失效；请运行 hey-my-buddy console 重新授权。", CONSOLE_ENTRY_EXPIRED: "入口已过期；请重新打开控制台。",
    },
    session: "会话信息无法识别；请检查服务版本或重新授权。", access: "控制台访问设置无法识别，请刷新后重试。", reminders: "提醒设置无法识别，请刷新后重试。",
    storagePlan: "存储计划响应不完整，请检查服务版本。", version: "版本信息无法识别，请检查服务版本。", network: "无法连接本地黑板；请刷新重试。",
    unparsable: "本地服务返回了无法解析的响应。", snapshot: "控制台数据不完整；请检查服务版本。", submit: "提交结果未知；请核对后重试。", macroTasks: "宏任务列表不完整；请检查服务版本。", timeline: "这个宏任务的记录没有读全；请检查服务版本。",
    cleanup: "清理结果未知；请重试同一请求。", recheck: "重新检测结果不完整；请检查服务版本。", pathSaved: "路径保存结果未知；请检查服务版本。",
    refused: "请求未成功。", failed: "请求失败，请稍后重试。",
  },
  snapshot: {
    verified: "核对时间",
    unverified: "核对时间未记录",
  },
};
