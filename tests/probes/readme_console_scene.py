"""Authored synthetic story for README captures; no board or harness is opened."""
from __future__ import annotations

from types import ModuleType


def configure(p: ModuleType, language: str) -> None:
    """Select only this preview process's data, using the standard constructors."""
    def text(en: str, zh: str) -> str:
        return zh if language == "zh" else en

    p.HOST_A = "host-atlas"
    p.COOLING_UNTIL = p.instant(10, 30)
    p.PROJECTS = {
        "alpha": {"id": "synthetic-atlas", "path": "~/projects/atlas", "label": text("Atlas search", "Atlas 搜索")},
        "beta": {"id": "synthetic-orbit", "path": "~/projects/orbit", "label": text("Orbit docs", "Orbit 文档")},
    }
    titles = [text("Ship offline search", "交付离线搜索"), text("Refresh the guide", "更新使用指南"),
              text("Audit export formats", "核对导出格式"), text("Trim the index", "精简搜索索引")]
    groups = (p.MACRO_A, p.MACRO_B, p.MACRO_C, p.MACRO_PALETTE)
    p.MACRO_TASKS = {group: {"title": title, "description": None, "project": "alpha" if i != 1 else "beta",
                           "host": p.HOST_A, "createdAt": p.instant(8 if i == 0 else 6, 0)}
                     for i, (group, title) in enumerate(zip(groups, titles))}
    p.MACRO_TASKS[p.MACRO_A]["description"] = text(
        "A compact index, typo-tolerant ranking and a release ready for review.",
        "精简索引、容错排序，以及等待评审的发布候选。")
    host = p.host_actor(p.HOST_A)
    lead_id = "readme-lead"
    lead_actor = {"role": "lead", "id": lead_id}
    rows = []

    def task(identifier, en, zh, created, *, buddy, state="delivered", status="completed",
             category="review", owner=False, **kwargs):
        entry = p.run(identifier, project="alpha", group=p.MACRO_A, title=text(en, zh), title_source="title",
                      task_text=text(en, zh), created=created, updated=kwargs.pop("updated", created),
                      state=state, status=status, category=category, buddy=buddy,
                      hosts=(None, None) if owner else (p.HOST_A, p.HOST_A),
                      lead_owner=(lead_id, "turn-att-readme-lead-1") if owner else None, **kwargs)
        rows.append(entry)
        return entry

    def execution(entry, start, end, buddy, *, disposition="completed", error=None):
        index = len(entry["attempts"]) + 1
        aid = f"att-{entry['runId']}-{index}"
        entry["attempts"].append(p.attempt(aid, index, "finished" if end else "executing", bool(end), start, end,
            buddy, result=(error or text("Evidence ready for review.", "证据已交回，等待核对。")) if end else None,
            disposition=disposition, error=error, expected_seconds=900))
        entry["spans"].append(p.span(f"execution:{aid}", "execution", start, end,
            "finished" if end else "executing", attempt_id=aid, buddy=buddy, confirmed=bool(end),
            generation=index, disposition=disposition, turn_index=index, error=error))
        entry["updatedAt"] = end or start
        return aid

    def dispatch(entry, actor=host):
        entry["markers"].append(p.marker(entry["createdAt"], "dispatch", actor=actor))

    def accept(entry, when, actor=host):
        latest = entry["attempts"][-1]
        if not entry["artifacts"] or entry["artifacts"][-1]["attemptId"] != latest["attemptId"]:
            entry["artifacts"].append(p.artifact(f"output-{entry['runId']}", latest["attemptId"],
                p.digest(entry["runId"])[:40], f"~/artifacts/{entry['runId']}.diff", [f"changes/{entry['runId']}.json"]))
        artifact = entry["artifacts"][-1]
        integration = p.integration(f"integration-{entry['runId']}", artifact["artifactId"],
                                    latest["attemptId"], actor["id"])
        integration.update(actor=actor, sourceCommit=artifact["outputCommit"])
        integration["verification"]["matchingPaths"] = artifact["changedPaths"]
        entry["integrations"].append(integration)
        entry.update(state="accepted", category="ended", acceptedAt=when, updatedAt=when, resultAvailable=True)
        # Acceptance records verification and acknowledgement in one transaction.
        entry["markers"] += [
            p.marker(when, "integrate", actor=actor, artifact_id=artifact["artifactId"],
                attempt_id=latest["attemptId"], integration_id=integration["integrationId"],
                strategy="cherry-pick", state="verified"),
            p.marker(when, "accept", actor=actor, verdict="accepted", artifact_id=artifact["artifactId"],
                attempt_id=latest["attemptId"], integration_id=integration["integrationId"]),
        ]

    lead = task(lead_id, "Coordinate the rollout", "协调功能交付", p.instant(9, 5), buddy=p.CFG_CODEX,
                state="executing", status="running", category="active", delegation_authority=True,
                shutdown_confirmed=False)
    execution(lead, p.instant(9, 5, 30), None, p.CFG_CODEX)
    lead["expectedSeconds"] = 7200
    lead["attempts"][0]["expectedSeconds"] = 7200
    lead["activity"] = {"phase": "thinking", "observedAt": p.instant(9, 59), "eventSeq": 50,
                        "lastToolActivityAt": p.instant(9, 59), "toolName": None,
                        "counts": {"modelTurns": 8, "toolCalls": 16}}
    dispatch(lead)

    schema = task("readme-schema", "Define the index schema", "定义索引结构", p.instant(9, 6),
                  buddy=p.CFG_FLASH, owner=True)
    dispatch(schema, lead_actor)
    decision = p.routing("readme-route-schema", "readme-router", "completed", p.CFG_FLASH,
                         text("Select a buddy for the index schema.", "为索引结构选择 buddy。"), {},
                         attempt_id="att-readme-router", mode="fast", elapsed_ms=60000, tool_calls=0)
    schema["routing"] = decision
    schema["spans"] += [p.span("routing:att-readme-router", "routing", p.instant(9, 6), p.instant(9, 7),
                              "finished", attempt_id="att-readme-router", confirmed=True,
                              disposition="completed", decision_task="readme-router", routing_record=decision)]
    execution(schema, p.instant(9, 7), p.instant(9, 18), p.CFG_FLASH)
    accept(schema, p.instant(9, 19), lead_actor)

    tokenizer = task("readme-tokenizer", "Handle multilingual tokens", "处理多语言分词", p.instant(9, 7),
                     buddy=p.CFG_CLAUDE, owner=True, continuation_count=1, result=True)
    dispatch(tokenizer, lead_actor)
    tokenizer["spans"].append(p.span("queue:readme-tokenizer", "queue", p.instant(9, 7), p.instant(9, 9), "admitted"))
    first = execution(tokenizer, p.instant(9, 9), p.instant(9, 15), p.CFG_GLM)
    original = p.artifact("readme-tokenizer-v1", first, p.digest("tokenizer-v1")[:40],
                          "~/artifacts/tokenizer-v1.diff", ["src/tokenizer.py"])
    tokenizer["artifacts"].append(original)
    # workflow_continue from a delivered artifact emits review_rejected; the
    # next admission consumes it and preserves the event (workflow.py).
    tokenizer["markers"].append(p.marker(p.instant(9, 16), "reject", actor=lead_actor,
        eventKind="workflow.review_rejected", artifact_id=original["artifactId"], attempt_id=first,
        verdict="rejected", note=text("Include mixed-script words.", "补上混合文字的分词。")))
    second = execution(tokenizer, p.instant(9, 17), p.instant(9, 25), p.CFG_CLAUDE)
    tokenizer["artifacts"].append(p.artifact("readme-tokenizer-v2", second, p.digest("tokenizer-v2")[:40],
        "~/artifacts/tokenizer-v2.diff", ["src/tokenizer.py"]))
    tokenizer["continuations"].append(p.continuation("readme-tokenizer-next", second,
        text("Include mixed-script words and rerun the fixtures.", "补上混合文字并重新核对样例。"),
        request_id=None, created=p.instant(9, 16)))
    tokenizer["markers"].append(p.marker(p.instant(9, 16), "continue", actor=lead_actor,
                                        continuation_id="readme-tokenizer-next"))
    accept(tokenizer, p.instant(9, 27), lead_actor)

    ranking = task("readme-ranking", "Benchmark fuzzy ranking", "评测容错排序", p.instant(8, 12),
                   buddy=p.CFG_GLM, state="concluded", status="concluded", category="ended", result=True)
    dispatch(ranking)
    failure = text("The sample corpus could not be decoded.", "样例语料解码失败。")
    aid = execution(ranking, p.instant(8, 13), p.instant(8, 24), p.CFG_GLM, error=failure)
    ranking["hostConclusion"] = p.host_conclusion("readme-ranking-conclusion", attempt_id=aid,
        execution_status="failed", note=failure, artifact_id=None, actor=host, created_at=p.instant(8, 26), run_revision=3)
    ranking["updatedAt"] = p.instant(8, 26)
    ranking["markers"].append(p.marker(p.instant(8, 26), "conclude", actor=host, attempt_id=aid))

    samples = task("readme-samples", "Validate the sample corpus", "核对样例语料", p.instant(8, 17),
                   buddy=p.CFG_CODEX, result=True, continuation_count=1)
    dispatch(samples)
    execution(samples, p.instant(8, 18), p.instant(8, 22), p.CFG_CODEX, disposition="assistance")
    samples["spans"].append(p.span("host:readme-corpus", "host", p.instant(8, 22), p.instant(8, 30),
        "approved", request_id="readme-corpus", request_kind="assistance",
        summary=text("Confirm the public sample licence.", "确认公开样例的许可。")))
    aid = execution(samples, p.instant(8, 31), p.instant(8, 38), p.CFG_CODEX)
    samples["continuations"].append(p.continuation("readme-corpus-next", aid,
        text("Use the public CC0 corpus.", "使用公开的 CC0 语料。"), request_id="readme-corpus", created=p.instant(8, 30)))
    samples["markers"] += [p.marker(p.instant(8, 30), "decide", actor=host, request_id="readme-corpus", decision="approve"),
                           p.marker(p.instant(8, 30), "continue", actor=host, continuation_id="readme-corpus-next")]
    accept(samples, p.instant(8, 39))

    fix = task("readme-corpus-fix", "Fix corpus decoding", "修正语料解码", p.instant(9, 7),
               buddy=p.CFG_FLASH, result=True)
    dispatch(fix)
    aid = execution(fix, p.instant(9, 8), p.instant(9, 24), p.CFG_FLASH)
    art = p.artifact("readme-corpus-output", aid, p.digest("corpus-output")[:40],
                     "~/artifacts/corpus.diff", ["src/corpus.py"])
    fix["artifacts"].append(art)
    accept(fix, p.instant(9, 25))

    docs = task("readme-guide", "Write the migration guide", "编写迁移指南", p.instant(9, 20),
                buddy=p.CFG_CLAUDE, state="awaiting-host", category="host", result=True)
    dispatch(docs)
    execution(docs, p.instant(9, 21), p.instant(9, 33), p.CFG_CLAUDE, disposition="assistance")
    question = text("Confirm the default index location.", "确认默认的索引位置。")
    docs["activeRequest"] = {**p.ACTIVE_REQUEST, "requestId": "readme-guide-location", "createdAt": p.instant(9, 33),
        "summary": question, "attempted": text("Checked the configuration examples.", "已核对配置示例。"),
        "neededWork": [question], "acceptance": text("Record the default location.", "记录默认位置。")}
    docs["spans"].append(p.span("host:readme-guide-location", "host", p.instant(9, 33), None, "open",
        request_id="readme-guide-location", request_kind="assistance", summary=question))

    live = task("preview-run-a3", "Run the search regression", "运行搜索回归", p.instant(9, 34),
                buddy=p.CFG_CODEX, state="executing", status="running", category="active", shutdown_confirmed=False)
    dispatch(live)
    aid = execution(live, p.instant(9, 35), None, p.CFG_CODEX)
    live["expectedSeconds"] = 600
    live["attempts"][0]["expectedSeconds"] = 600
    live["activity"] = {"phase": "thinking", "observedAt": p.instant(9, 40), "eventSeq": 40,
                        "lastToolActivityAt": p.instant(9, 40), "toolName": None,
                        "counts": {"modelTurns": 4, "toolCalls": 12}}
    live["messages"].append(p.message("readme-regression-inquiry", aid,
        text("Which case is still running?", "还在跑哪一个用例？"), author=p.HOST_A,
        created=p.instant(9, 56), state="delivered", delivered=p.instant(9, 56, 2)))

    queued = task("readme-package", "Package the release candidate", "打包发布候选", p.instant(9, 55),
                  buddy=p.CFG_GLM, state="executing", status="queued", category="active")
    dispatch(queued)
    queued["spans"].append(p.span("queue:readme-package", "queue", p.instant(9, 55), None, "queued"))

    router = p.run("readme-router", project="alpha", title=text("Choose the schema buddy", "选择索引结构 buddy"),
        title_source="title", task_text=decision["note"], created=p.instant(9, 6), updated=p.instant(9, 7),
        state="finished", status="completed", category="ended", buddy=p.ROUTER_BUDDY,
        parent=schema["runId"], group=p.MACRO_A, micro_task=False, adapter="decision", routing=decision,
        router_budget_seconds=60, attempts=[p.attempt("att-readme-router", 1, "finished", True,
            p.instant(9, 6), p.instant(9, 7), p.ROUTER_BUDDY, result=p.decision_result(decision))])
    rows.append(router)
    for group, en, zh, state, status, category, hour in [
        (p.MACRO_B, "Review the quickstart", "评审快速入门", "delivered", "completed", "review", 9),
        (p.MACRO_C, "Check CSV export", "核对 CSV 导出", "accepted", "completed", "ended", 7),
        (p.MACRO_PALETTE, "Measure index size", "测量索引大小", "failed", "failed", "ended", 6),
    ]:
        entry = p.run(f"readme-side-{hour}", project="beta" if group == p.MACRO_B else "alpha", group=group,
            title=text(en, zh), title_source="title", task_text=text(en, zh), created=p.instant(hour, 0),
            updated=p.instant(hour, 20), state=state, status=status, category=category, buddy=p.CFG_FLASH,
            result=True, accepted=p.instant(hour, 21) if state == "accepted" else None)
        execution(entry, p.instant(hour, 1), p.instant(hour, 20), p.CFG_FLASH,
                  error=text("Size budget exceeded.", "超出体积预算。") if state == "failed" else None)
        dispatch(entry)
        if state == "accepted":
            accept(entry, p.instant(hour, 21))
        rows.append(entry)
    p.PREVIEW_FIXES = {"readme-corpus-fix": "readme-ranking"}
    p.FIXTURES = p.index_runs(rows)
    for entry in p.FIXTURES["runs"].values():
        if entry["adapter"] != "decision":
            for item in entry["attempts"]:
                if item["result"] is not None and item["result"]["status"] == "ok" and not any(
                        artifact["attemptId"] == item["attemptId"] for artifact in entry["artifacts"]):
                    output = p.artifact(f"output-{item['attemptId']}", item["attemptId"],
                        p.digest(item["attemptId"])[:40], f"~/artifacts/{item['attemptId']}.diff", [],
                        final=item["disposition"] == "completed")
                    output.update(sourceTaskId=entry["runId"], createdAt=item["finishedAt"])
                    entry["artifacts"].append(output)
            turn_order = {item["attemptId"]: item["turnIndex"] for item in entry["attempts"]}
            entry["artifacts"].sort(key=lambda artifact: turn_order[artifact["attemptId"]])
        entry["cwd"] = f"~/checkouts/{entry['runId']}"
        entry["requestAttempted"] = text("Checked the public sample licence.", "已核对公开样例的许可。")
        entry["requestNeededWork"] = [text("Confirm the CC0 corpus.", "确认 CC0 语料。")]
        entry["requestAcceptance"] = text("Use the approved corpus in the regression.", "在回归中使用已批准的语料。")
        entry["timelineSummary"] = text("Evidence ready for review.", "证据已交回，等待核对。")
        for artifact in entry["artifacts"]:
            artifact["createdAt"] = next(item["finishedAt"] for item in entry["attempts"]
                                         if item["attemptId"] == artifact["attemptId"])
        for integration in entry["integrations"]:
            integration["createdAt"] = next(item["at"] for item in entry["markers"]
                                            if item["integrationId"] == integration["integrationId"])
            integration["note"] = text("Focused checks passed.", "相关检查已通过。")
