/** The frame around every page: top bar, connection state and the startup screen. */
export default {
  skipToContent: "跳至主要内容",
  nav: {
    label: "主要导航",
    tasks: "任务",
    buddies: "Buddy",
    settings: "设置",
    pendingHost: "等待 Host 决定的微任务",
    unsaved: "有未保存的修改",
  },
  gate: {
    draining: "写入排队中",
    exclusive: "独占编辑中",
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
    waiting: "等待其他保存完成…",
    retry: "重试同一保存",
    discard: "放弃",
    cancelWait: "取消等待",
    waitingRefusal: "正在等待其他保存完成；可先取消等待。",
    unresolvedNote: "保存结果未知，可能已生效；请核对后重试。",
    unavailable: {
      connection: "连接中断；请重试连接，草稿已保留。",
      login: "登录已失效；请运行 buddy console 重新登录，草稿已保留。",
      gate: "无写入资格；保存不可用。",
    },
    conflict: "设置已更新；请核对草稿。",
    reload: "重新加载最新版本",
    discardChanges: "放弃修改",
  },
  startup: {
    connecting: "正在连接本地黑板",
    waiting: "正在连接…",
    failed: "暂时无法连接黑板",
    reconnect: "重新连接",
  },
};
