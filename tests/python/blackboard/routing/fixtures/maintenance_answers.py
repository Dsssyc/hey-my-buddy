"""Model-free maintenance staging and commit over frozen pages."""
from hey_my_buddy.blackboard.routing.maintenance_data import commit_staged, stage_answer
from hey_my_buddy.protocol.snapshot_query import SnapshotQuery


EMPTY_STAGE = {"tree": [], "classifications": {}, "summaries": {}}


def stage_and_commit(db, *, packet, answer, maintenance_run_id):
    snapshot = SnapshotQuery(packet, plane="maintenance")
    events = []
    for item in packet["microTasks"]:
        args = {"runIds": [item["runId"]]}
        while True:
            page, evidence = snapshot.call("query_micro_tasks", args)
            events.append(evidence)
            if page["nextCursor"] is None:
                break
            args["cursor"] = page["nextCursor"]
    staged, feedback = stage_answer(db, packet=packet, answer=answer, previous=EMPTY_STAGE)
    written, errors = commit_staged(db, packet=packet, staged=staged, verified_events=events,
                                    maintenance_run_id=maintenance_run_id)
    return {"errors": list(dict.fromkeys(feedback["errors"] + errors)), "written": written}
