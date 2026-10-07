"""Actual service registry calls through a holding Worker to its controller.

Only registration delivery uses the peer's recording BoardClient. The service
operation, actor/turn checks, channel factory and two live C-Two hops are real.
The controller commits a fsynced fake native journal, without a model.
"""
from blackboard.store.test_store import StoreConcurrencyTestCase
from buddy.runtime.test_live import peer, request, claim, socket_path
from hey_my_buddy.blackboard.service.live_registry import LiveRegistry
from hey_my_buddy.buddy.harnesses.run_contract import RunIdentity
from hey_my_buddy.buddy.harnesses.c_two_live import LiveEndpointDescriptor
from hey_my_buddy.protocol.worker_live import WorkerLiveAttach
from hey_my_buddy.protocol.worker_live import WorkerLiveActor, WorkerLiveDetach


class ServiceHolderIntegrationTests(StoreConcurrencyTestCase):
    def test_real_actor_service_worker_controller_restart_and_withdrawal(self):
        board = self.board()
        client = board.client()
        client.register_worker("worker-test", adapter="dsh", capabilities=["dsh"])
        run_id = self.submit_governed(board, "live-service-holder")
        granted = client.claim("worker-test", "holder-claim", "n"*32,
                               worker_instance="worker-process-test")["claim"]
        turn, attempt = granted["turn"], granted["attempt"]
        identity = RunIdentity(task_id=run_id, attempt_id=attempt["attemptId"],
            generation=attempt["generation"], invocation_id="held-controller-invocation",
            turn_id=turn["turnId"], input_sha256=turn["inputSha256"])
        with peer() as holding:
            descriptor = LiveEndpointDescriptor.from_payload(holding.call(op="spawn", label="one",
                identity=identity.to_payload(), journal=str(holding.root / "one-journal.jsonl"))["descriptor"])
            holding.controllers.append(descriptor)
            attachment = WorkerLiveAttach.from_payload(holding.call(op="bind", label="one",
                claim=granted, nonce="n"*32)["attachment"])
            self.assertTrue(board.call("worker_live_attach", attachment.to_payload())["attached"])
            view = board.store.task_get({"runId": run_id})["task"]
            registry = board.store.live_registry
            channel = registry.channel_for(view)
            self.assertEqual(channel.identity, identity)
            self.assertEqual(channel.request(request(identity), timeout_ms=2000).status, "queued")
            self.assertTrue((holding.root / "one-journal.jsonl").read_text())
            self.assertEqual(channel.observe(limit=10, timeout_ms=1000).inquiries[0].status, "queued")

            board.store.reconcile_startup()
            fresh = LiveRegistry(board.store, registry._channel_factory)
            board.store.live_registry = fresh
            self.assertIsNone(fresh.channel_for(board.store.task_get({"runId": run_id})["task"]))
            client.reconcile("worker-test", attempt["attemptId"], attempt["generation"],
                             "n"*32, worker_instance="worker-process-test")
            rebound = holding.call(op="refresh", label="one", claim=claim(identity), nonce="n"*32)
            self.assertTrue(rebound["bound"])
            self.assertEqual(rebound["attachment"], attachment.to_payload())
            board.call("worker_live_attach", rebound["attachment"])
            resumed = fresh.channel_for(board.store.task_get({"runId": run_id})["task"])
            self.assertEqual(resumed.request(request(identity, request_id="after-restart",
                question_id="after-restart"), timeout_ms=2000).status, "queued")
            holding.call(op="unbind", claim=claim(identity))
            detached = WorkerLiveDetach(
                **{field: getattr(attachment, field) for field in WorkerLiveActor.model_fields},
                identity=identity, instance_id=attachment.instance_id)
            self.assertTrue(board.call("worker_live_detach", detached.to_payload())["detached"])
            self.assertIsNone(fresh.channel_for(board.store.task_get({"runId": run_id})["task"]))
            descriptors = [holding.descriptor, *holding.controllers]
            self.assertTrue(all(socket_path(d).is_socket() for d in descriptors))
        self.assertTrue(all(not socket_path(d).exists() for d in descriptors))
