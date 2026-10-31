"""Model-free v4 queue surface, shaped by the recorded CLI 0.17.0 probes."""
import json


class InquiryQueue:
    def __init__(self, send, session_id, on_guide=None, on_command=None, *, lying_delete=False):
        self.send, self.session_id = send, session_id
        self.on_guide, self.on_command = on_guide, on_command
        self.items = []
        self.auto_drain = True
        self.revision = self.serial = 0
        self.lying_delete = lying_delete

    def drain(self, command_id):
        self.items = [item for item in self.items if item["sourceCommandId"] != command_id]
        self.revision += 1

    def handle(self, method, params, request_id):
        if method == "v4/conversation/subscribe":
            self.serial += 1
            sub = "sub-fixture-" + str(self.serial)
            frame = {"topic": params["topic"], "subscriptionId": sub, "payload": {"kind": "snapshot", "snapshot": {
                "sessionId": self.session_id(), "revision": self.revision, "logEpoch": "fixture-epoch",
                "queue": {"items": self.items, "autoDrain": self.auto_drain}}}}
            self.send({"id": request_id, "result": {"ack": {"subscriptionId": sub, "mode": "snapshot", "logEpoch": "fixture-epoch"}}})
            self.send({"method": "v4/conversation/frame", "params": {"wireVersion": 3, "kind": "complete",
                "deliveryKind": "initial", "logicalFrameId": sub + "-lf-1", "logicalFrameOrdinal": 1,
                "topic": params["topic"], "subscriptionId": sub, "frame": frame}})
        elif method == "v4/conversation/unsubscribe":
            self.send({"id": request_id, "result": {}})
        elif method == "v4/command":
            if self.on_command:
                self.on_command(params)
            kind = params["type"]
            result = {"commandId": params["commandId"], "status": "accepted", "revisionAtDecision": self.revision}
            if kind in ("setAutoDrain", "deleteQueueItem"):
                assert params["baseRevision"] == self.revision
                assert params["baseLogEpoch"] == "fixture-epoch"
            if kind == "setAutoDrain":
                self.auto_drain = params["payload"]["autoDrain"]
                self.revision += 1
            elif kind == "deleteQueueItem":
                if not self.lying_delete:
                    self.items = [v for v in self.items if v["queueItemId"] != params["payload"]["queueItemId"]]
                self.revision += 1
            elif kind == "sendText":
                assert self.auto_drain is False
                command_id = params["commandId"]
                item = {"queueItemId": "queue_" + command_id, "sourceCommandId": command_id,
                        "text": params["payload"]["text"]}
                self.items.append(item)
                self.revision += 1
                if self.on_guide:
                    self.on_guide(params, item)
                result["result"] = {"type": "inputAccepted", "delivery": "queue", "inputId": command_id}
            else:
                raise AssertionError("unexpected v4 command: " + kind)
            self.send({"id": request_id, "result": result})
        else:
            return False
        return True
