"""Slot lintas proses dan launcher memakai simulator, tanpa membuka Chrome."""
from contextlib import redirect_stdout
import io
import json
import multiprocessing
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


class BrowserLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name)

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

    def test_browser_left_for_inspection_keeps_slot_until_closed(self):
        with lifecycle.browser_slot(self.base,'akun1',9221,1):pass
        with patch.object(lifecycle,'browser_ready',return_value=True),patch.object(lifecycle.time,'sleep',side_effect=KeyboardInterrupt),redirect_stdout(io.StringIO()):
            with self.assertRaises(KeyboardInterrupt):
                with lifecycle.browser_slot(self.base,'akun2',9222,1):self.fail('Busy browser slot reused')
        with patch.object(lifecycle,'browser_ready',return_value=False):
            with lifecycle.browser_slot(self.base,'akun2',9222,1):pass
        self.assertEqual(json.loads((self.base/'db/browser-slots/0.json').read_text())['account'],'akun2')

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


if __name__=='__main__':unittest.main()
