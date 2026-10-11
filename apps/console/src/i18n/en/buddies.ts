import type zh from "../zh/buddies";

export default {
  sections: {
    label: "Sections of the Buddies page",
    catalog: "Catalog",
    roles: "Roles",
  },
  catalog: {
    label: "Tables of the catalog",
    models: "Models",
    harness: "Harnesses",
  },
  attention: {
    upgrade: "Router settings need an upgrade",
    unset: "No Router set",
    unusable: "Router unavailable",
    failing: "Routing keeps failing",
    quota: "Quota near its limit: {{names}}",
  },
} satisfies typeof zh;
