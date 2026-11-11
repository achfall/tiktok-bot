"""Simulasi suspend, jaringan pulih, dan batas aman sebelum/sesudah submit."""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from wake_recovery import WakeMonitor, WakeInterrupted, run_with_wake_recovery
import daily_orchestrator as bot


class WakeRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.monitor = WakeMonitor(clock=lambda: self.now, sleep=self.advance)

    def advance(self, seconds):
        self.now += seconds

    def wake(self):
        self.advance(3600)
        self.monitor.sample()

    def test_deadline_includes_time_asleep(self):
        sleeps = []
        def sleep(seconds):
            sleeps.append(seconds)
            self.advance(7200)
        self.monitor.sleep = sleep
        self.monitor.wait_until(3600)
        self.assertEqual(sleeps, [1])
        self.assertEqual(self.monitor.generation, 1)

    def test_normal_long_upload_is_not_sleep_with_heartbeat(self):
        for _ in range(120):
            self.advance(1)
            self.monitor.sample()
        self.assertEqual(self.monitor.generation, 0)

    def test_pre_submit_wake_retries_same_operation_once(self):
        before = Mock(); reset = Mock(); calls = []
        def upload(submit):
            calls.append('same-video')
            if len(calls) == 1:
                self.wake()
                self.monitor.check()
            submit()
            return True, None
        with patch.object(self.monitor, 'wait_for_network') as network:
            result = run_with_wake_recovery(self.monitor, upload, before, reset)
        self.assertEqual(result, (True, None))
        self.assertEqual(calls, ['same-video', 'same-video'])
        before.assert_called_once(); reset.assert_called_once(); network.assert_called_once()
        self.assertIsNone(self.monitor.armed)

    def test_after_submit_wake_never_retries(self):
        before = Mock(); reset = Mock()
        def upload(submit):
            submit(); self.wake()
            return False, 'hasil belum pasti'
        with patch.object(self.monitor, 'wait_for_network') as network:
            self.assertEqual(run_with_wake_recovery(self.monitor, upload, before, reset), (False, 'hasil belum pasti'))
        before.assert_called_once(); reset.assert_not_called(); network.assert_not_called()

    def test_verified_success_after_wake_is_preserved(self):
        reset = Mock()
        def upload(submit):
            submit(); self.wake()
            return True, None
        self.assertEqual(run_with_wake_recovery(self.monitor, upload, Mock(), reset), (True, None))
        reset.assert_not_called()

    def test_ordinary_error_does_not_restart(self):
        upload = Mock(return_value=(False, 'selector gagal')); reset = Mock()
        self.assertEqual(run_with_wake_recovery(self.monitor, upload, Mock(), reset), (False, 'selector gagal'))
        upload.assert_called_once(); reset.assert_not_called()

    def test_repeated_wakes_have_bounded_pre_submit_retries(self):
        calls=[];reset=Mock();before=Mock()
        def upload(submit):
            calls.append(1);self.wake();return False,'wake'
        with patch.object(self.monitor,'wait_for_network'):
            success,error=run_with_wake_recovery(self.monitor,upload,before,reset,max_recoveries=2)
        self.assertFalse(success)
        self.assertIn('batas pemulihan',error)
        self.assertEqual(len(calls),3)
        self.assertEqual(reset.call_count,2)
        before.assert_not_called()

    def test_wake_before_submit_callback_does_not_write_submission(self):
        before = Mock(); calls = []
        def upload(submit):
            calls.append(1)
            if len(calls) == 1:
                self.wake()
                submit()
            return False, 'ordinary failure'
        with patch.object(self.monitor, 'wait_for_network'):
            run_with_wake_recovery(self.monitor, upload, before, Mock())
        before.assert_not_called()
        self.assertEqual(len(calls), 2)

    def test_network_waits_until_connection_returns(self):
        connection = Mock(); connection.__enter__ = Mock(); connection.__exit__ = Mock()
        with patch('wake_recovery.socket.create_connection', side_effect=[OSError('offline'), connection]) as connect:
            self.monitor.wait_for_network()
        self.assertEqual(connect.call_count, 2)
        self.assertEqual(self.now, 5)

    def test_cdp_never_sends_stale_action_after_wake(self):
        self.monitor.armed = 0
        self.wake(); ws = Mock()
        with patch.object(bot, 'WAKE_MONITOR', self.monitor):
            with self.assertRaises(WakeInterrupted):
                bot.send_cdp(ws, 'Input.dispatchMouseEvent')
        ws.send.assert_not_called()

    def test_cdp_rejects_reply_received_after_wake(self):
        self.monitor.armed = 0; ws = Mock()
        def reply():
            self.wake()
            return '{"id": 1, "result": {}}'
        ws.recv.side_effect = reply
        with patch.object(bot, 'WAKE_MONITOR', self.monitor):
            with self.assertRaises(WakeInterrupted):
                bot.send_cdp(ws, 'Runtime.evaluate')
        ws.send.assert_called_once()


if __name__ == '__main__':
    unittest.main()
