export default {
  title: "设置", theme: "主题", language: "语言", light: "浅色", dark: "深色", system: "跟随系统", browser: "跟随浏览器", chinese: "中文", english: "English",
  access: {
    title: "访问", allowAuthorized: "只允许已授权的浏览器",
    description: "开启后，新的浏览器要用 hey-my-buddy console 给出的链接授权，才能打开控制台。这台电脑有别的账户，或者端口被转发给别的机器时开启。",
    browsers: "已授权的浏览器", current: "当前", lastSeen: "最近使用 {{time}}", revoke: "撤销", revokeBrowser: "撤销浏览器 {{id}}", revokeCurrent: "撤销当前浏览器",
    confirmTitle: "撤销当前浏览器？", confirmBody: "撤销后，这个浏览器无法继续操作控制台。运行 hey-my-buddy console，使用给出的链接重新授权。", cancel: "取消", confirm: "确认撤销",
    enabled: "已开启浏览器授权，当前浏览器已授权。", disabled: "已关闭浏览器授权，原有授权均已撤销。", revoked: "已撤销该浏览器的授权。", loggedOut: "已撤销当前浏览器；运行 hey-my-buddy console 重新授权。",
    unavailable: "访问设置暂不可用，请刷新控制台。", disconnected: "连接中断或当前浏览器无权修改。", busy: "正在保存访问设置。", loggedOutReason: "当前浏览器的授权已撤销，请重新授权。",
  },
  reminders: {
    title: "提醒", possiblyStuck: "可能卡住", minutesWithoutProgress: "分钟没有进展", silenceMinutes: "静默提醒阈值（分钟）",
    description: "一次执行这么久没有任何进展，时间轴与微任务里标为可能卡住。", range: "请输入 1 至 1440 的整数分钟。", currentSeconds: "当前保存值为 {{n}} 秒；请输入整数分钟才能修改。",
    save: "保存", saving: "保存中", saved: "提醒设置已保存。", unavailable: "提醒设置暂不可用，请刷新控制台。", disconnected: "连接中断或当前浏览器无权修改。", unchanged: "没有需要保存的改动。", busy: "正在保存提醒设置。", unsaved: "未保存",
  },
  version: {
    title: "版本", software: "软件版本", installedAt: "安装时间", details: "详细信息", contract: "契约版本", schema: "schema", sourceCommit: "来源提交", unrecorded: "未记录",
    runningSource: "正在运行（源码）", installedRuntime: "已安装运行时", loading: "正在读取版本…", unread: "尚未读取版本", failed: "无法读取版本：{{error}}",
  },
};
