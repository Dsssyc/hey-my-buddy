"""Model-free maintenance staging and commit over frozen pages."""
from hey_my_buddy.blackboard.routing.maintenance_data import commit_staged, stage_answer
from hey_my_buddy.protocol.snapshot_query import SnapshotQuery


EMPTY_STAGE = {"tree": [], "classifications": {}, "summaries": {}}


def coverage_events(packet, queries=None):
    snapshot = SnapshotQuery(packet, plane="maintenance")
    if queries is None:
        queries = [{"runIds": [item["runId"]]} for item in packet["microTasks"]]
    events = []
    for args in queries:
        while True:
            page, evidence = snapshot.call("query_micro_tasks", args)
            events.append(evidence)
            if page["nextCursor"] is None:
                break
            args = {**args, "cursor": page["nextCursor"]}
    return events


def stage_and_commit(db, *, packet, answer, maintenance_run_id):
    events = coverage_events(packet)
    staged, _ = stage_answer(db, packet=packet, answer=answer, previous=EMPTY_STAGE, issued_events=events)
    written, errors = commit_staged(db, packet=packet, answer=answer, staged=staged, verified_events=events,
                                    maintenance_run_id=maintenance_run_id)
    return {"errors": errors, "written": written}
