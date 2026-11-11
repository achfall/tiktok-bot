"""E2E Diagnostik: fixture nyata, upload/CDP disimulasikan, tanpa TikTok."""
from contextlib import ExitStack, nullcontext, redirect_stdout, closing
from datetime import timedelta
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'scripts')]
import daily_orchestrator as bot
import content_queue as queue
import schedule_store as store
from diagnostic_options import validate_options,cli_options
from account_sources import save_ids
from botapp.service import ControlService
from botapp.worker import run_command


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.service=ControlService(self.base)
        self.service.create_account({'name':'diagnostic.fixture'})
        self.folder=self.base/'content/diagnostic.fixture'
        save_ids(self.folder,'11111')
        (self.folder/'original.mp4').write_bytes(b'video fixture')
        self.service.scan({'account':'akun1'})
        self.db=self.base/'db/tiktok_bot.db'
        self.conn=sqlite3.connect(self.db);self.addCleanup(self.conn.close)
        self.target=(store.now_wib()+timedelta(hours=2)).replace(minute=0,second=0,microsecond=0)

    def options(self,mode='schedule',music='on'):
        return {'mode':mode,'music':music,**({'scheduled_at':self.target.isoformat()} if mode=='schedule' else {})}

    def simulate(self,options,fail=False,late=False):
        calls=[]
        def upload(port,path,caption,product,mode,before_submit=None,**kw):
            calls.append((mode,path,kw.get('music_context')))
            self.assertNotEqual(Path(path),self.folder/'original.mp4')
            if mode=='schedule':
                target=kw['schedule_context']['reserve']()
                self.assertEqual(target,self.target)
                # Repeated reserve during wake recovery retains the same slot.
                self.assertEqual(kw['schedule_context']['reserve'](),target)
            if late:
                raise bot.StepFailed('before submit failure')
            before_submit()
            return (False,'unknown result') if fail else (True,None)
        args=['bot','akun1','--publish','--retest-content','1','--test-options',json.dumps(options)]
        with ExitStack() as stack:
            for name,value in [('DB_PATH',self.db),('generate_unique_caption',lambda *a:'fixture caption'),('post_one_video',upload),('prepare_browser',lambda *a:True),('browser_slot',lambda *a:nullcontext()),('close_account_browser',lambda *a:True)]:
                stack.enter_context(patch.object(bot,name,value))
            stack.enter_context(patch.object(sys,'argv',args))
            stack.enter_context(redirect_stdout(io.StringIO()))
            result=bot.main()
        return result,calls

    def test_validation_and_cli_reject_bad_options_before_side_effects(self):
        invalid=[None,[],{'mode':'bogus'},{'music':True},{'mode':'auto','scheduled_at':self.target.isoformat()},{'mode':'schedule'},{'mode':'schedule','scheduled_at':'bad'},{'mode':'schedule','scheduled_at':(store.now_wib()+timedelta(minutes=5)).isoformat()},{'mode':'schedule','scheduled_at':(self.target+timedelta(days=11)).isoformat()},{'mode':'schedule','scheduled_at':self.target.replace(minute=3).isoformat()},{'max_per_day':1000}]
        for options in invalid:
            with self.subTest(options=options),self.assertRaises(ValueError):validate_options(options)
        for args in [['--test-options','{}'],['--publish','--retest-content','1','--test-options'],['--publish','--retest-content','1','--test-options','{}','--test-draft']]:
            with self.assertRaises(ValueError):cli_options(args)
        naive=self.target.replace(tzinfo=None).isoformat(timespec='minutes')
        self.assertEqual(validate_options({'mode':'schedule','scheduled_at':naive})['scheduled_at'],self.target.isoformat())

    def test_api_confirmation_options_persist_and_worker_uses_same_options(self):
        request={'account':'akun1','kind':'e2e_test','content_id':1,'test_options':self.options()}
        with patch('botapp.jobs.subprocess.Popen') as process:
            process.return_value.poll.return_value=None
            with self.assertRaises(ValueError):self.service.start(request)
            self.assertEqual(self.service.jobs.list(),[])
            self.service.start({**request,'confirmed':True})
            process.assert_called_once()
        row=self.service.jobs.list()[0]
        with closing(self.service.jobs.connect()) as conn:
            options=json.loads(conn.execute('SELECT options_json FROM run_test_options').fetchone()[0])
        command=run_command(row,options)
        self.assertEqual(json.loads(command[-1]),self.options())
        self.assertEqual(command[-4:-2],['--retest-content','1'])
        self.assertEqual(self.service.account('akun1')['publish_mode'],'manual_review')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM contents').fetchone()[0],1)
        with self.assertRaises(ValueError):run_command(row,None)

    def test_each_mode_and_music_override_runs_one_copy_preserving_account(self):
        for mode,expected in [('manual_review','manual'),('auto','auto'),('schedule','schedule')]:
            with self.subTest(mode=mode):
                result,calls=self.simulate(self.options(mode,'on'))
                self.assertTrue(result);self.assertEqual(len(calls),1)
                self.assertEqual(calls[0][0],expected);self.assertIsNotNone(calls[0][2])
        self.assertEqual(self.service.account('akun1')['publish_mode'],'manual_review')
        self.assertFalse(self.service.state()['accounts'][0]['settings']['music_enabled'])
        self.assertEqual((self.folder/'original.mp4').read_bytes(),b'video fixture')
        self.assertEqual(self.conn.execute('SELECT status FROM contents WHERE id=1').fetchone()[0],'unused')
        self.assertEqual([r[0] for r in self.conn.execute('SELECT status FROM posts_log ORDER BY id')],['draft','success','scheduled'])
        self.assertEqual(queue.daily_post_count(self.conn,1),3)
        self.assertEqual(self.service.state()['history'][0]['scheduled_at'],self.target.isoformat())

    def test_music_off_overrides_enabled_and_account_inherits(self):
        self.service.update_account({'account':'akun1','mode':'auto','values':{'music_enabled':True}})
        for music,enabled in [('off',False),('account',True)]:
            result,calls=self.simulate(self.options('manual_review',music))
            self.assertTrue(result);self.assertEqual(calls[0][2] is not None,enabled)
        self.assertEqual(self.service.account('akun1')['publish_mode'],'auto')
        self.assertTrue(self.service.state()['accounts'][0]['settings']['music_enabled'])

    def test_schedule_test_does_not_modify_open_automation_batch(self):
        batch=store.batch_for(self.conn,1,self.service.state()['accounts'][0]['settings'])
        before=self.conn.execute('SELECT * FROM schedule_meta.batches').fetchall()
        result,_=self.simulate(self.options())
        self.assertTrue(result)
        self.assertEqual(self.conn.execute('SELECT * FROM schedule_meta.batches').fetchall(),before)
        self.assertEqual(store.batch_for(self.conn,1,self.service.state()['accounts'][0]['settings']),batch)

    def test_unknown_submit_is_held_counted_and_cannot_restart(self):
        result,calls=self.simulate(self.options(),fail=True)
        self.assertFalse(result);self.assertEqual(len(calls),1)
        self.assertEqual(self.conn.execute('SELECT status FROM posts_log').fetchone()[0],'uncertain')
        self.assertEqual(queue.daily_post_count(self.conn,1),1)
        with self.assertRaises(ValueError):
            self.service.start({'account':'akun1','kind':'e2e_test','content_id':1,'test_options':self.options(),'confirmed':True})

    def test_quota_exhausted_does_not_copy_upload_or_open_browser(self):
        self.service.update_account({'account':'akun1','mode':'manual_review','values':{'max_per_day':1}})
        self.simulate(self.options('manual_review'))
        count=self.conn.execute('SELECT COUNT(*) FROM contents').fetchone()[0]
        result,calls=self.simulate(self.options('auto'))
        self.assertTrue(result);self.assertEqual(calls,[])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM contents').fetchone()[0],count)

    def test_duplicate_exact_schedule_is_rejected_without_submit(self):
        self.assertTrue(self.simulate(self.options())[0])
        result,calls=self.simulate(self.options())
        self.assertFalse(result)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM posts_log WHERE status='scheduled'").fetchone()[0],1)
        self.assertEqual(queue.daily_post_count(self.conn,1),1)

    def test_upload_delay_does_not_shift_or_submit_expired_schedule(self):
        original=store.reserve_test
        def delayed(conn,claim,target):
            return original(conn,claim,target,now=target-timedelta(minutes=10))
        with patch.object(store,'reserve_test',side_effect=delayed):
            result,calls=self.simulate(self.options())
        self.assertFalse(result);self.assertEqual(len(calls),1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM posts_log').fetchone()[0],1)
        self.assertEqual(self.conn.execute('SELECT status FROM posts_log').fetchone()[0],'failed')
        self.assertEqual(queue.daily_post_count(self.conn,1),0)
        self.assertEqual(self.service.state()['history'][0].get('scheduled_at'),None)


if __name__=='__main__':unittest.main()
