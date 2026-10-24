"""Execution facts cross C-Two through one bound private receipt reference."""
import json
from unittest import mock

from admission import admit_execution
from support import BoardTestCase
from hey_my_buddy.errors import BoardError
from hey_my_buddy.protocol.client import BoardClient
from hey_my_buddy.protocol.execution import RECEIPT, execution_directory, fsync_json, receipt_reference
from hey_my_buddy.protocol.transport import MAX_MESSAGE_BYTES


class ResultReferenceTests(BoardTestCase):
    def setUp(self):
        super().setUp()
        self.board_fixture = self.board()
        task = self.board_fixture.submit_execution(requestId="receipt-ref", task="collect output facts",
                                                  cwd=str(self.workdir()), adapter="command", argv=["true"])["task"]
        self.nonce = "n" * 32
        self.claim = admit_execution(self.board_fixture.client(), self.nonce, task_id=task["runId"])["claim"]
        self.attempt = self.claim["attempt"]
        self.receipt_dir = execution_directory(self.claim, self.board_fixture.directory)
        self.receipt_dir.mkdir(parents=True, mode=0o700)
        self.client = BoardClient(self.board_fixture.directory, call=self.board_fixture.call)

    def publish(self, report):
        receipt = {"taskId": self.claim["task"]["taskId"], "attemptId": self.attempt["attemptId"],
                   "generation": self.attempt["generation"], "report": report, "createdAt": "2026-10-19T00:00:00Z"}
        fsync_json(self.receipt_dir / RECEIPT, receipt)
        return receipt_reference(self.receipt_dir)

    def submit(self, reference, *, nonce=None):
        return self.client.submit_result(self.attempt["attemptId"], self.attempt["generation"],
                                         self.nonce if nonce is None else nonce, reference)

    def test_large_receipt_is_committed_and_replayed_with_a_small_wire_frame(self):
        paths = [f"src/{index:04d}-" + "a" * 2990 for index in range(3000)]
        report = {"status": "ok", "shutdownConfirmed": True, "result": {
            "workspaceSeal": {"changedPaths": paths, "snapshot": {"changedPaths": paths},
                              "diffPath": "/private/output.patch"}}}
        reference = self.publish(report)
        self.assertGreater((self.receipt_dir / RECEIPT).stat().st_size, MAX_MESSAGE_BYTES)
        with mock.patch.object(self.board_fixture.service, "execution_result",
                               wraps=self.board_fixture.service.execution_result) as called:
            first = self.submit(reference)
            replay = self.submit(reference)
        self.assertFalse(first["duplicate"])
        self.assertTrue(replay["duplicate"])
        for call in called.call_args_list:
            self.assertLess(len(call.args[0].encode()), 1024)
            wire = json.loads(call.args[0])
            self.assertEqual(set(wire) - {"token"}, {"attemptId", "generation", "nonce", "receipt"})
            self.assertEqual(wire["receipt"], reference)
        with self.board_fixture.store.db.read() as connection:
            raw = connection.execute("SELECT result_json FROM attempts WHERE attempt_id=?",
                                     (self.attempt["attemptId"],)).fetchone()[0]
        self.assertEqual(json.loads(raw)["result"], report["result"])
        self.assertEqual(first["attempt"]["result"]["result"]["workspaceSeal"]["changedPathsCount"], len(paths))
        self.assertLess(len(json.dumps(first).encode()), MAX_MESSAGE_BYTES)

    def test_wrong_nonce_is_rejected_before_the_file_is_opened(self):
        reference = self.publish({"status": "ok", "shutdownConfirmed": True})
        with mock.patch("hey_my_buddy.protocol.execution.read_execution_receipt") as reader:
            with self.assertRaises(BoardError) as caught:
                self.submit(reference, nonce="wrong")
        self.assertEqual(caught.exception.code, "UNAUTHORIZED")
        reader.assert_not_called()

    def test_digest_and_envelope_binding_must_match(self):
        reference = self.publish({"status": "ok", "shutdownConfirmed": True})
        with (self.receipt_dir / RECEIPT).open("ab") as stream:
            stream.write(b"\n")
        with self.assertRaises(BoardError) as caught:
            self.submit(reference)
        self.assertEqual(caught.exception.code, "RECEIPT_MISMATCH")
        receipt = json.loads((self.receipt_dir / RECEIPT).read_bytes())
        receipt["generation"] += 1
        fsync_json(self.receipt_dir / RECEIPT, receipt)
        with self.assertRaises(BoardError) as caught:
            self.submit(receipt_reference(self.receipt_dir))
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")

    def test_foreign_and_linked_files_are_rejected(self):
        reference = self.publish({"status": "ok", "shutdownConfirmed": True})
        outside = self.receipt_dir.parent / "foreign.json"
        outside.write_bytes((self.receipt_dir / RECEIPT).read_bytes())
        with self.assertRaises(BoardError) as caught:
            self.submit({**reference, "path": str(outside)})
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
        (self.receipt_dir / RECEIPT).unlink()
        try:
            (self.receipt_dir / RECEIPT).symlink_to(outside)
        except OSError as error:
            self.skipTest(str(error))
        with self.assertRaises(BoardError):
            self.submit(reference)

    def test_inline_result_is_not_a_wire_format(self):
        from hey_my_buddy.blackboard.service.service import call_operation
        with self.assertRaises(BoardError) as caught:
            call_operation(self.board_fixture.service, "execution_result", {
                "attemptId": self.attempt["attemptId"], "generation": self.attempt["generation"],
                "nonce": self.nonce, "status": "ok", "shutdownConfirmed": True})
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
