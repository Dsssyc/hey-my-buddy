import type zh from "../zh/settings";

export default {
  title: "Settings", theme: "Theme", language: "Language", light: "Light", dark: "Dark", system: "System", browser: "Browser", chinese: "中文", english: "English",
  dataFlow: { title: "Session data", detail: "Sessions may contain sensitive command input and output. They are shown only in the local console, excluded from backups and exports, and deleted with their macro task." },
  access: {
    title: "Access", allowAuthorized: "Allow only authorized browsers",
    description: "When enabled, new browsers need the link from hey-my-buddy console to access the console. Enable this when the computer has other accounts or the port is forwarded to another machine.",
    browsers: "Authorized browsers", current: "Current", lastSeen: "Last used {{time}}", revoke: "Revoke", revokeBrowser: "Revoke browser {{id}}", revokeCurrent: "Revoke current browser",
    confirmTitle: "Revoke this browser?", confirmBody: "This browser will no longer be able to operate the console. Run hey-my-buddy console and use its link to authorize it again.", cancel: "Cancel", confirm: "Confirm revocation",
    enabled: "Browser authorization enabled. This browser is authorized.", disabled: "Browser authorization disabled. All previous authorizations were revoked.", revoked: "Browser authorization revoked.", loggedOut: "This browser was revoked. Run hey-my-buddy console to authorize it again.",
    unavailable: "Access settings are unavailable. Refresh the console.", disconnected: "The connection is interrupted or this browser cannot make changes.", busy: "Saving access settings.", loggedOutReason: "This browser's authorization was revoked. Authorize it again.",
  },
  reminders: {
    title: "Reminders", possiblyStuck: "Possibly stuck", minutesWithoutProgress: "minutes without progress", silenceMinutes: "Silence reminder threshold (minutes)",
    description: "An execution without progress for this long is marked as possibly stuck.", range: "Enter a whole number of minutes from 1 to 1440.", currentDuration: "The saved value is {{duration}}. Enter whole minutes to change it.",
    save: "Save", saving: "Saving", saved: "Reminder settings saved.", unavailable: "Reminder settings are unavailable. Refresh the console.", disconnected: "The connection is interrupted or this browser cannot make changes.", unchanged: "No changes to save.", busy: "Saving reminder settings.", unsaved: "Unsaved",
  },
  version: {
    title: "Version", software: "Software version", installedAt: "Installed", details: "Details", contract: "Contract version", schema: "Schema", sourceCommit: "Source commit", unrecorded: "Not recorded",
    runningSource: "Running (source)", installedRuntime: "Installed runtime", loading: "Reading version…", unread: "Version not read yet", failed: "Unable to read version: {{error}}",
  },
} satisfies typeof zh;
