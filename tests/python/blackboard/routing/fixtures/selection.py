"""Read the same digest-bound routing data the execution receives."""
from hey_my_buddy.json_codec import canonical_json
from hey_my_buddy.private_files import publish_snapshot, read_snapshot
from hey_my_buddy.blackboard.store.db import sha256_text
from hey_my_buddy.protocol import router_selection


def frozen_input(board, head):
    reference = head["snapshot"]
    source = read_snapshot(board.directory, "routing", reference["owner"], reference["reference"],
                           round=reference["round"])
    return {**source, "buddy": head["buddy"], "routerBuddy": head["routerBuddy"],
            "routerBuddyId": head["routerBuddyId"], "routerIndex": head["routerIndex"],
            **({"executionWorkspaceForm": head["executionWorkspaceForm"]} if "executionWorkspaceForm" in head else {})}


def assert_frozen_dispatch(test, board, head, expected):
    test.assertEqual(frozen_input(board, head), expected)
    test.assertEqual(head["inputSha256"], sha256_text(canonical_json(expected)))


def validate(raw, buddy_ids, mode="review", *, domain_node_ids=()):
    return router_selection.validate_answer(raw, {
        "routingMode": mode, "buddies": [{"buddyId": identifier} for identifier in buddy_ids],
        "domainNodes": [{"nodeId": identifier, "parentId": None} for identifier in domain_node_ids],
    })



def fast_document(state, owner, buddy, candidates, *, task="Select", wait=60):
    from hey_my_buddy.blackboard.routing.router_sequence import execution_document

    base = {"protocolVersion": router_selection.VERSION, "routingMode": "fast", "task": task,
            "requestId": owner, "project": None, "expectedSeconds": 60, "requiredCapabilities": [],
            "modelResponseWaitSeconds": wait, "buddies": candidates, "domainNodes": [], "preferences": [],
            "policyFacts": {"hardConstraints": {}}}
    source, _ = publish_snapshot(state, "routing", owner, base, round=1)
    document = {**base, "buddy": buddy, "routerBuddy": buddy, "routerBuddyId": ":".join(buddy.values()),
                "routerIndex": 0}
    return execution_document({"document": document, "decisionId": owner, "round": 1}, source,
                              sha256_text(canonical_json(document)))
