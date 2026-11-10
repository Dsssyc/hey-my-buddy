import routerErrors from "./router-errors";
import routerWork from "./router-work";
import domainTree from "./domain-tree";
import routerProfiles from "./router-profiles";
import modelProfile from "./model-profile";
import harness from "./buddies-harness";
import roles from "./buddies-roles";
import evaluation from "./evaluation";
import maintenance from "./maintenance";

/** The Buddy page: the catalogue of buddies and the roles they hold. */
export default {
  routerWork, domainTree, routerProfiles, routerErrors,
  maintenance,
  modelProfile,
  profile: {
    choose: "选择 buddy",
    search: "搜索 buddy",
    none: "未指定",
    noCandidates: "没有已启用、可用且具备强制只联网档位的 buddy",
    dataFlow: "构建画像时，模型名称与对应关系候选会发给画像 buddy 的模型提供方；画像 buddy 可以联网查找公开资料。",
  },
  sections: {
    evaluation: "评价",
    label: "Buddy 的分区",
    catalog: "目录",
    router: "Router",
    summary: "{{n}} 个 harness · 启用 {{enabled}} / {{total}}",
  },
  attention: {
    upgrade: "Router 设置需要升级",
    unset: "未指定 Router",
    unusable: "Router 不可用",
    failing: "路由连续失败",
  },
  /** One clear choice and three buddy preference modes. */
  preference: {
    unset: "不设",
    prefer: "优先",
    pin: "固定",
    exclude: "排除",
  },
  unsaved: "未保存",
  loginExpired: "浏览器授权已失效，操作未提交；请重新授权。",
  models: {
    search: "搜索 buddy",
    enabledOnly: "仅显示已启用",
    showUnavailable: "显示不可用",
    enabled: "启用",
    discover: "发现模型",
    discoverBusy: "发现进行中",
    columns: {
      buddy: "Buddy",
      effort: "推理强度",
      concurrency: "并发",
    },
    fold: "收起 {{name}}",
    unfold: "展开 {{name}}",
    modelCount: "{{n}} 个模型",
    harnessState: {
      missing: "未找到",
      "login-required": "未登录",
      unhealthy: "未通过",
      unknown: "未检测",
      unavailable: "不可用",
    },
    unavailable: "不可用",
    pending: "待确认",
    pendingHintNoTime: "最近一次读取未见此模型，复查前仍可使用；首次缺席满一小时后复查，仍缺席才记为不可用。无需操作。",
    pendingHint: "自 {{time}} 起读取未见此模型，复查前仍可使用；首次缺席满一小时后复查，仍缺席才记为不可用。无需操作。",
    noProfile: "已启用；尚未构建画像。",
    fresh: "新",
    occupancyUnknown: "占用未知",
    occupancy: "占用 {{active}} / {{limit}}",
    reading: "正在读取不可用的 buddy",
    retry: "重试",
    limit: "只显示前 {{n}} 个不可用的 buddy；更早的请用搜索查找。",
    discovered: "新增 {{n}} 个 buddy，默认不启用",
    discoveredNone: "没有新的 buddy",
    refused: "连接中断或没有写入资格，暂时不能发现模型；草稿仍保留。",
    empty: {
      none: "暂无模型",
      noneHint: "使用“发现模型”查找本机 harness 可用的模型。",
      filtered: "没有匹配的模型",
      filteredHint: "更换搜索词或清除筛选。",
    },
  },
  pane: {
    preference: "偏好",
    noteNone: "选择偏好后可填写理由",
    enable: "启用 {{effort}}",
    buddyPreference: "{{effort}} 的偏好",
    buddyNote: "{{effort}} 的偏好理由",
    model: "模型服务",
    status: "状态",
    firstMissing: "首次缺席",
    samples: "计入评价的次数",
    infrastructureFailures: "基础设施失败",
    available: "可用",
    unavailable: "不可用",
    pending: "待确认",
    concurrency: "并发",
    concurrencyLimit: "并发上限",
    occupancy: "占用 {{n}}",
    range: "需要正整数",
    note: "理由",
    locked: {
      session: "浏览器授权已失效，重新授权后可以修改",
      saving: "正在保存或等待核对，暂时不能修改",
      retired: "已不可用，不能启用",
      pinBuddy: "该 buddy 不可用或未启用，不能固定",
    },
  },
  /** The two other views keep their texts in files of their own. */
  harness,
  roles,
  evaluation,
};
