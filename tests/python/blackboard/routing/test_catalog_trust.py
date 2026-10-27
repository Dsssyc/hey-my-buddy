"""ADR-027 rule 2 at the routing boundary: a pending model stays a legal candidate."""
from support import BoardTestCase, FakeClock
from hey_my_buddy.blackboard.catalog import catalog

from hey_my_buddy.blackboard.routing.decision import DecisionCoordinator


def reading(models=('alpha',)):
    return catalog.canonical_payload({'source': 'fixture-native', 'discoveries': [{'harness': 'dsh', 'status': 'complete', 'accountStatus': 'confirmed'}],
            'providers': [{'harness': 'dsh', 'provider': 'fixture',
                           'models': [{'id': model, 'efforts': ['max'], 'available': True} for model in models]}]})


class CatalogTrustRoutingTests(BoardTestCase):
    def setUp(self):
        super().setUp()
        self.clock = FakeClock()
        self.board = self.board(clock=self.clock)
        self.evaluation = self.board.evaluation
        self.evaluation.record_catalog(reading())
        with self.board.store.db.write() as db:
            db.execute("UPDATE buddies SET enabled=1 WHERE harness='dsh'")

    def candidates(self):
        with self.board.store.db.read() as db:
            return [row["buddy_id"] for row in DecisionCoordinator._scan_candidates(db, [], coding_only=True)[0]]

    def test_pending_model_stays_inside_the_routing_bounds(self):
        self.evaluation.record_catalog(reading(models=()))
        self.assertEqual(self.candidates(), ['dsh:fixture:alpha:max'],
                         'A first confirmed absence must not remove a legal candidate')

    def test_confirmed_absence_leaves_the_bounds_after_the_window(self):
        self.evaluation.record_catalog(reading(models=()))
        self.clock.advance(3601)
        self.evaluation.record_catalog(reading(models=()))
        self.assertEqual(self.candidates(), [])

    def test_unknown_reading_keeps_the_candidate_and_its_bounds(self):
        self.evaluation.record_catalog(reading(models=()))
        self.evaluation.record_catalog({**reading(models=()), 'discoveries': [
            {'harness': 'dsh', 'status': 'complete', 'accountStatus': 'unknown'}]})
        self.assertEqual(self.candidates(), ['dsh:fixture:alpha:max'])

    def test_user_pin_on_a_pending_buddy_still_resolves(self):
        self.evaluation.record_catalog(reading(models=()))
        pinned = self.board.console_call(
            "user_policy_publish",
            {
                "commandId": "pin-1",
                "preferenceChanges": [{"buddyId": "dsh:fixture:alpha:max", "mode": "pin", "note": "still my model"}],
            },
        )
        self.assertEqual(pinned["counts"]["preferenceChanges"], 1)
        with self.board.store.db.read() as db:
            row = db.execute("SELECT mode FROM evaluation_preferences WHERE buddy_id='dsh:fixture:alpha:max'").fetchone()
        self.assertEqual(row["mode"], "pin")
