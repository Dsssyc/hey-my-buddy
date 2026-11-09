"""The declaration of harnesses whose protocol cannot show the model's start.

The Router start wait is a judgment about live output: it holds only where the
native protocol can show the model beginning to answer as it happens. This
table says which verified harnesses cannot, so the execution process skips the
wait for them; every name absent from it — including the other three harnesses
— is simply unverified and keeps the configured wait.
"""
from __future__ import annotations

import unittest

from hey_my_buddy.protocol.capabilities import MODEL_START_UNSEEN, model_start_unseen
from hey_my_buddy.protocol.harness_names import HARNESS_NAMES


class ModelStartUnseenDeclarationTests(unittest.TestCase):
    def test_dsh_alone_is_declared_with_the_verified_basis(self):
        declaration = MODEL_START_UNSEEN["dsh"]
        self.assertEqual(declaration.form, "cli-acp")
        self.assertEqual(declaration.versions, ("0.2.2-rc.1",))
        # The basis states the protocol fact the declaration rests on: the ACP
        # channel emits nothing until the model's whole step is in the session.
        self.assertEqual(declaration.basis,
                         "the ACP server emits each block only after the model's whole step "
                         "is written to the session")

    def test_every_other_name_is_undeclared_and_keeps_the_wait(self):
        self.assertEqual(set(MODEL_START_UNSEEN), {"dsh"})
        for name in HARNESS_NAMES:
            with self.subTest(harness=name):
                if name == "dsh":
                    self.assertIs(model_start_unseen(name), MODEL_START_UNSEEN["dsh"])
                else:
                    self.assertIsNone(model_start_unseen(name))
        for name in ("command", "decision", "not-a-harness"):
            with self.subTest(harness=name):
                self.assertIsNone(model_start_unseen(name))


if __name__ == "__main__":
    unittest.main()
