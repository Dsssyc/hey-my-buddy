"""Public model facts snapshots: fixture parsing, identity mapping and retention.

All coverage is offline. The source hook is pinned to a private fixture path by the
test harness; unit tests inject source callables directly, and the publish-path tests
write a models.dev-shaped fixture file for the pinned hook to read. No test touches
the network.
"""
from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

from support import BoardTestCase, FIXTURE_CATALOG, FakeClock

from blackboard.evaluation.test_evaluation import EvaluationTestCase

from hey_my_buddy.blackboard.evaluation import model_facts
from hey_my_buddy.errors import BoardError


#: A models.dev-shaped fixture payload with the real public field names (``cost``,
#: ``limit``, ``release_date``, ``modalities``, flags), the live GLM entry shape,
#: and the API-versus-subscription-plan provider split.
def facts_fixture() -> dict:
    return {
        "deepseek": {
            "id": "deepseek",
            "name": "DeepSeek",
            "models": {
                "deepseek-flash": {
                    "id": "deepseek-flash",
                    "name": "DeepSeek Flash",
                    "release_date": "2025-11-01",
                    "tool_call": True,
                    "reasoning": False,
                    "attachment": True,
                    "temperature": True,
                    "open_weights": True,
                    "modalities": {"input": ["text", "image"], "output": ["text"]},
                    "limit": {"context": 131072, "output": 8192},
                    "cost": {"input": 0.27, "output": 1.1, "cache_read": 0.027, "cache_write": 0.055},
                },
            },
        },
        "openai": {
            "id": "openai",
            "name": "OpenAI",
            "models": {
                "GPT-Test": {
                    "id": "GPT-Test",
                    "name": "GPT Test",
                    "limit": {"context": 400000},
                },
            },
        },
        "zai": {
            "id": "zai",
            "name": "Z.ai",
            "models": {
                "glm-5.3": {
                    "id": "glm-5.3",
                    "name": "GLM-5.3",
                    "release_date": "2026-08-14",
                    "tool_call": True,
                    "reasoning": True,
                    "open_weights": True,
                    "modalities": {"input": ["text"], "output": ["text"]},
                    "limit": {"context": 1000000, "output": 131072},
                    "cost": {"input": 1.4, "output": 4.4, "cache_read": 0.26, "cache_write": 0},
                },
            },
        },
        "zai-coding-plan": {
            "id": "zai-coding-plan",
            "name": "Z.ai Coding Plan",
            "models": {
                "glm-5.3": {
                    "id": "glm-5.3",
                    "name": "GLM-5.3",
                    "release_date": "2026-08-14",
                    "limit": {"context": 1000000, "output": 131072},
                    "cost": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0},
                },
            },
        },
    }


EXPECTED_FLASH_FACTS = {
    "price": {"currency": "USD", "unit": "usd-per-million-tokens",
              "input": 0.27, "output": 1.1, "cacheRead": 0.027, "cacheWrite": 0.055},
    "billing": {"method": "per-token", "unit": "million-tokens", "currency": "USD"},
    "contextLength": 131072,
    "outputLimit": 8192,
    "releaseDate": "2025-11-01",
    "capabilities": {"toolCall": True, "reasoning": False, "attachment": True, "temperature": True,
                     "openWeights": True, "inputModalities": ["text", "image"], "outputModalities": ["text"]},
}

FLASH_FAMILY = ("dsh", "deepseek-official", "deepseek-flash")
FLASH_PROFILE_ID = "dsh:deepseek-official:deepseek-flash:off"
FLASH_LOW_PROFILE_ID = "dsh:deepseek-official:deepseek-flash:low"


class ModelFactsTestCase(EvaluationTestCase):
    """Model facts over the real store and the real publish path, fully offline."""

    # -- fixture helpers -----------------------------------------------------
    def fixture_path(self) -> Path:
        return Path(os.environ["BUDDY_MODEL_FACTS_FILE"])

    def write_fixture(self, payload: dict | None = None, *, raw: str | None = None) -> Path:
        path = self.fixture_path()
        path.write_text(raw if raw is not None else json.dumps(facts_fixture() if payload is None else payload))
        return path

    def remove_fixture(self) -> None:
        self.fixture_path().unlink(missing_ok=True)

    def row(self, board, family: tuple[str, str, str]) -> dict | None:
        with board.store.db.read() as db:
            row = db.execute("SELECT * FROM model_facts WHERE adapter=? AND provider=? AND model=?", family).fetchone()
        return dict(row) if row is not None else None

    def count_refresh_calls(self):
        """Count enablement-triggered refresh calls without changing their behavior."""
        calls = []
        original = model_facts.refresh_families

        def counting(board, families, **kwargs):
            calls.append([list(family) for family in families])
            return original(board, families, **kwargs)

        model_facts.refresh_families = counting
        self.addCleanup(setattr, model_facts, "refresh_families", original)
        return calls

    # -- source and request boundaries ---------------------------------------
    def test_build_request_carries_no_credentials(self):
        request = model_facts.build_request()
        joined = " ".join(str(value) for value in request.header_items()).lower()
        for marker in ("authorization", "cookie", "token", "key"):
            self.assertNotIn(marker, joined)
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.full_url, model_facts.SOURCE_URL)

    def test_fetch_source_reads_the_pinned_file(self):
        path = self.write_fixture()
        raw, origin = model_facts.fetch_source()
        self.assertEqual(json.loads(raw), facts_fixture())
        self.assertEqual(origin, f"file:{path}")

    def test_fetch_source_missing_file_is_unavailable(self):
        # The harness default pins a fixture path that was never written.
        with self.assertRaises(BoardError) as caught:
            model_facts.fetch_source()
        self.assertEqual(caught.exception.code, "MODEL_FACTS_SOURCE_UNAVAILABLE")

    def test_fetch_source_refuses_oversized_payload(self):
        self.write_fixture(raw="x" * (model_facts.MAX_SOURCE_BYTES + 1))
        with self.assertRaises(BoardError) as caught:
            model_facts.fetch_source()
        self.assertEqual(caught.exception.code, "MODEL_FACTS_SOURCE_INVALID")

    def test_parse_source_rejects_structural_garbage(self):
        for raw in (b"[]", b'"text"', b"not json"):
            with self.assertRaises(BoardError) as caught:
                model_facts.parse_source(raw)
            self.assertEqual(caught.exception.code, "MODEL_FACTS_SOURCE_INVALID", raw)

    # -- parsing, facts and identity mapping ---------------------------------
    def test_extract_facts_covers_every_program_fact(self):
        entry = facts_fixture()["deepseek"]["models"]["deepseek-flash"]
        self.assertEqual(model_facts.extract_facts(entry), EXPECTED_FLASH_FACTS)

    def test_extract_facts_partial_entry_keeps_known_fields(self):
        entry = facts_fixture()["openai"]["models"]["GPT-Test"]
        self.assertEqual(model_facts.extract_facts(entry), {"contextLength": 400000})

    def test_extract_facts_ignores_unusable_values(self):
        entry = {"cost": {"input": "free", "output": -1}, "limit": {"context": "big"},
                 "release_date": 7, "tool_call": "yes"}
        self.assertEqual(model_facts.extract_facts(entry), {})

    def test_extract_facts_preserves_context_tier_prices(self):
        # The live tier shape: context-tier prices travel with their tier size, and
        # the over-200k mirror is kept beside the base price.
        entry = {"cost": {"input": 2.5, "output": 7.5, "cache_read": 0.5,
                          "tiers": [
                              {"input": 5, "output": 15, "cache_read": 1, "tier": {"type": "context", "size": 32000}},
                              {"input": 6.25, "output": 18.5, "cache_read": 1.25, "tier": {"type": "context", "size": 128000}},
                          ],
                          "context_over_200k": {"input": 4, "output": 18, "cache_read": 0.4}}}
        facts = model_facts.extract_facts(entry)
        self.assertEqual(facts["price"]["tiers"], [
            {"input": 5, "output": 15, "cacheRead": 1, "tierContextSize": 32000},
            {"input": 6.25, "output": 18.5, "cacheRead": 1.25, "tierContextSize": 128000},
        ])
        self.assertEqual(facts["price"]["contextOver200k"], {"input": 4, "output": 18, "cacheRead": 0.4})
        self.assertEqual(facts["billing"], {"method": "per-token", "unit": "million-tokens", "currency": "USD"})

    def test_extract_facts_marks_plan_included_pricing(self):
        # A subscription plan entry's zeros mean plan inclusion, never a free API.
        entry = {"cost": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0},
                 "limit": {"context": 1000000}}
        facts = model_facts.extract_facts(entry, plan_provider=True)
        self.assertEqual(facts["billing"]["method"], "plan-included")
        self.assertEqual(facts["price"]["input"], 0)
        self.assertEqual(facts["contextLength"], 1000000)

    def test_extract_facts_reads_the_cost_field_not_pricing(self):
        # The live source publishes prices under ``cost``; a legacy ``pricing`` key
        # alone is not a price.
        self.assertEqual(model_facts.extract_facts({"pricing": {"input": 3, "output": 15}}), {})

    def test_resolve_identity_alias_exact_normalized_and_missing(self):
        index = model_facts.parse_source(json.dumps(facts_fixture()).encode())
        # Provider alias: the harness label resolves to the published provider id.
        self.assertEqual(model_facts.resolve_identity(index, "deepseek-official", "deepseek-flash"),
                         ("deepseek", "deepseek-flash"))
        # Exact provider id needs no alias.
        self.assertEqual(model_facts.resolve_identity(index, "openai", "GPT-Test"), ("openai", "GPT-Test"))
        # Normalized equality tolerates case and punctuation, never substrings.
        self.assertEqual(model_facts.resolve_identity(index, "OpenAI", "gpt_test"), ("openai", "GPT-Test"))
        self.assertEqual(model_facts.resolve_identity(index, "open", "GPT"), (None, None))
        # A known provider without the requested model keeps the provider, so the
        # caller can report the exact unknown reason.
        self.assertEqual(model_facts.resolve_identity(index, "openai", "gpt-missing"), ("openai", None))
        # A models/ prefix on either side still resolves.
        self.assertEqual(model_facts.resolve_identity(index, "deepseek-official", "models/deepseek-flash"),
                         ("deepseek", "deepseek-flash"))
        # Our zcode API-provider label resolves to the API provider, never to the
        # subscription plan entry that publishes the same model with zero prices.
        self.assertEqual(model_facts.resolve_identity(index, "zai-api", "glm-5.3"), ("zai", "glm-5.3"))

    def test_parse_source_bounds_are_an_explicit_failure(self):
        from unittest import mock

        payload = {"p": {"models": {f"m{index}": {"id": f"m{index}"} for index in range(5)}}}
        with mock.patch.object(model_facts, "MAX_MODELS", 4):
            with self.assertRaises(BoardError) as caught:
                model_facts.parse_source(json.dumps(payload).encode())
            self.assertEqual(caught.exception.code, "MODEL_FACTS_SOURCE_INVALID")
        two_providers = {"p": {"models": {}}, "q": {"models": {}}}
        with mock.patch.object(model_facts, "MAX_PROVIDERS", 1):
            with self.assertRaises(BoardError) as caught:
                model_facts.parse_source(json.dumps(two_providers).encode())
            self.assertEqual(caught.exception.code, "MODEL_FACTS_SOURCE_INVALID")

    # -- refresh semantics over the real store -------------------------------
    def test_refresh_writes_one_dated_snapshot_per_family(self):
        board = self.board()
        outcome = model_facts.refresh_families(board.store, [FLASH_FAMILY], now="2026-10-01T00:00:00.000Z",
                                               source=lambda: (json.dumps(facts_fixture()).encode(), "file:fixture"))
        self.assertTrue(outcome["ok"])
        self.assertEqual(outcome["refreshed"], [list(FLASH_FAMILY)])
        self.assertEqual(outcome["unknown"], [])
        view = model_facts.family_facts(board.store, [FLASH_FAMILY])[FLASH_FAMILY]
        self.assertEqual(view["status"], "current")
        self.assertEqual(view["facts"], EXPECTED_FLASH_FACTS)
        self.assertEqual(view["identity"], {"devProvider": "deepseek", "devModel": "deepseek-flash"})
        self.assertEqual(view["source"], "file:fixture")
        self.assertEqual(view["fetchedAt"], "2026-10-01T00:00:00.000Z")
        self.assertIsNone(view["reason"])

    def test_refresh_failure_retains_the_previous_snapshot(self):
        board = self.board()
        model_facts.refresh_families(board.store, [FLASH_FAMILY], source=lambda: (json.dumps(facts_fixture()).encode(), "file:one"))
        before = self.row(board, FLASH_FAMILY)
        outcome = model_facts.refresh_families(board.store, [FLASH_FAMILY], source=lambda: (_ for _ in ()).throw(
            BoardError("MODEL_FACTS_SOURCE_UNAVAILABLE", "down")))
        self.assertFalse(outcome["ok"])
        self.assertEqual(outcome["retained"], [list(FLASH_FAMILY)])
        self.assertEqual(self.row(board, FLASH_FAMILY), before)
        # A later successful fetch replaces the retained snapshot.
        model_facts.refresh_families(board.store, [FLASH_FAMILY], source=lambda: (json.dumps(facts_fixture()).encode(), "file:two"))
        self.assertEqual(self.row(board, FLASH_FAMILY)["source"], "file:two")

    def test_refresh_missing_entry_records_unknown(self):
        board = self.board()
        fixture = facts_fixture()
        del fixture["deepseek"]["models"]["deepseek-flash"]
        outcome = model_facts.refresh_families(board.store, [FLASH_FAMILY], source=lambda: (json.dumps(fixture).encode(), "file:fixture"))
        self.assertTrue(outcome["ok"])
        self.assertEqual(outcome["unknown"], [{"family": list(FLASH_FAMILY), "reason": "entry-missing"}])
        view = model_facts.family_facts(board.store, [FLASH_FAMILY])[FLASH_FAMILY]
        self.assertEqual(view["status"], "unknown")
        self.assertEqual(view["reason"], "entry-missing")
        self.assertIsNone(view["facts"])

    def test_refresh_unresolvable_identity_records_unknown(self):
        board = self.board()
        outcome = model_facts.refresh_families(board.store, [("dsh", "fixture-api", "fixture-model")],
                                               source=lambda: (json.dumps(facts_fixture()).encode(), "file:fixture"))
        self.assertEqual(outcome["unknown"][0]["reason"], "identity-unresolved")
        row = self.row(board, ("dsh", "fixture-api", "fixture-model"))
        self.assertEqual(row["status"], "unknown")
        self.assertEqual(row["reason"], "identity-unresolved")

    def test_refresh_records_plan_inclusion_not_a_free_api(self):
        board = self.board()
        family = ("zcode", "zai-api", "glm-5.3")
        model_facts.refresh_families(board.store, [family],
                                     source=lambda: (json.dumps(facts_fixture()).encode(), "file:fixture"))
        view = model_facts.family_facts(board.store, [family])[family]
        self.assertEqual(view["facts"]["billing"]["method"], "per-token")
        self.assertEqual(view["facts"]["price"]["input"], 1.4)
        # The same model under a subscription plan provider is recorded as
        # plan-included, so its zeros are never read as a free API.
        plan_family = ("zcode", "zai-coding-plan", "glm-5.3")
        model_facts.refresh_families(board.store, [plan_family],
                                     source=lambda: (json.dumps(facts_fixture()).encode(), "file:fixture"))
        plan_view = model_facts.family_facts(board.store, [plan_family])[plan_family]
        self.assertEqual(plan_view["facts"]["billing"]["method"], "plan-included")
        self.assertEqual(plan_view["identity"], {"devProvider": "zai-coding-plan", "devModel": "glm-5.3"})

    def test_family_facts_without_a_row_is_unknown(self):
        board = self.board()
        view = model_facts.family_facts(board.store, [FLASH_FAMILY])[FLASH_FAMILY]
        self.assertEqual(view["status"], "unknown")
        self.assertEqual(view["reason"], "never-fetched")
        self.assertIsNone(view["facts"])

    def test_refresh_rejects_bad_families(self):
        board = self.board()
        with self.assertRaises(BoardError) as caught:
            model_facts.refresh_families(board.store, [("dsh", "provider",)])
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
        with self.assertRaises(BoardError) as caught:
            model_facts.refresh_families(board.store, [("dsh", f"provider-{index}", "model") for index in range(model_facts.MAX_FAMILIES + 1)])
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")

    # -- the user enablement trigger over the real publish path --------------
    def seed(self, board):
        self.seed_catalog(board)

    def test_enablement_refreshes_the_enabled_family(self):
        board = self.board()
        self.write_fixture()
        self.seed(board)
        calls = self.count_refresh_calls()
        self.publish(board, profileSettings=[{"profileId": FLASH_PROFILE_ID, "enabled": True}])
        self.assertEqual(calls, [[list(FLASH_FAMILY)]])
        view = model_facts.family_facts(board.store, [FLASH_FAMILY])[FLASH_FAMILY]
        self.assertEqual(view["status"], "current")
        self.assertEqual(view["facts"], EXPECTED_FLASH_FACTS)
        self.assertTrue(view["source"].startswith("file:"))
        self.assertIsNotNone(view["fetchedAt"])

    def test_only_a_family_enablement_transition_triggers(self):
        board = self.board()
        self.write_fixture()
        self.seed(board)
        calls = self.count_refresh_calls()
        self.publish(board, request_id="w1", command_id="c1",
                     profileSettings=[{"profileId": FLASH_PROFILE_ID, "enabled": True}])
        self.assertEqual(len(calls), 1)
        # A second effort of the already enabled family is not a new enablement.
        self.publish(board, request_id="w2", command_id="c2",
                     profileSettings=[{"profileId": FLASH_LOW_PROFILE_ID, "enabled": True}])
        self.assertEqual(len(calls), 1)
        # Re-saving an enabled effort never fetches.
        self.publish(board, request_id="w3", command_id="c3",
                     profileSettings=[{"profileId": FLASH_PROFILE_ID, "enabled": True}])
        self.assertEqual(len(calls), 1)
        # While another effort of the family stays enabled, re-enabling this one is
        # still not a family enablement.
        self.publish(board, request_id="w4", command_id="c4",
                     profileSettings=[{"profileId": FLASH_PROFILE_ID, "enabled": False}])
        self.publish(board, request_id="w5", command_id="c5",
                     profileSettings=[{"profileId": FLASH_PROFILE_ID, "enabled": True}])
        self.assertEqual(len(calls), 1)
        # Once every effort of the family is disabled again, enabling one is a fresh
        # user enablement of the family.
        self.publish(board, request_id="w6", command_id="c6",
                     profileSettings=[{"profileId": FLASH_PROFILE_ID, "enabled": False},
                                      {"profileId": FLASH_LOW_PROFILE_ID, "enabled": False}])
        self.publish(board, request_id="w7", command_id="c7",
                     profileSettings=[{"profileId": FLASH_PROFILE_ID, "enabled": True}])
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[-1], [list(FLASH_FAMILY)])

    def test_switching_enabled_efforts_in_one_patch_never_refetches(self):
        board = self.board()
        self.write_fixture()
        self.seed(board)
        calls = self.count_refresh_calls()
        self.publish(board, request_id="start", command_id="start",
                     profileSettings=[{"profileId": FLASH_PROFILE_ID, "enabled": True}])
        self.publish(board, request_id="switch1", command_id="switch1",
                     profileSettings=[{"profileId": FLASH_PROFILE_ID, "enabled": False},
                                      {"profileId": FLASH_LOW_PROFILE_ID, "enabled": True}])
        self.assertEqual(len(calls), 1)
        self.publish(board, request_id="switch2", command_id="switch2",
                     profileSettings=[{"profileId": FLASH_PROFILE_ID, "enabled": True},
                                      {"profileId": FLASH_LOW_PROFILE_ID, "enabled": False}])
        self.assertEqual(len(calls), 1)

    def test_alias_absence_and_ambiguous_normalization_stay_unknown(self):
        self.assertEqual(model_facts.resolve_identity({}, "zai-api", "GLM-5.3"), (None, None))
        models = {"a-b": {"models": {}}, "ab": {"models": {}}}
        raw = json.dumps(models).encode()
        self.assertEqual(model_facts.resolve_identity(model_facts.parse_source(raw), "A_B", "anything"), (None, None))
        index = {"openai": {"model-1": {}, "model1": {}}}
        self.assertEqual(model_facts.resolve_identity(index, "openai", "MODEL_1"), ("openai", None))
        self.assertEqual(model_facts.resolve_identity(index, "openai", "model-1"), ("openai", "model-1"))

    def test_duplicate_publish_replay_does_not_refetch(self):
        board = self.board()
        self.write_fixture()
        self.seed(board)
        calls = self.count_refresh_calls()
        grant = self.begin(board, request_id="write-1")
        patch = [{"profileId": FLASH_PROFILE_ID, "enabled": True}]
        first = self.publish_with(board, grant, "cmd-1", profileSettings=patch)
        self.assertEqual(len(calls), 1)
        # The idempotent replay of the same command returns the recorded response
        # without a second fetch.
        replay = self.publish_with(board, grant, "cmd-1", profileSettings=patch)
        self.assertTrue(replay["duplicate"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(first["revision"], replay["revision"])

    def test_failed_fetch_keeps_the_committed_publication_and_retains(self):
        board = self.board()
        # No fixture file: the source read fails, the publication still commits.
        self.remove_fixture()
        self.seed(board)
        self.publish(board, request_id="w1", command_id="c1",
                     profileSettings=[{"profileId": FLASH_PROFILE_ID, "enabled": True}])
        self.assertIsNone(self.row(board, FLASH_FAMILY))
        # A snapshot from an earlier fetch survives the next failed fetch untouched.
        self.write_fixture()
        self.publish(board, request_id="w2", command_id="c2",
                     profileSettings=[{"profileId": FLASH_PROFILE_ID, "enabled": False}])
        self.publish(board, request_id="w3", command_id="c3",
                     profileSettings=[{"profileId": FLASH_PROFILE_ID, "enabled": True}])
        retained = self.row(board, FLASH_FAMILY)
        self.assertEqual(retained["status"], "current")
        first_hash = retained["payload_sha256"]
        self.remove_fixture()
        self.publish(board, request_id="w4", command_id="c4",
                     profileSettings=[{"profileId": FLASH_PROFILE_ID, "enabled": False}])
        self.publish(board, request_id="w5", command_id="c5",
                     profileSettings=[{"profileId": FLASH_PROFILE_ID, "enabled": True}])
        after = self.row(board, FLASH_FAMILY)
        self.assertEqual(after["payload_sha256"], first_hash)
        self.assertEqual(after["status"], "current")

    def test_removal_marks_unknown_and_a_later_failure_keeps_that_unknown(self):
        board = self.board()
        self.write_fixture()
        self.seed(board)
        self.publish(board, request_id="w1", command_id="c1",
                     profileSettings=[{"profileId": FLASH_PROFILE_ID, "enabled": True}])
        self.assertEqual(self.row(board, FLASH_FAMILY)["status"], "current")
        # A completed fetch without the entry records unknown with its date.
        removed = facts_fixture()
        del removed["deepseek"]["models"]["deepseek-flash"]
        self.write_fixture(removed)
        self.publish(board, request_id="w2", command_id="c2",
                     profileSettings=[{"profileId": FLASH_PROFILE_ID, "enabled": False}])
        self.publish(board, request_id="w3", command_id="c3",
                     profileSettings=[{"profileId": FLASH_PROFILE_ID, "enabled": True}])
        unknown = self.row(board, FLASH_FAMILY)
        self.assertEqual(unknown["status"], "unknown")
        self.assertEqual(unknown["reason"], "entry-missing")
        unknown_hash = unknown["payload_sha256"]
        # The next failed fetch retains the unknown record; it never invents facts.
        self.remove_fixture()
        self.publish(board, request_id="w4", command_id="c4",
                     profileSettings=[{"profileId": FLASH_PROFILE_ID, "enabled": False}])
        self.publish(board, request_id="w5", command_id="c5",
                     profileSettings=[{"profileId": FLASH_PROFILE_ID, "enabled": True}])
        retained = self.row(board, FLASH_FAMILY)
        self.assertEqual(retained["payload_sha256"], unknown_hash)
        self.assertEqual(retained["reason"], "entry-missing")

    def test_disabled_family_policy_publish_does_not_fetch(self):
        board = self.board()
        self.write_fixture()
        self.seed(board)
        calls = self.count_refresh_calls()
        # Preference and note patches without an enablement never touch the source.
        self.publish(board, familyPreferenceChanges=[{"adapter": "dsh", "provider": "deepseek-official",
                                                      "model": "deepseek-flash", "mode": "prefer", "reason": ""}])
        self.assertEqual(calls, [])

    def test_resaving_an_enabled_effort_does_not_trigger(self):
        # A single-effort catalog removes the "another effort is enabled" shield, so
        # this test pins the re-save rule itself: only the disabled-to-enabled
        # transition of a family is a user enablement.
        catalog = json.loads(json.dumps(FIXTURE_CATALOG))
        catalog["providers"][0]["efforts"] = ["off"]
        self.catalog_fixture(catalog)
        board = self.board()
        self.write_fixture()
        self.seed(board)
        calls = self.count_refresh_calls()
        self.publish(board, request_id="w1", command_id="c1",
                     profileSettings=[{"profileId": FLASH_PROFILE_ID, "enabled": True}])
        self.assertEqual(len(calls), 1)
        self.publish(board, request_id="w2", command_id="c2",
                     profileSettings=[{"profileId": FLASH_PROFILE_ID, "enabled": True}])
        self.assertEqual(len(calls), 1)


class ModelFactsClockTestCase(BoardTestCase):
    """The refresh boundary a later profile-plane caller reuses, with a fake clock."""

    def test_refresh_uses_the_injected_clock_and_updates_the_dated_snapshot(self):
        clock = FakeClock()
        board = self.board(clock=clock)
        source = lambda: (json.dumps(facts_fixture()).encode(), "file:fixture")  # noqa: E731
        model_facts.refresh_families(board.store, [FLASH_FAMILY], now=clock(), source=source)
        first = model_facts.snapshot_marker(board.store)
        self.assertNotEqual(first, "empty")
        clock.advance(3600)
        model_facts.refresh_families(board.store, [FLASH_FAMILY], now=clock(), source=source)
        second = model_facts.snapshot_marker(board.store)
        self.assertNotEqual(first, second)
        with board.store.db.read() as db:
            row = db.execute("SELECT fetched_at FROM model_facts").fetchone()
        self.assertEqual(row["fetched_at"], "2026-01-01T01:00:00.000Z")


if __name__ == "__main__":
    unittest.main()
