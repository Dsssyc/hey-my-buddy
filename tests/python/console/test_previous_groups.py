"""Earlier sampled identities shown beside the current kind evaluation."""
from __future__ import annotations

from support import BoardTestCase, enable_fixture_buddy

from hey_my_buddy.blackboard.evaluation.models import (
    append_correspondence, create_model, current_model,
)
from hey_my_buddy.console.reads import _kind_evaluations


class PreviousGroupsTests(BoardTestCase):
    def setUp(self):
        super().setUp()
        self.catalog_fixture()
        self.board_ = self.board()
        self.first = ('dsh', 'first-provider', 'first-name')
        self.second = ('dsh', 'second-provider', 'second-name')
        self.other_harness = ('codex', 'third-provider', 'third-name')
        for service, effort in ((self.first, 'off'), (self.first, 'low'),
                                (self.second, 'off'), (self.other_harness, 'off')):
            enable_fixture_buddy(self.board_.store, dict(zip(
                ('harness', 'provider', 'model', 'effort'), (*service, effort))))

        with self.board_.store.db.write() as db:
            self.first_old = current_model(db, *self.first)
            self.second_old = current_model(db, *self.second)
            self.other_old = current_model(db, *self.other_harness)
            self.middle = create_model(db, 'Middle generation')
            self.unsampled = create_model(db, 'Unsampled generation')
            self.current = create_model(db, 'Current generation')

            # First name: A -> B -> A -> unsampled -> current. The repeated A
            # should show once, ordered at its most recent prior position.
            for model, since in ((self.middle, '2026-11-01T02:00:00Z'),
                                 (self.first_old, '2026-11-01T03:00:00Z'),
                                 (self.unsampled, '2026-11-01T04:00:00Z'),
                                 (self.current, '2026-11-01T05:00:00Z')):
                append_correspondence(db, self.first, model, since, 'user')
            append_correspondence(db, self.second, self.first_old, '2026-11-01T05:30:00Z', 'user')
            append_correspondence(db, self.second, self.current, '2026-11-01T06:00:00Z', 'user')
            append_correspondence(db, self.other_harness, self.current, '2026-11-01T07:00:00Z', 'user')

            for harness, model, effort in (
                ('dsh', self.first_old, 'off'), ('dsh', self.middle, 'off'),
                ('dsh', self.middle, 'low'), ('dsh', self.second_old, 'off'),
                ('codex', self.other_old, 'off'),
                # A sample for an unrelated harness cannot make that name
                # appear in this harness's current kind.
                ('dsh', self.other_old, 'off'),
            ):
                db.execute(
                    'INSERT INTO kind_statistics(harness,model_id,effort,samples,successes,'
                    'first_passes,rework_rounds,failures) VALUES(?,?,?,1,1,1,0,0)',
                    (harness, model, effort),
                )

    def groups(self, harness, model, effort):
        with self.board_.store.db.read() as db:
            row = next(row for row in _kind_evaluations(db)
                       if (row['harness'], row['modelId'], row['effort']) == (harness, model, effort))
        return row

    @staticmethod
    def name(service, since):
        return dict(zip(('harness', 'provider', 'model'), service)) | {
            'since': since,
        }

    @staticmethod
    def expected(model, display_name, names):
        return {'modelId': model, 'modelDisplayName': display_name, 'names': names}

    def test_current_names_show_sampled_old_targets_once_and_keep_statistics_separate(self):
        current_off = self.groups('dsh', self.current, 'off')
        self.assertEqual(current_off['previousGroups'], [
            self.expected(self.first_old, 'first-name', [
                self.name(self.second, '2026-11-01T06:00:00Z'),
                self.name(self.first, '2026-11-01T05:00:00Z'),
            ]),
            self.expected(self.second_old, 'second-name', [
                self.name(self.second, '2026-11-01T06:00:00Z'),
            ]),
            self.expected(self.middle, 'Middle generation', [
                self.name(self.first, '2026-11-01T05:00:00Z'),
            ]),
        ])
        self.assertEqual(current_off['statistics']['overall']['samples'], 0)
        self.assertEqual(self.groups('dsh', self.first_old, 'off')['statistics']['overall']['samples'], 1)
        self.assertEqual(self.groups('dsh', self.current, 'low')['previousGroups'], [
            self.expected(self.middle, 'Middle generation', [
                self.name(self.first, '2026-11-01T05:00:00Z'),
            ]),
        ])
        self.assertEqual(self.groups('codex', self.current, 'off')['previousGroups'], [
            self.expected(self.other_old, 'third-name', [
                self.name(self.other_harness, '2026-11-01T07:00:00Z'),
            ]),
        ])

    def test_models_still_current_under_other_names_are_not_previous(self):
        with self.board_.store.db.write() as db:
            append_correspondence(db, self.second, self.first_old, '2026-11-01T08:00:00Z', 'user')
            append_correspondence(db, self.other_harness, self.middle,
                                  '2026-11-01T09:00:00Z', 'user')
        self.assertEqual(self.groups('dsh', self.current, 'off')['previousGroups'], [])
        self.assertEqual(self.groups('dsh', self.current, 'low')['previousGroups'], [])

    def test_undo_changes_current_names_and_excludes_deleted_history(self):
        with self.board_.store.db.write() as db:
            db.execute(
                'DELETE FROM model_correspondences WHERE rowid=(SELECT MAX(rowid)'
                ' FROM model_correspondences WHERE harness=? AND provider=? AND model=?)',
                self.first,
            )
        self.assertEqual(self.groups('dsh', self.current, 'off')['previousGroups'], [
            self.expected(self.first_old, 'first-name', [
                self.name(self.second, '2026-11-01T06:00:00Z'),
            ]),
            self.expected(self.second_old, 'second-name', [
                self.name(self.second, '2026-11-01T06:00:00Z'),
            ]),
        ])
        self.assertEqual(self.groups('dsh', self.unsampled, 'off')['previousGroups'], [
            self.expected(self.first_old, 'first-name', [
                self.name(self.first, '2026-11-01T04:00:00Z'),
            ]),
            self.expected(self.middle, 'Middle generation', [
                self.name(self.first, '2026-11-01T04:00:00Z'),
            ]),
        ])
