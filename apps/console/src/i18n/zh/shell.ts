/** The frame around every page: top bar, connection state and the startup screen. */
export default {
  actor: {
    currentOwner: "当前管理方",
    unknownOrigin: "未记录发起者", microTask: "微任务 {{id}}",
    events: {
      dispatch: "{{time}} {{role}} 派发「{{title}}」", approved: "{{time}} {{role}} 批准了「{{title}}」的请求",
      declined: "{{time}} {{role}} 拒绝了「{{title}}」的请求", decide: "{{time}} {{role}} 批准 / 拒绝了「{{title}}」的请求",
      continue: "{{time}} {{role}} 让「{{title}}」继续", integrate: "{{time}} {{role}} 整合了「{{title}}」的产出",
      accept: "{{time}} {{role}} 验收通过「{{title}}」", reject: "{{time}} {{role}} 对「{{title}}」提出验收问题",
      stop: "{{time}} {{role}} 停止了「{{title}}」", console: "{{time}} 在控制台停止了「{{title}}」",
      service: "{{time}} 服务停止了「{{title}}」", unknown: "{{time}} 停止了「{{title}}」（发起者未知）",
      takeover: "{{time}} {{role}} 接管了「{{title}}」", other: "{{time}} {{role}} {{action}}「{{title}}」",
    },
    unrecorded: "未记录", identity: "{{role}} · {{id}}", recordedBy: "记录来源",
    markerIdentity: "{{sentence}}，记录来源 {{actor}}", origin: "发起 {{role}}", reason: "停止理由",
    roles: { host: "Host", lead: "Lead", console: "控制台", service: "服务", workflow: "黑板", bridge: "桥接", worker: "Worker", agent: "Agent" },
    stop: { host: "Host 停止", lead: "Lead 停止", console: "在控制台停止", service: "服务停止", unknown: "停止（发起者未知）" },
  },
  skipToContent: "跳至主要内容",
  nav: {
    label: "主要导航",
    tasks: "任务",
    buddies: "Buddy",
    settings: "设置",
    pendingHost: "等待 Host 决定的微任务",
    unsaved: "有未保存的修改",
  },
  snapshot: {
    verified: "核对时间",
    unverified: "核对时间未记录",
  },
  connection: {
    lost: "连接中断",
    retry: "重试连接",
  },
  /** The state of the page's draft: what waits to be saved, and what a save ran into. */
  draft: {
    label: "未保存的修改",
    unsaved: "{{n}} 项未保存",
    unresolved: "保存结果待核对",
    save: "保存",
    saving: "正在保存…",
    retry: "重试同一保存",
    discard: "放弃",
    unresolvedNote: "保存结果未知，可能已生效；请核对后重试。",
    unavailable: {
      connection: "连接中断；请重试连接，草稿已保留。",
      login: "登录已失效；请运行 hey-my-buddy console 重新登录，草稿已保留。",
    },
    reload: "重新读取设置",
  },
  startup: {
    connecting: "正在连接本地黑板",
    waiting: "正在连接…",
    failed: "暂时无法连接黑板",
    reconnect: "重新连接",
  },
};
