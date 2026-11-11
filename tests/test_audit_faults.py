"""Audit regresi: tidak ada koneksi TikTok, provider AI, atau upload nyata."""
from contextlib import closing, redirect_stdout, nullcontext
from datetime import timedelta
import io
import json
import multiprocessing
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'scripts'))
import daily_orchestrator as bot
import caption_generator as captions
import schedule_store as schedules
from bot_settings import defaults, validate_values, effective_settings
from botapp.service import ControlService
from botapp.worker import stop_child
from botapp.server import schedule_tick_safely
from wake_recovery import WakeMonitor
from media_limits import validate_video, MAX_VIDEO_BYTES
from account_sources import save_ids
import content_queue as queue


def write_account_settings(base, account, barrier, limit):
    service=ControlService(base)
    barrier.wait()
    service.save_settings({'account':account,'values':{'max_per_day':limit}})


class ProtocolFaultTests(unittest.TestCase):
    def test_cdp_error_and_javascript_exception_stop_pipeline(self):
        ws=Mock();ws.recv.return_value=json.dumps({'id':1,'error':{'message':'Method not found'}})
        with self.assertRaises(bot.StepFailed):bot.send_cdp(ws,'Input.enable')
        with patch.object(bot,'send_cdp',return_value={'result':{'exceptionDetails':{'text':'boom'}}}),self.assertRaises(bot.StepFailed):
            bot.eval_js(None,'broken()',1)

    def test_event_flood_cannot_wait_forever_for_cdp_reply(self):
        ws=Mock();ws.recv.return_value=json.dumps({'method':'Page.event'})
        with patch.object(bot.time,'monotonic',side_effect=[0,0,31]),self.assertRaises(bot.StepFailed):
            bot.send_cdp(ws,'Page.enable')
        self.assertEqual(ws.recv.call_count,1)

    def test_connection_failure_closes_only_new_tab_with_timeout(self):
        response=Mock();response.json.return_value={'id':'owned','webSocketDebuggerUrl':'ws://127.0.0.1:9221/devtools/page/owned'}
        with patch.object(bot.requests,'put',return_value=response),patch.object(bot.websocket,'create_connection',side_effect=OSError('disconnected')),patch.object(bot.requests,'get') as close:
            success,_=bot.post_one_video(9221,'unused.mp4','caption','11111','auto')
        self.assertFalse(success)
        close.assert_called_once_with('http://127.0.0.1:9221/json/close/owned',timeout=3)

    def test_wrong_endpoint_is_rejected_before_connection(self):
        response=Mock();response.json.return_value={'id':'owned','webSocketDebuggerUrl':'ws://external.invalid/devtools/page/owned'}
        with patch.object(bot.requests,'put',return_value=response),patch.object(bot.websocket,'create_connection') as connect,patch.object(bot.requests,'get'):
            success,_=bot.post_one_video(9221,'unused.mp4','caption','11111','auto')
        self.assertFalse(success);connect.assert_not_called()

    def test_keyboard_interrupt_closes_websocket(self):
        response=Mock();response.json.return_value={'id':'owned','webSocketDebuggerUrl':'ws://127.0.0.1:9221/devtools/page/owned'}
        ws=Mock()
        with patch.object(bot.requests,'put',return_value=response),patch.object(bot.websocket,'create_connection',return_value=ws),patch.object(bot,'send_cdp',side_effect=KeyboardInterrupt),self.assertRaises(KeyboardInterrupt):
            bot.post_one_video(9221,'unused.mp4','caption','11111','auto')
        ws.close.assert_called_once()

    def test_child_shutdown_waits_and_escalates_only_after_timeout(self):
        child=Mock();child.poll.return_value=None
        child.wait.side_effect=[subprocess.TimeoutExpired('child',12),0]
        stop_child(child)
        child.send_signal.assert_called_once_with(signal.SIGINT)
        child.kill.assert_called_once()
        self.assertEqual(child.wait.call_args_list[0].kwargs,{'timeout':12})
        self.assertEqual(child.wait.call_args_list[1].kwargs,{'timeout':5})

    def test_orphan_watchdog_interrupts_own_process_only(self):
        monitor=WakeMonitor(supervisor_pid=123)
        with patch('wake_recovery.os.getppid',return_value=1),patch('wake_recovery.os.getpid',return_value=77),patch('wake_recovery.os.kill') as kill,redirect_stdout(io.StringIO()):
            self.assertFalse(monitor.check_parent())
        kill.assert_called_once_with(77,signal.SIGINT)
        self.assertTrue(monitor.stopped.is_set())

    def test_non_finite_settings_are_rejected(self):
        for value in (float('nan'),float('inf'),float('-inf'),10**1000):
            with self.assertRaises(ValueError):validate_values({'max_per_day':value})

    def test_scheduler_recovers_after_database_error(self):
        service=Mock();service.schedule_tick.side_effect=[sqlite3.OperationalError('SENTINEL'),None]
        self.assertEqual(schedule_tick_safely(service),'OperationalError')
        self.assertIsNone(schedule_tick_safely(service))

    def test_caption_errors_do_not_log_secrets_and_duplicates_stop(self):
        output=io.StringIO()
        with patch.object(captions,'get_recent_captions',return_value=[]),patch.object(captions,'call_gemini',side_effect=RuntimeError('SENTINEL_SECRET')),patch.object(captions,'call_deepseek',return_value='fresh'),redirect_stdout(output):
            self.assertEqual(captions.generate_unique_caption(None,1),'fresh')
        self.assertNotIn('SENTINEL_SECRET',output.getvalue())
        with patch.object(captions,'get_recent_captions',return_value=['same']),patch.object(captions,'call_gemini',return_value='same'),redirect_stdout(io.StringIO()):
            self.assertIsNone(captions.generate_unique_caption(None,1))

    def test_legacy_upload_entrypoints_are_disabled_without_db_or_browser(self):
        with tempfile.TemporaryDirectory() as root:
            for name in ('full_test_post.py','test_save_draft.py'):
                result=subprocess.run([sys.executable,str(ROOT/'scripts'/name),'akun1'],cwd=root,capture_output=True,text=True)
                self.assertNotEqual(result.returncode,0)
                self.assertIn('dinonaktifkan',result.stderr)

    def test_launchd_guard_and_runner_delegate_without_preopening_chrome(self):
        with tempfile.TemporaryDirectory() as root:
            base=Path(root);(base/'config').mkdir()
            stub=base/'stub-python'
            stub.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n');stub.chmod(0o700)
            env={**os.environ,'TIKTOK_BOT_HOME':root,'TIKTOK_BOT_PYTHON':str(stub)}
            command=['bash',str(ROOT/'scripts/launchd_runner.sh'),'akun1','9221']
            denied=subprocess.run(command,env=env,capture_output=True,text=True)
            self.assertNotEqual(denied.returncode,0)
            self.assertIn('Posting belum diaktifkan',denied.stderr)
            (base/'config/allow-posting-akun1').touch()
            allowed=subprocess.run(command,env=env,capture_output=True,text=True)
            self.assertEqual(allowed.returncode,0,allowed.stderr)
            self.assertIn('daily_orchestrator.py',allowed.stdout)
            self.assertNotIn('start-chrome-profile',allowed.stdout)

    def test_obsolete_boot_launcher_never_starts_anything(self):
        result=subprocess.run(['bash',str(ROOT/'scripts/boot_start_all.sh')],capture_output=True,text=True,timeout=2)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('dinonaktifkan',result.stderr)


class PipelineFaultTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name)
        self.service=ControlService(self.base)
        self.service.create_account({'name':'audit.fixture'})
        self.folder=self.base/'content/audit.fixture'
        save_ids(self.folder,'11111')
        (self.folder/'video.mp4').write_bytes(b'fixture')
        self.db=self.base/'db/tiktok_bot.db'
        self.conn=sqlite3.connect(self.db);self.addCleanup(self.conn.close)
        queue.scan_account(self.conn,1)

    def run_pipeline(self, owned=True, success=True):
        def upload(port,path,caption,product,mode,before_submit=None,**kwargs):
            if success:before_submit()
            return success,None if success else 'simulated failure'
        with patch.object(bot,'DB_PATH',self.db),patch.object(sys,'argv',['bot','akun1','--publish','--limit','1']),patch.object(bot,'generate_unique_caption',return_value='caption'),patch.object(bot,'post_one_video',side_effect=upload),patch.object(bot,'prepare_browser',return_value=owned),patch.object(bot,'browser_slot',return_value=nullcontext()),patch.object(bot,'close_account_browser',return_value=True) as close,patch.object(bot,'wait_for_window'),redirect_stdout(io.StringIO()):
            result=bot.main()
        return result,close

    def test_manual_chrome_is_not_closed_on_success(self):
        result,close=self.run_pipeline(owned=False)
        self.assertTrue(result);close.assert_not_called()
        self.assertEqual(self.conn.execute('SELECT status FROM posts_log').fetchone()[0],'draft')

    def test_bot_created_chrome_is_closed_on_success(self):
        result,close=self.run_pipeline(owned=True)
        self.assertTrue(result);close.assert_called_once_with(9221)

    def test_pipeline_failure_returns_failed_outcome_and_releases_pre_submit_claim(self):
        result,close=self.run_pipeline(success=False)
        self.assertFalse(result);close.assert_called_once_with(9221)
        self.assertEqual(self.conn.execute('SELECT status FROM contents').fetchone()[0],'unused')

    def test_failed_manual_chrome_is_preserved(self):
        result,close=self.run_pipeline(owned=False,success=False)
        self.assertFalse(result);close.assert_not_called()

    def test_schedule_using_manual_chrome_is_preserved(self):
        self.service.update_account({'account':'akun1','mode':'schedule'})
        def upload(port,path,caption,product,mode,before_submit=None,schedule_context=None,**kwargs):
            schedule_context['reserve']()
            before_submit()
            return True,None
        with patch.object(bot,'DB_PATH',self.db),patch.object(sys,'argv',['bot','akun1','--publish','--limit','1']),patch.object(bot,'generate_unique_caption',return_value='caption'),patch.object(bot,'post_one_video',side_effect=upload),patch.object(bot,'prepare_browser',return_value=False),patch.object(bot,'browser_slot',return_value=nullcontext()),patch.object(bot,'close_account_browser') as close,patch.object(bot,'wait_for_window'),redirect_stdout(io.StringIO()):
            self.assertTrue(bot.main())
        close.assert_not_called()

    def test_invalid_video_stops_before_caption_or_chrome(self):
        (self.folder/'video.mp4').write_bytes(b'')
        with patch.object(bot,'DB_PATH',self.db),patch.object(sys,'argv',['bot','akun1','--publish']),patch.object(bot,'generate_unique_caption') as caption,patch.object(bot,'prepare_browser') as chrome,patch.object(bot,'wait_for_window'),redirect_stdout(io.StringIO()):
            self.assertFalse(bot.main())
        caption.assert_not_called();chrome.assert_not_called()

    def test_inactive_account_cannot_bypass_panel_by_cli(self):
        self.conn.execute('UPDATE accounts SET active=0');self.conn.commit()
        with patch.object(bot,'DB_PATH',self.db),patch.object(sys,'argv',['bot','akun1','--publish']),patch.object(bot,'prepare_browser') as chrome,self.assertRaises(SystemExit):
            bot.main()
        chrome.assert_not_called()

    def test_null_legacy_product_log_does_not_hide_all_products(self):
        self.conn.execute("INSERT INTO posts_log(account_id,status,published_at) VALUES(1,'success',datetime('now'))");self.conn.commit()
        self.assertEqual(len(bot.get_active_products(self.conn.cursor(),1)),1)

    def test_completed_batch_accepts_new_settings_without_resetting_slots(self):
        settings=defaults()
        batch=schedules.batch_for(self.conn,1,settings)
        schedules.finish_batch(self.conn,batch)
        updated=schedules.batch_for(self.conn,1,{**settings,'schedule_start_hour':23,'schedule_interval_minutes':45})
        self.assertEqual(updated['id'],batch['id'])
        self.assertEqual(updated['interval_minutes'],45)
        self.assertIn('T23:',updated['first_at'])

    def test_media_size_and_symlink_preflight(self):
        path=self.folder/'video.mp4'
        validate_video(path)
        with path.open('wb') as stream:stream.truncate(MAX_VIDEO_BYTES+1)
        with self.assertRaises(ValueError):validate_video(path)
        link=self.folder/'link.mp4';link.symlink_to(path)
        with self.assertRaises(ValueError):validate_video(link)

    def test_config_save_failure_does_not_change_publish_mode(self):
        with patch('botapp.service.save_document',side_effect=OSError('disk full')),patch.object(self.service,'command') as command,self.assertRaises(OSError):
            self.service.update_account({'account':'akun1','mode':'auto','values':{'max_per_day':2}})
        command.assert_not_called()
        self.assertEqual(self.service.account('akun1')['publish_mode'],'manual_review')

    def test_cli_failure_restores_settings(self):
        self.service.save_settings({'account':'akun1','values':{'max_per_day':3}})
        before=effective_settings(self.base,'akun1')
        with patch.object(self.service,'command',side_effect=ValueError('CLI failed')),self.assertRaises(ValueError):
            self.service.update_account({'account':'akun1','mode':'auto','values':{'max_per_day':2}})
        self.assertEqual(effective_settings(self.base,'akun1'),before)

    def test_other_processes_do_not_lose_account_setting_updates(self):
        self.service.create_account({'name':'second.fixture'})
        ctx=multiprocessing.get_context('spawn');barrier=ctx.Barrier(2)
        processes=[ctx.Process(target=write_account_settings,args=(self.base,'akun'+str(i),barrier,i+1)) for i in (1,2)]
        for process in processes:process.start()
        for process in processes:
            process.join(timeout=15)
            if process.is_alive():process.terminate();process.join();self.fail('Config worker stuck')
            self.assertEqual(process.exitcode,0)
        self.assertEqual(effective_settings(self.base,'akun1')['max_per_day'],2)
        self.assertEqual(effective_settings(self.base,'akun2')['max_per_day'],3)

    def test_history_on_plain_connection_and_wal_schedule_guard(self):
        batch=schedules.batch_for(self.conn,1,defaults())
        claim=queue.claim_video(self.conn,1,1)
        schedules.reserve(self.conn,claim,batch)
        queue.begin_submission(self.conn,claim,'caption',quota_day=batch['quota_day'],scheduled=True)
        queue.finish_success(self.conn,claim,'schedule')
        with closing(sqlite3.connect(self.db)) as plain:
            self.assertEqual(len(schedules.history_metadata(plain)),1)
        self.conn.execute('PRAGMA main.journal_mode=WAL')
        with self.assertRaises(ValueError):schedules.batch_for(self.conn,1,defaults())

    def test_real_worker_records_preflight_failure_as_failed(self):
        (self.folder/'video.mp4').write_bytes(b'')
        self.service.save_settings({'account':'akun1','values':{'window_start_hour':0}})
        result=self.service.start({'account':'akun1','kind':'configured','limit':1})
        deadline=time.monotonic()+12
        while time.monotonic()<deadline:
            run=next(r for r in self.service.jobs.list() if r['id']==result['run_id'])
            if run['status'] not in ('queued','running','stopping'):break
            time.sleep(.1)
        self.assertEqual(run['status'],'failed',self.service.jobs.logs(result['run_id']))
        self.assertNotEqual(run['exit_code'],0)


if __name__=='__main__':unittest.main()
