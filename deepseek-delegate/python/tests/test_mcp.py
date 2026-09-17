import asyncio
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from buddy.transport import call_service


class McpIntegration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="buddy-mcp-", dir="/tmp")
        self.root = Path(self.temp.name)
        self.cwd = self.root / "cwd"; self.cwd.mkdir()
        self.settings = self.root / "settings.yaml"; self.settings.write_text('agent-default-model:\n  model: fixture-model\n')
        self.mock = self.root / "dsh"
        self.mock.write_text('#!/usr/bin/env node\nconsole.log("mock dsh result");\n')
        self.mock.chmod(0o700)
        self.state = self.root / "state"
        self.env = {k:v for k,v in os.environ.items() if not k.startswith(('DSH_', 'BUDDY_', 'MOCK_')) and k not in ['CODEX_APP_TOOLS_PIPE_PATH','C2_RELAY_ANCHOR_ADDRESS']}
        self.env.update(BUDDY_STATE_DIR=str(self.state), BUDDY_PYTHON=sys.executable, BUDDY_NODE=shutil.which('node'), DSH_BIN=str(self.mock), DSH_SETTINGS_FILE=str(self.settings), DSH_HOME=str(self.root/'dsh-home'), C2_RELAY_ANCHOR_ADDRESS='')

    async def asyncTearDown(self):
        try: await asyncio.to_thread(call_service, 'stop', {}, self.state)
        except Exception: pass
        self.temp.cleanup()

    def process(self):
        return StdioServerParameters(command=sys.executable, args=['-m','buddy.mcp_server'],env=self.env)

    async def test_default_start_reconnects_without_native_app_channel(self):
        async with stdio_client(self.process()) as streams:
            async with ClientSession(*streams) as client:
                await client.initialize()
                names = {t.name for t in (await client.list_tools()).tools}
                self.assertIn('buddy_start',names);self.assertIn('buddy_health',names)
                denied = await client.call_tool('buddy_start',{'requestId':'no-host','task':'x','cwd':str(self.cwd),'workspace':False,'notify':True})
                self.assertTrue(denied.isError)
                result = await client.call_tool('buddy_start',{'requestId':'fixture','task':'x','cwd':str(self.cwd),'workspace':False})
                self.assertFalse(result.isError, result)
                run = result.structuredContent
        # The MCP connection is gone; the detached C-Two service retains ownership.
        async with stdio_client(self.process()) as streams:
            async with ClientSession(*streams) as client:
                await client.initialize()
                again = await client.call_tool('buddy_start',{'requestId':'fixture','task':'x','cwd':str(self.cwd),'workspace':False,'notify':False})
                self.assertEqual(again.structuredContent['runId'],run['runId'])
                for _ in range(20):
                    current = await client.call_tool('buddy_wait',{'runId':run['runId'],'timeoutMs':1000})
                    if current.structuredContent['resultAvailable']: break
                final = await client.call_tool('buddy_result',{'runId':run['runId']})
                self.assertEqual(final.structuredContent['status'],'completed',final)
                self.assertEqual(final.structuredContent['result']['finalText'],'mock dsh result')
                self.assertEqual(self.settings.read_text(),'agent-default-model:\n  model: fixture-model\n')
