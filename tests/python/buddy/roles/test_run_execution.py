"""The role executor's share of the ADR-027 facts, over the registered seams.

The discovery receipt crosses this layer untouched — including each harness's
``discoveries[].accountStatus`` — and the Codex run result's model-check fact
is published through the module's own ``native_evidence`` projection wherever
the role merges one. No native process is spawned: the controller seam is
mocked, and the evidence reference is a real private file the projection must
verify by size and digest.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

from hey_my_buddy.buddy.harnesses import run_contract as rc
from hey_my_buddy.buddy.harnesses.run_contract import decode_run_request
from hey_my_buddy.buddy.roles import run_execution
from hey_my_buddy.buddy.roles.turn_io import private_json
from hey_my_buddy.errors import BoardError
from hey_my_buddy.private_dirs import ensure_private_dir
from buddy.harnesses.test_run_contract import full_request


def _catalog(name: str, account_status: str) -> dict:
    return {"source": name + "-native", "adapter": name, "harnessVersion": "fixture",
            "providers": [], "warnings": [],
            "discoveries": [{"adapter": name, "status": "complete",
                             "accountStatus": account_status}]}


class DiscoveryReceiptTests(unittest.TestCase):
    """The discovery outer layer carries the account fact, and the refusal text."""

    def _discover(self, payload: dict, *, exit_code: int = 0, stopped: bool = True) -> dict:
        handle = SimpleNamespace(wait=lambda timeout: 0, terminate=lambda **kwargs: None)
        collection = SimpleNamespace(payload=payload, exit_code=exit_code, stop_confirmed=stopped)
        with mock.patch.object(run_execution, "launch_controller", return_value=handle), \
                mock.patch.object(run_execution, "collect_controller", return_value=collection):
            return run_execution.discover_models("codex")

    def test_the_discovery_receipt_crosses_with_its_account_fact(self):
        receipt = _catalog("codex", "unknown")
        result = self._discover({"status": "ok", "catalog": receipt})
        self.assertEqual(result["discoveries"],
                         [{"adapter": "codex", "status": "complete", "accountStatus": "unknown"}])

    def test_a_refused_discovery_keeps_the_harness_s_own_reason(self):
        with self.assertRaises(BoardError) as caught:
            self._discover({"status": "error",
                            "error": "Codex requires an existing ChatGPT account-plan login"},
                           exit_code=1)
        self.assertEqual(caught.exception.code, "ADAPTER_UNAVAILABLE")
        self.assertEqual(caught.exception.details.get("adapter"), "codex")
        self.assertEqual(caught.exception.message,
                         "Codex requires an existing ChatGPT account-plan login")

    def test_an_unconfirmed_native_stop_never_publishes_a_reading(self):
        with self.assertRaises(BoardError) as caught:
            self._discover({"status": "ok", "catalog": _catalog("codex", "confirmed")},
                           stopped=False)
        self.assertEqual(caught.exception.code, "ADAPTER_UNAVAILABLE")


class CodexModelCheckProjectionTests(unittest.TestCase):
    """The run result's model-check fact, published where the role merges evidence."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="buddy-roles-model-check-",
                                                dir=os.environ.get("BUDDY_CHECKS_TMPDIR", "/tmp"))
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def _request(self, tool_scope: str) -> rc.RunRequest:
        fields = full_request(Path(self.base)).to_payload()
        fields.update(harness="codex", toolScope=tool_scope, sessionServices=[])
        return decode_run_request(fields)

    def _result(self, request: rc.RunRequest, *, with_ref: bool, status: str = "error") -> rc.RunResult:
        refs = []
        if with_ref:
            check = {"adapter": "codex", "selectedModelListed": False,
                     "selected": {"provider": "openai", "model": "gpt-6.1-sol", "effort": "high"}}
            path = ensure_private_dir(self.base / "inv") / "model-check.json"
            private_json(path, check)
            raw = path.read_bytes()
            refs.append(rc.EvidenceRef(kind="model-check", location=str(path),
                                       size_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest()))
        return rc.RunResult(identity=request.identity, harness="codex",
                            end=rc.RunEnd(status=status, reason_code="native-rpc-error",
                                          message="Codex rejected turn/start: model not found: gpt-6.1-sol"),
                            evidence_refs=refs)

    def test_the_fast_receipt_publishes_the_absence_fact_at_its_own_level(self):
        request = self._request("none")
        payload = run_execution._fast_result(self._result(request, with_ref=True), request,
                                             {"stopReason": None, "elapsedMs": 1})
        self.assertEqual(payload["selectedModelListed"], False)
        self.assertEqual(payload["selectedModel"],
                         {"provider": "openai", "model": "gpt-6.1-sol", "effort": "high"})
        self.assertEqual(payload["code"], "native-rpc-error")
        # The native refusal message reaches the receipt's own error field.
        self.assertEqual(payload["error"],
                         "Codex rejected turn/start: model not found: gpt-6.1-sol")

    def test_the_review_receipt_merges_the_same_projection(self):
        request = self._request("read")
        payload = run_execution._review_result(self._result(request, with_ref=True), request,
                                               {"stopReason": None, "elapsedMs": 1})
        self.assertEqual(payload["selectedModelListed"], False)
        self.assertEqual(payload["selectedModel"],
                         {"provider": "openai", "model": "gpt-6.1-sol", "effort": "high"})

    def test_the_coding_receipt_publishes_the_absence_fact_from_its_verified_ref(self):
        request = self._request("write")
        payload = run_execution._worker_facts(self._result(request, with_ref=True))
        self.assertIs(payload["selectedModelListed"], False)
        self.assertEqual(payload["selectedModel"],
                         {"provider": "openai", "model": "gpt-6.1-sol", "effort": "high"})
        self.assertEqual(payload["error"],
                         "Codex rejected turn/start: model not found: gpt-6.1-sol")

    def test_the_coding_receipt_without_an_absence_fact_publishes_nothing(self):
        request = self._request("write")
        payload = run_execution._worker_facts(self._result(request, with_ref=False))
        self.assertNotIn("selectedModelListed", payload)
        self.assertNotIn("selectedModel", payload)

    def test_a_changed_model_check_reference_fails_the_coding_receipt(self):
        # The governed receipt keeps the same verified-evidence rule as every
        # other reference: a changed digest fails the collection, never silently.
        request = self._request("write")
        result = self._result(request, with_ref=True)
        changed = rc.RunResult(
            identity=request.identity, harness="codex", end=result.end,
            evidence_refs=[rc.EvidenceRef(kind="model-check",
                                          location=result.evidence_refs[0].location,
                                          size_bytes=result.evidence_refs[0].size_bytes,
                                          sha256="0" * 64)])
        with self.assertRaises(BoardError):
            run_execution._worker_facts(changed)

    def test_a_run_without_the_absence_fact_publishes_nothing(self):
        request = self._request("none")
        payload = run_execution._fast_result(self._result(request, with_ref=False), request,
                                             {"stopReason": None, "elapsedMs": 1})
        self.assertNotIn("selectedModelListed", payload)
        self.assertNotIn("selectedModel", payload)

    def test_a_changed_evidence_reference_is_refused(self):
        request = self._request("none")
        result = self._result(request, with_ref=True)
        changed = rc.RunResult(
            identity=request.identity, harness="codex", end=result.end,
            evidence_refs=[rc.EvidenceRef(kind="model-check",
                                          location=result.evidence_refs[0].location,
                                          size_bytes=result.evidence_refs[0].size_bytes,
                                          sha256="0" * 64)])
        from hey_my_buddy.buddy.harnesses.registry import run_seam
        with self.assertRaises(BoardError):
            run_seam("codex").native_evidence(changed)


if __name__ == "__main__":
    unittest.main()
