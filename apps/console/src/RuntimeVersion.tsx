import { useEffect, useRef, useState } from "react";
import { errorText, type ConsoleApi } from "./api";
import type { RuntimeVersionInfo, VersionFacts } from "./types";

function Facts({ value }: { value: VersionFacts }) {
  return <dl className="facts">
    <dt>软件版本</dt><dd>{value.softwareVersion ?? "未记录"}</dd>
    <dt>契约版本</dt><dd>{value.contractVersion ?? "未记录"}</dd>
    <dt>schema</dt><dd>{value.schemaVersion ?? "未记录"}</dd>
    <dt>来源提交</dt><dd className="mono wrap">{value.sourceCommit ?? "未记录"}</dd>
    <dt>安装时间</dt><dd>{value.installedAt ?? "未记录"}</dd>
  </dl>;
}

/** One read on the first visible settings visit; snapshot updates never rescan. */
export function RuntimeVersion({ api, active }: { api: ConsoleApi; active: boolean }) {
  const [info, setInfo] = useState<RuntimeVersionInfo | null>(null);
  const [error, setError] = useState("");
  const pending = useRef<Promise<RuntimeVersionInfo> | null>(null);
  useEffect(() => {
    if (!active) return;
    let current = true;
    pending.current ??= api.runtimeVersion();
    pending.current.then(value => { if (current) setInfo(value); })
      .catch(reason => { if (current) setError(errorText(reason)); });
    return () => { current = false; };
  }, [api, active]);
  return <section className="panel settings-panel" aria-labelledby="runtime-version">
    <div className="panel-heading"><h2 id="runtime-version">版本</h2></div>
    {error ? <p role="alert" className="error-message">无法读取版本：{error}</p>
      : info ? <>
        <h3>正在运行{info.running.mode === "source" ? "（源码）" : "（已安装运行时）"}</h3>
        <Facts value={info.running} />
        {info.running.mode === "source" && <>
          <h3>已安装运行时</h3>
          {info.installed ? <Facts value={info.installed} /> : <p className="muted">未记录</p>}
        </>}
      </> : <p className="muted" role="status">{active ? "正在读取版本…" : "尚未读取版本"}</p>}
  </section>;
}
