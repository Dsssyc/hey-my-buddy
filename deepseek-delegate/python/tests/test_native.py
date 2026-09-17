import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from buddy.native import CompletionReceiver, caller_thread


class NativeDelivery(unittest.IsolatedAsyncioTestCase):
    async def test_binding_dedup_and_submitted_receipt(self):
        with tempfile.TemporaryDirectory() as d:
            run_id, thread_id = str(uuid4()), str(uuid4())
            calls=[]
            def service(method,params):
                calls.append((method,params))
                if method=='status':return {'runId':run_id,'requestId':'req','resultAvailable':True,'shutdownConfirmed':True,'status':'completed'}
                return {}
            receiver=CompletionReceiver(Path(d),asyncio.get_running_loop(),service)
            token=receiver.bind('req',thread_id)
            event={'token':token,'requestId':'req','runId':run_id,'eventId':run_id}
            with patch('buddy.native.native_call',new=AsyncMock(return_value={'threadId':thread_id})) as native:
                self.assertEqual(json.loads(receiver.submit(json.dumps(event)))['status'],'received')
                receiver.submit(json.dumps(event))
                await asyncio.sleep(0)
                await asyncio.gather(*receiver.tasks)
                self.assertEqual(native.await_count,1)
                self.assertEqual(json.loads(receiver.submit(json.dumps(event)))['status'],'submitted')
                self.assertEqual(calls[-1][1]['status'],'submitted')
            with self.assertRaises(ValueError):receiver.submit(json.dumps({**event,'token':'wrong'}))

    async def test_uncertain_native_send_never_repeats(self):
        with tempfile.TemporaryDirectory() as d:
            run_id,thread_id=str(uuid4()),str(uuid4())
            def service(method,params):return {'requestId':'r','resultAvailable':True,'shutdownConfirmed':True}
            receiver=CompletionReceiver(Path(d),asyncio.get_running_loop(),service)
            event={'token':receiver.bind('r',thread_id),'requestId':'r','runId':run_id,'eventId':run_id}
            with patch('buddy.native.native_call',new=AsyncMock(side_effect=TimeoutError('delivery unknown'))) as native:
                receiver.submit(json.dumps(event));await asyncio.sleep(0);await asyncio.gather(*receiver.tasks)
                self.assertEqual(json.loads(receiver.submit(json.dumps(event)))['status'],'unknown')
                self.assertEqual(native.await_count,1)

    def test_real_metadata_only(self):
        thread_id=str(uuid4())
        self.assertEqual(caller_thread({'x-codex-turn-metadata':{'thread_id':thread_id}}),thread_id)
        self.assertIsNone(caller_thread({}));self.assertIsNone(caller_thread({'threadId':'invented'}))
