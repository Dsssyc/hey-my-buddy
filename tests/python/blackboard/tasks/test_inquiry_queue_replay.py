"""A retried question reaches the original attempt once its channel attaches."""
from blackboard.tasks import test_inquiry as fixture
from support import BoardTestCase


class InquiryQueueReplayTests(BoardTestCase):
    def test_duplicate_question_is_injected_after_the_original_channel_binds(self):
        self.catalog_fixture()
        board = self.board()
        client = board.client()
        task = fixture.TestInquiry._submit(self,client,self.workdir(),request_id="unbound-question")
        task,attempt,owner = fixture.attach_owner(self,board,client,task,"dsh")
        view = board.store.task_get({"runId":task["runId"]})["task"]
        channel = board.live_registry.channel_for(view)
        board.live_registry.unbind(attempt["attemptId"],channel)
        packet = {"runId":task["runId"],"question":"What remains?"}
        first = board.call("inquiry_observe",packet)
        self.assertEqual(first["inquiry"]["state"],"queued")
        self.assertEqual(owner.requests,[])
        board.live_registry.bind(attempt["attemptId"],fixture.ExecutionChannel(owner))
        replay = board.call("inquiry_observe",packet)
        self.assertEqual(replay["inquiryId"],first["inquiryId"])
        self.assertTrue(replay["inquiry"]["duplicate"])
        self.assertTrue(replay["bridge"]["observed"])
        self.assertIn(first["inquiryId"],owner.entries)
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM messages WHERE task_id=?",(task["runId"],)).fetchone()[0],1)
