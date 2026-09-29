import type { ConsoleApi } from "./api";
import type { ThemeChoice } from "./theme";
import { StoragePanel } from "./StoragePanel";
import { ConsoleAccessSettings } from "./ConsoleAccessSettings";
import type { ConsoleAccess } from "./types";

const THEME_CHOICES: [ThemeChoice, string][] = [["light", "浅色"], ["dark", "深色"], ["system", "跟随系统"]];

/**
 * System-level settings, unrelated to any buddy: the display theme and the
 * local storage check and reclaim panel (moved here unchanged from the old
 * routing page). Language will join them later.
 */
export function Settings({ api, csrfToken, connectionError = "", access, refresh = async () => {}, theme, onTheme }: {
  api: ConsoleApi;
  csrfToken: string;
  connectionError?: string;
  access?: ConsoleAccess;
  refresh?: () => Promise<unknown>;
  theme: ThemeChoice;
  onTheme: (choice: ThemeChoice) => void;
}) {
  return <div className="settings-page">
    <section className="panel settings-panel" aria-labelledby="display-settings">
      <div className="panel-heading">
        <h2 id="display-settings">显示</h2>
        <p className="muted">选择控制台的颜色主题。</p>
      </div>
      <div className="segmented theme-choice" role="radiogroup" aria-label="主题">
        {THEME_CHOICES.map(([value, label]) => <label key={value} className={"segment" + (theme === value ? " checked" : "")}>
          <input type="radio" name="console-theme" value={value} checked={theme === value} onChange={() => onTheme(value)} />
          {label}
        </label>)}
      </div>
    </section>
    <ConsoleAccessSettings api={api} csrfToken={csrfToken} access={access} refresh={refresh} unavailable={!!connectionError} />
    <StoragePanel api={api} csrfToken={csrfToken} connectionError={connectionError} />
  </div>;
}
