"""The checkpoint-only resumable guard of the Worker role collection.

ADR-025 step 5 pinned the native-session resumable conjunction over a complete
imported turn (``test_registered_run_wiring``); the DSH-native-resume plan's
V-C10 pins the same conjunction where there is no complete turn at all — only a
legal native checkpoint. Over the registered ZCode fixture (a real controller
process and a real mock app-server, no model call), one actual run's result is
read back, the run's final value is dropped so the role cannot collect a
complete turn delivery and no turn record is ever imported, and a legal
checkpoint rides in as a real digest-bound evidence file. The session is then
resumable only when every layer is a confirmed fact:
the native stop gone, the outer stop confirmed, the native continuation
capability explicitly true, and the checkpoint itself resumable — and any
layer that is unknown or unconfirmed leaves the session not resumable.
"""
from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
import unittest
from unittest import mock

from buddy.harnesses.zcode.test_zcode import ZcodeFixtureCase
from hey_my_buddy.buddy.harnesses import run_contract as rc
from hey_my_buddy.buddy.harnesses.run_contract import decode_run_result, encode_run_result
from hey_my_buddy.buddy.roles import turn_io
from hey_my_buddy.buddy.roles.turn_io import private_json

CHECKPOINT_STATUSES = ("completed", "incomplete")


class CheckpointOnlyResumableTests(ZcodeFixtureCase):
    """``nativeSession.resumable`` over a legal checkpoint and no complete turn."""

    def _run(self, case="ok"):
        context = self.context(case)
        handle = self.adapter.start(context)
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.2) if handle.group_alive() else None)
        self.assertIsNotNone(handle.wait(context.timeout_seconds + 10),
                             "controller did not exit within its deadline and shutdown grace")
        return context, handle

    def _checkpoint_ref(self, context, result_fields, status):
        fields = {"version": 1,
                  **{key: context.turn_input[key]
                     for key in ("taskId", "attemptId", "generation", "turnId")},
                  "inputSha256": turn_io.input_hash(context.turn_input),
                  "nativeTurnStarted": True, "nativeTurnStatus": status,
                  "bindingSaved": True, "eventSeq": 2,
                  "sessionId": result_fields["nativeIdentity"]["sessionId"],
                  "nativeTurnId": result_fields["nativeIdentity"]["turnId"]}
        path = context.directory / f"native-checkpoint-{status}-{uuid.uuid4().hex}.json"
        private_json(path, fields)
        raw = path.read_bytes()
        return rc.EvidenceRef(kind="native-checkpoint", location=str(path),
                              size_bytes=len(raw),
                              sha256=hashlib.sha256(raw).hexdigest()).to_payload()

    def _checkpoint_only_fields(self, context, handle, *, checkpoint_status="completed"):
        """The actual run result, minus its final value, plus a legal checkpoint.

        Without the run's final value the role cannot collect a complete turn
        delivery, so no turn record is ever written or imported and the
        checkpoint is the only continuation material left — every other fact
        stays exactly what the real run produced.
        """
        path = Path(handle.log_paths["stdout"])
        fields = decode_run_result(path.read_bytes()).to_payload()
        self.assertEqual(fields["end"]["nativeExitCode"], 0,
                         "the actual run must really have exited zero for a resumable checkpoint")
        self.assertEqual(fields["stopEvidence"]["native"]["groupState"], "gone")
        self.assertEqual(fields["continuation"]["resumable"], True)
        fields.pop("value", None)
        refs = list(fields.get("evidenceRefs") or [])
        refs.append(self._checkpoint_ref(context, fields, checkpoint_status))
        fields["evidenceRefs"] = refs
        path.write_text(encode_run_result(decode_run_result(fields)))
        return fields

    def _rewrite(self, handle, fields):
        path = Path(handle.log_paths["stdout"])
        path.write_text(encode_run_result(decode_run_result(fields)))

    def test_a_legal_checkpoint_alone_is_resumable_when_every_layer_confirms(self):
        context, handle = self._run()
        fields = self._checkpoint_only_fields(context, handle)
        outcome = self.adapter.collect(handle, context)
        session = outcome.result["nativeSession"]
        self.assertIs(session["bindingPresent"], True)
        self.assertIs(session["resumable"], True,
                      "a confirmed two-layer stop with a resumable checkpoint and no complete "
                      "turn is still the one resumable fact")
        self.assertEqual(outcome.result["code"], "invalid-role-result",
                         "the run's own delivery stayed incomplete: the checkpoint is the only "
                         "continuation material this witness has")
        self.assertNotIn("turn", outcome.result,
                         "the witness must be the checkpoint path, not an imported turn")
        self.assertIn("turnError", outcome.result)
        self.assertEqual(outcome.result["nativeCheckpoint"]["nativeTurnStatus"], "completed")
        self.assertEqual(outcome.result["nativeCheckpoint"]["sessionId"],
                         fields["nativeIdentity"]["sessionId"])

    def test_an_unknown_or_alive_native_stop_keeps_a_checkpoint_only_session_not_resumable(self):
        context, handle = self._run()
        fields = self._checkpoint_only_fields(context, handle)
        for state in ("unknown", "alive"):
            with self.subTest(nativeGroupState=state):
                moved = dict(fields)
                moved["stopEvidence"] = {**fields["stopEvidence"],
                                         "native": {**fields["stopEvidence"]["native"],
                                                    "groupState": state}}
                self._rewrite(handle, moved)
                refused = self.adapter.collect(handle, context)
                self.assertIs(refused.shutdown_confirmed, False,
                              "a native group that is not gone leaves the stop unconfirmed")
                self.assertIs(refused.result["nativeSession"]["resumable"], False,
                              "an unknown or still-alive native stop is never a resumable session")

    def test_an_unconfirmed_outer_stop_keeps_a_checkpoint_only_session_not_resumable(self):
        context, handle = self._run()
        self._checkpoint_only_fields(context, handle)
        with mock.patch.object(handle, "shutdown_confirmed", return_value=False):
            refused = self.adapter.collect(handle, context)
        self.assertIs(refused.shutdown_confirmed, False)
        self.assertIs(refused.result["nativeSession"]["resumable"], False,
                      "an unconfirmed outer stop is never a resumable session, even over a "
                      "legal resumable checkpoint")

    def test_an_unknown_continuation_capability_keeps_a_checkpoint_only_session_not_resumable(self):
        context, handle = self._run()
        fields = self._checkpoint_only_fields(context, handle)
        for naming, moved in (
            ("unknown", {**fields, "continuation": {**fields["continuation"], "resumable": None}}),
            ("absent", {**fields, "continuation": None}),
        ):
            with self.subTest(continuationCapability=naming):
                self._rewrite(handle, moved)
                refused = self.adapter.collect(handle, context)
                self.assertIs(refused.shutdown_confirmed, True,
                              "both stop layers stay confirmed; only the capability went away")
                self.assertIs(refused.result["nativeSession"]["resumable"], False,
                              "an unknown or absent continuation capability is not a resumable "
                              "session, even over a legal resumable checkpoint")

    def test_only_a_non_resumable_or_absent_checkpoint_is_never_a_resumable_session(self):
        context, handle = self._run()
        fields = self._checkpoint_only_fields(context, handle)
        incomplete = dict(fields)
        incomplete["evidenceRefs"] = list(fields["evidenceRefs"])[:-1] + [
            self._checkpoint_ref(context, fields, "incomplete")]
        for naming, moved in (("not-resumable", incomplete),
                              ("absent", {**fields, "evidenceRefs": fields["evidenceRefs"][:-1]})):
            with self.subTest(checkpoint=naming):
                self._rewrite(handle, moved)
                refused = self.adapter.collect(handle, context)
                self.assertIs(refused.shutdown_confirmed, True,
                              "both stop layers stay confirmed")
                self.assertIs(refused.result["nativeSession"]["resumable"], False,
                              "without a complete turn, only a resumable checkpoint can carry "
                              "the session's resumable fact")


if __name__ == "__main__":
    unittest.main()
