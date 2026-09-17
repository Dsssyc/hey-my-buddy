import json
import os
from pathlib import Path
import tempfile
import unittest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

class PortablePlugin(unittest.IsolatedAsyncioTestCase):
    async def test_declared_launcher_starts_from_unrelated_cwd_and_minimal_path(self):
        root=Path(__file__).resolve().parents[3]
        config=json.loads((root/'mcp.json').read_text())['mcpServers']['buddy_ctwo']
        self.assertEqual(config['type'],'stdio')
        self.assertTrue(config['command'].startswith('./'))
        command=root/config['command'][2:]
        with tempfile.TemporaryDirectory() as cwd:
            env={'HOME':os.environ['HOME'],'PATH':'/usr/bin:/bin','PLUGIN_ROOT':str(root)}
            async with stdio_client(StdioServerParameters(command=str(command),args=config.get('args',[]),env=env,cwd=cwd)) as streams:
                async with ClientSession(*streams) as client:
                    await client.initialize()
                    self.assertIn('buddy_start',{t.name for t in (await client.list_tools()).tools})
