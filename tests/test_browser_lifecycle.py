"""Slot lintas proses dan launcher memakai simulator, tanpa membuka Chrome."""
from contextlib import redirect_stdout
import io
import fcntl
import json
import multiprocessing
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import browser_lifecycle as lifecycle


def occupy_slot(base, index, active, peak, counter_lock):
    with patch.object(lifecycle,'browser_ready',return_value=False),redirect_stdout(io.StringIO()):
        with lifecycle.browser_slot(base,f'akun{index}',9300+index,2):
            with counter_lock:
                active.value+=1
                peak.value=max(peak.value,active.value)
            time.sleep(.08)
            with counter_lock:
                active.value-=1


def abandon_slot(base):
    with lifecycle.browser_slot(base,'akun1',9221,1):
        os._exit(0)


class BrowserLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name)
        capture=patch('browser_process.capture_process',return_value=None)
        capture.start();self.addCleanup(capture.stop)

    def test_independent_processes_obey_global_cap(self):
        context=multiprocessing.get_context('spawn')
        active=context.Value('i',0);peak=context.Value('i',0);lock=context.Lock()
        workers=[context.Process(target=occupy_slot,args=(self.base,index,active,peak,lock)) for index in range(6)]
        for worker in workers:worker.start()
        for worker in workers:
            worker.join(timeout=15)
            if worker.is_alive():worker.terminate();worker.join()
            self.assertEqual(worker.exitcode,0)
        self.assertGreater(peak.value,0)
        self.assertLessEqual(peak.value,2)
        self.assertEqual(active.value,0)

    def test_browser_left_for_inspection_does_not_keep_finished_job_slot(self):
        with lifecycle.browser_slot(self.base,'akun1',9221,1):pass
        with patch.object(lifecycle,'browser_ready',return_value=True) as probe,patch.object(lifecycle.subprocess,'run') as launch:
            with lifecycle.browser_slot(self.base,'akun2',9222,1):
                self.assertEqual(json.loads((self.base/'db/browser-slots/0.json').read_text())['account'],'akun2')
        probe.assert_not_called()
        launch.assert_not_called()
        self.assertEqual(json.loads((self.base/'db/browser-slots/0.json').read_text()),{})

    def test_crashed_worker_stale_metadata_does_not_block_or_touch_user_chrome(self):
        worker=multiprocessing.get_context('spawn').Process(target=abandon_slot,args=(self.base,))
        worker.start();worker.join(timeout=15)
        if worker.is_alive():worker.terminate();worker.join();self.fail('Worker did not exit')
        self.assertEqual(worker.exitcode,0)
        self.assertEqual(json.loads((self.base/'db/browser-slots/0.json').read_text())['account'],'akun1')
        with patch.object(lifecycle,'browser_ready',return_value=True) as probe,patch.object(lifecycle.urllib.request,'urlopen') as network:
            with lifecycle.browser_slot(self.base,'akun2',9222,1):pass
        probe.assert_not_called();network.assert_not_called()

    def test_active_bot_holds_slot_even_if_browser_is_not_open_yet(self):
        with lifecycle.browser_slot(self.base,'akun1',9221,1):
            with patch.object(lifecycle,'browser_ready',return_value=False) as probe,patch.object(lifecycle.time,'sleep',side_effect=KeyboardInterrupt),redirect_stdout(io.StringIO()):
                with self.assertRaises(KeyboardInterrupt):
                    with lifecycle.browser_slot(self.base,'akun2',9222,1):self.fail('Active bot slot reused')
            probe.assert_not_called()

    def test_lowered_cap_counts_active_higher_index_slots(self):
        root=self.base/'db/browser-slots';root.mkdir(parents=True)
        with (root/'9.json').open('a+') as old_worker:
            fcntl.flock(old_worker,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with patch.object(lifecycle.time,'sleep',side_effect=KeyboardInterrupt),redirect_stdout(io.StringIO()):
                with self.assertRaises(KeyboardInterrupt):
                    with lifecycle.browser_slot(self.base,'akun2',9222,1):self.fail('Higher active slot ignored')
        with lifecycle.browser_slot(self.base,'akun2',9222,1):pass

    def test_invalid_stale_metadata_can_be_reused_without_chrome_probe(self):
        root=self.base/'db/browser-slots';root.mkdir(parents=True)
        (root/'0.json').write_text('{interrupted write')
        with patch.object(lifecycle.urllib.request,'urlopen') as network:
            with lifecycle.browser_slot(self.base,'akun2',9222,1):pass
        network.assert_not_called()

    def test_exception_releases_lock_and_same_account_can_reuse_browser(self):
        with self.assertRaises(RuntimeError):
            with lifecycle.browser_slot(self.base,'akun1',9221,1):raise RuntimeError('stopped')
        with patch.object(lifecycle,'browser_ready',return_value=True):
            with lifecycle.browser_slot(self.base,'akun1',9221,1):pass

    def test_prepare_reuses_connected_browser_without_launcher(self):
        with patch.object(lifecycle,'browser_ready',return_value=True),patch.object(lifecycle.subprocess,'run') as launch:
            lifecycle.prepare_browser(self.base,'akun1',9221)
        launch.assert_not_called()

    def test_prepare_waits_for_cdp_and_isolates_subprocess(self):
        with patch.object(lifecycle,'browser_ready',side_effect=[False,False,True]),patch.object(lifecycle.subprocess,'run',return_value=MagicMock(returncode=0)) as launch,patch.object(lifecycle.time,'sleep'),redirect_stdout(io.StringIO()):
            lifecycle.prepare_browser(self.base,'akun3',9223)
        self.assertEqual(launch.call_args.args[0][-2:],['akun3','9223'])
        self.assertEqual(launch.call_args.kwargs['env']['TIKTOK_BOT_HOME'],str(self.base))
        self.assertNotIn('shell',launch.call_args.kwargs)

    def test_prepare_failure_stops_before_upload(self):
        with patch.object(lifecycle,'browser_ready',return_value=False),patch.object(lifecycle.subprocess,'run',return_value=MagicMock(returncode=1)),redirect_stdout(io.StringIO()):
            with self.assertRaises(RuntimeError):lifecycle.prepare_browser(self.base,'akun1',9221)

    def test_connected_nondefault_profile_still_calls_launcher(self):
        settings={'chrome_profile_directory':'Profile 19'}
        with patch('bot_settings.effective_settings',return_value=settings),patch.object(lifecycle,'browser_ready',return_value=True),patch.object(lifecycle.subprocess,'run',return_value=MagicMock(returncode=0)) as launch,redirect_stdout(io.StringIO()):
            lifecycle.prepare_browser(self.base,'akun6',9226)
        self.assertEqual(launch.call_args.args[0][-2:],['akun6','9226'])


if __name__=='__main__':unittest.main()
