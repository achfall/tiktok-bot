"""Konfigurasi launchd panel harus terpisah dari worker posting."""
from pathlib import Path
import plistlib
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from prepare_panel_launchd import panel_agent


class PanelAutostartTests(unittest.TestCase):
    def test_agent_starts_only_panel_and_survives_exit(self):
        root=Path('/tmp/fixture ruangkonten').resolve()
        agent=plistlib.loads(plistlib.dumps(panel_agent(root)))
        self.assertEqual(agent['ProgramArguments'],[str(root/'venv/bin/python'),str(root/'scripts/start_panel.py')])
        self.assertTrue(agent['RunAtLoad'])
        self.assertTrue(agent['KeepAlive'])
        self.assertEqual(agent['EnvironmentVariables']['TIKTOK_BOT_HOME'],str(root))
        self.assertNotIn('StartCalendarInterval',agent)
        self.assertEqual(agent['WorkingDirectory'],str(root))
