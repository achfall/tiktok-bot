"""Quit terarah tanpa Chrome nyata atau signal ke proses pengguna."""
import json
from pathlib import Path
import signal
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import browser_process as processes
import browser_lifecycle as lifecycle
import daily_orchestrator as bot

TOKEN='Wed Nov 12 01:00:00 2026 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
IDENTITY={'pid':123,'token':TOKEN,'endpoint':'ws://127.0.0.1:9477/devtools/browser/owned'}


class BrowserQuitTests(unittest.TestCase):
    def test_graceful_quit_verifies_process_exit_without_signal(self):
        with patch.object(processes,'process_token',return_value=''),patch.object(processes.os,'kill') as kill:
            self.assertTrue(processes.wait_for_quit(9477,IDENTITY,lambda port:False))
        kill.assert_not_called()

    def test_lingering_owned_browser_receives_sigterm_only(self):
        with patch.object(processes,'process_token',side_effect=[TOKEN]*22+['']),patch.object(processes.os,'kill') as kill,patch.object(processes.time,'sleep'):
            self.assertTrue(processes.wait_for_quit(9477,IDENTITY,lambda port:False))
        kill.assert_called_once_with(123,signal.SIGTERM)

    def test_reused_pid_is_never_signalled(self):
        with patch.object(processes,'process_token',return_value='new process identity'),patch.object(processes.os,'kill') as kill:
            self.assertTrue(processes.wait_for_quit(9477,IDENTITY,lambda port:False))
        kill.assert_not_called()

    def test_unknown_process_state_never_sends_signal_or_claims_quit(self):
        with patch.object(processes,'process_token',return_value=None),patch.object(processes.os,'kill') as kill,patch.object(processes.time,'sleep'):
            self.assertFalse(processes.wait_for_quit(9477,IDENTITY,lambda port:False))
        kill.assert_not_called()

    def test_invalid_pid_is_never_signalled(self):
        with patch.object(processes.os,'kill') as kill:
            for pid in (-1,0,1,True,None):
                self.assertFalse(processes.wait_for_quit(9477,{**IDENTITY,'pid':pid},lambda port:False))
        kill.assert_not_called()

    def test_capture_selects_browser_pid_not_renderer(self):
        ws=Mock();ws.recv.return_value=json.dumps({'id':1,'result':{'processInfo':[{'type':'renderer','id':456},{'type':'browser','id':123}]}})
        response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock()
        with patch.object(processes.urllib.request,'urlopen',return_value=response),patch.object(processes.json,'load',return_value={'webSocketDebuggerUrl':IDENTITY['endpoint']}),patch.object(processes.websocket,'create_connection',return_value=ws),patch.object(processes,'process_token',return_value=TOKEN):
            self.assertEqual(processes.capture_process(9477),IDENTITY)
        ws.close.assert_called_once()

    def test_close_uses_owned_process_verification(self):
        response=Mock();response.json.return_value={'webSocketDebuggerUrl':IDENTITY['endpoint']}
        ws=Mock()
        with patch.dict(lifecycle.OWNED_PROCESSES,{9477:IDENTITY},clear=True),patch.object(bot.requests,'get',return_value=response),patch.object(bot.websocket,'create_connection',return_value=ws),patch.object(bot,'wait_for_quit',return_value=True) as wait:
            self.assertTrue(bot.close_account_browser(9477))
            self.assertNotIn(9477,lifecycle.OWNED_PROCESSES)
        wait.assert_called_once_with(9477,IDENTITY,bot.browser_ready)

    def test_changed_browser_instance_is_not_closed(self):
        response=Mock();response.json.return_value={'webSocketDebuggerUrl':IDENTITY['endpoint']+'-changed'}
        ws=Mock()
        with patch.dict(lifecycle.OWNED_PROCESSES,{9477:IDENTITY},clear=True),patch.object(bot.requests,'get',return_value=response),patch.object(bot.websocket,'create_connection',return_value=ws),patch.object(bot,'wait_for_quit') as wait:
            self.assertFalse(bot.close_account_browser(9477))
        ws.send.assert_not_called();wait.assert_not_called()


if __name__=='__main__':unittest.main()
