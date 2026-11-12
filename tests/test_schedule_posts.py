"""Slot, kuota, restart, batch dan CDP Schedule tanpa jaringan/upload nyata."""
import contextlib
from datetime import datetime, timedelta
import io
import json
from pathlib import Path
import sqlite3
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/'scripts'))
import content_queue as queue
import schedule_store as store
import schedule_ui
import daily_orchestrator as bot
from bot_settings import defaults, validate_values
from account_sources import save_ids
from botapp.service import ControlService
from manage_queue import resolve


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.service = ControlService(self.base)
        self.service.create_account({'name': 'schedule.test'})
        self.folder = self.base/'content/schedule.test'
        save_ids(self.folder, '11111\n22222')
        self.db = self.base/'db/tiktok_bot.db'
        self.conn = sqlite3.connect(self.db)
        self.addCleanup(self.conn.close)
        self.runtime = {**defaults(), 'max_per_day': 12, 'schedule_interval_minutes': 30}
        self.now = datetime(2026, 11, 11, 17, 0, tzinfo=store.WIB)

    def videos(self, count):
        for i in range(count):
            (self.folder/f'{i:03d}.mp4').write_bytes(b'fixture')
        queue.scan_account(self.conn, 1)

    def claim(self):
        pid = self.conn.execute('SELECT id FROM products WHERE account_id=1 ORDER BY sort_order LIMIT 1').fetchone()[0]
        return queue.claim_video(self.conn, 1, pid)

    def batch(self):
        return store.batch_for(self.conn, 1, self.runtime, self.now)

    def test_slots_skip_late_preserve_grid_and_cross_midnight(self):
        first = self.now.replace(hour=17, minute=30)
        self.assertEqual(store.next_slot(first, 30, self.now.replace(hour=17, minute=35)), first+timedelta(minutes=30))
        self.assertEqual(store.next_slot(first, 30, first), first)
        late = first.replace(hour=23, minute=50)
        self.assertEqual(store.next_slot(first, 30, late), first.replace(day=12, hour=0, minute=0))
        with self.assertRaises(ValueError):
            store.next_slot(first, 7, first)

    def test_first_show_is_at_least_35_minutes_rounded_up(self):
        for hour,minute,expected in [(9,3,(9,40)),(9,0,(9,35)),(9,1,(9,40)),(23,30,(0,5)),(23,58,(0,35))]:
            values={**self.runtime,'window_start_hour':hour,'window_start_minute':minute,
                    'schedule_start_hour':0 if hour==23 else hour,'schedule_start_minute':0}
            normalized=validate_values(values)
            self.assertEqual((normalized['schedule_start_hour'],normalized['schedule_start_minute']),expected)
            self.assertEqual(validate_values(normalized),normalized)
        values={**self.runtime,'window_start_hour':9,'window_start_minute':3,
                'schedule_start_hour':9,'schedule_start_minute':35}
        batch=store.batch_for(self.conn,1,values,self.now.replace(hour=9,minute=3))
        self.assertEqual(datetime.fromisoformat(batch['first_at']).strftime('%H:%M'),'09:40')

    def test_first_show_crosses_midnight_without_changing_quota_day(self):
        settings={**self.runtime,'window_start_hour':23,'window_start_minute':30,
                  'schedule_start_hour':0,'schedule_start_minute':0}
        batch=store.batch_for(self.conn,1,settings,self.now.replace(hour=23,minute=30))
        target=datetime.fromisoformat(batch['first_at'])
        self.assertEqual(target,self.now.replace(day=12,hour=0,minute=5))
        self.assertEqual(batch['quota_day'],'2026-11-11')

    def test_config_three_modes_roundtrip_and_invalid_steps_do_not_write(self):
        for mode in ('auto', 'manual_review', 'schedule'):
            self.service.update_account({'account':'akun1','mode':mode,'values':{'schedule_start_hour':17,'schedule_start_minute':30,'schedule_interval_minutes':30}})
            self.assertEqual(self.service.account('akun1')['publish_mode'], mode)
        before = (self.base/'config/control-settings.json').read_bytes()
        for values in ({'schedule_start_minute':31}, {'schedule_interval_minutes':7}, {'schedule_interval_minutes':True}):
            with self.assertRaises(ValueError):
                self.service.update_account({'account':'akun1','mode':'auto','values':values})
        self.assertEqual(before, (self.base/'config/control-settings.json').read_bytes())
        self.assertEqual(self.service.account('akun1')['publish_mode'], 'schedule')
        self.assertEqual(self.service.state()['runs'], [])
        with self.assertRaises(ValueError):
            self.service.start({'account':'akun1','kind':'configured'})

    def test_reservation_restart_and_shift_keep_quota_day(self):
        self.videos(2)
        claim = self.claim()
        batch = self.batch()
        first = store.reserve(self.conn, claim, batch, self.now)
        self.assertEqual(first.strftime('%H:%M'), '18:00')
        self.assertEqual(store.reserve(self.conn, claim, batch, self.now), first)
        with contextlib.closing(sqlite3.connect(self.db)) as restarted:
            resumed = store.batch_for(restarted, 1, self.runtime, self.now+timedelta(days=1))
            self.assertEqual(resumed['quota_day'], '2026-11-11')
            self.assertEqual(resumed['id'], batch['id'])
        queue.begin_submission(self.conn, claim, 'caption', daily_limit=12, quota_day=batch['quota_day'], scheduled=True)
        queue.finish_success(self.conn, claim, 'schedule')
        second = self.claim()
        next_time = store.reserve(self.conn, second, batch, self.now)
        self.assertEqual(next_time, first+timedelta(minutes=30))
        self.assertEqual(queue.daily_post_count(self.conn, 1, '2026-11-11'), 1)
        self.assertEqual(queue.daily_post_count(self.conn, 1, '2026-11-12'), 0)
        history = self.service.state()['history'][0]
        self.assertEqual(history['status'], 'scheduled')
        self.assertEqual(history['scheduled_at'], first.isoformat())
        self.assertEqual(history['quota_day'], batch['quota_day'])

    def test_pre_submit_failure_shifts_same_video_without_consuming_quota(self):
        self.videos(1)
        batch = self.batch()
        c = self.claim()
        first = store.reserve(self.conn, c, batch, self.now)
        queue.finish_failure(self.conn, c, 'caption', 'before submit')
        self.assertEqual(queue.daily_post_count(self.conn, 1, batch['quota_day']), 0)
        c = self.claim()
        shifted = store.reserve(self.conn, c, batch, self.now+timedelta(hours=1))
        self.assertGreater(shifted, first)
        self.assertEqual(shifted.minute, 0)

    def test_shared_quota_and_uncertain_schedule_are_atomic(self):
        self.videos(3)
        today = self.conn.execute("SELECT DATE('now','localtime')").fetchone()[0]
        batch = store.batch_for(self.conn, 1, self.runtime, store.now_wib())
        c = self.claim()
        queue.begin_submission(self.conn, c, 'draft', daily_limit=2)
        queue.finish_success(self.conn, c, 'manual')
        c = self.claim()
        store.reserve(self.conn, c, batch)
        queue.begin_submission(self.conn, c, 'scheduled', daily_limit=2, quota_day=today, scheduled=True)
        queue.finish_failure(self.conn, c, 'scheduled', 'timeout after Schedule')
        self.assertEqual(queue.daily_post_count(self.conn, 1, today), 2)
        with self.assertRaises(ValueError):
            self.claim()
        resolve(self.conn, c.content_id, 'scheduled')
        c = self.claim()
        store.reserve(self.conn, c, batch)
        with self.assertRaises(ValueError):
            queue.begin_submission(self.conn, c, 'over quota', daily_limit=2, quota_day=today, scheduled=True)
        self.assertEqual(self.conn.execute('SELECT status FROM contents WHERE id=?',(c.content_id,)).fetchone()[0], 'claimed')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM posts_log').fetchone()[0], 2)

    def test_missing_slot_rolls_back_log_and_claim(self):
        self.videos(1)
        batch = self.batch()
        c = self.claim()
        with self.assertRaises(ValueError):
            queue.begin_submission(self.conn, c, 'caption', daily_limit=12, quota_day=batch['quota_day'], scheduled=True)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM posts_log').fetchone()[0], 0)
        self.assertEqual(self.conn.execute('SELECT status FROM contents').fetchone()[0], 'claimed')

    def test_batch_uploads_twelve_without_waiting_and_holds_one_browser_slot(self):
        self.videos(14)
        self.service.update_account({'account':'akun1','mode':'schedule','values':{'max_per_day':12,'schedule_start_hour':17,'schedule_start_minute':30,'schedule_interval_minutes':30}})
        uploads = []
        def upload(port, path, caption, product, mode, before_submit=None, schedule_context=None, **kwargs):
            self.assertEqual(mode, 'schedule')
            target = schedule_context['reserve']()
            uploads.append(target)
            before_submit()
            return True, None
        with patch.object(bot, 'DB_PATH', self.db), patch.object(sys,'argv',['bot','akun1','--publish']), patch.object(bot,'generate_unique_caption',return_value='caption'), patch.object(bot,'post_one_video',side_effect=upload), patch.object(bot,'wait_for_window'), patch.object(bot,'wait_until') as wait, patch.object(bot,'prepare_browser',return_value=True), patch.object(bot,'browser_slot',return_value=contextlib.nullcontext()) as slot, patch.object(bot,'close_account_browser',return_value=True) as close, contextlib.redirect_stdout(io.StringIO()):
            bot.main()
            bot.main()
        self.assertEqual(len(uploads), 12)
        self.assertEqual(len(set(uploads)), 12)
        wait.assert_not_called()
        slot.assert_called_once()
        close.assert_called_once()
        self.assertEqual(queue.daily_post_count(self.conn, 1), 12)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM contents WHERE status='unused'").fetchone()[0], 2)

    def test_schedule_redirect_alone_never_verifies_or_clicks_post_now(self):
        context = {'caption':'caption','target':self.now+timedelta(hours=1)}
        with patch.object(bot,'eval_js',side_effect=lambda ws,s,mid: 'https://www.tiktok.com/tiktokstudio/content' if s=='location.href' else False), patch.object(bot.time,'sleep'), patch.object(bot,'confirm_pending_post') as confirm:
            success, _, _ = bot.wait_for_submission(None, 'schedule', 1, attempts=2, schedule_context=context)
        self.assertFalse(success)
        confirm.assert_not_called()

    def test_schedule_never_accepts_draft_redirect(self):
        context = {'caption':'caption','target':self.now+timedelta(hours=1)}
        with patch.object(bot,'eval_js',return_value='https://www.tiktok.com/tiktokstudio/content?tab=draft'), patch.object(bot.time,'sleep'):
            success, _, _ = bot.wait_for_submission(None, 'schedule', 1, attempts=1, schedule_context=context)
        self.assertFalse(success)

    def test_ui_rejects_wrong_timezone_before_click(self):
        calls=[]
        with patch.object(schedule_ui,'now_wib',return_value=self.now):
            with self.assertRaises(ValueError):
                schedule_ui.configure(None, 1, self.now+timedelta(hours=1), lambda *args:'America/New_York', lambda *args:calls.append(args))
        self.assertEqual(calls, [])

    @unittest.skipUnless(sys.platform == 'darwin', 'JavaScriptCore fixture di macOS')
    def test_result_requires_matching_caption_scheduled_label_date_and_time(self):
        target = self.now.replace(hour=21, minute=30)
        expression = schedule_ui.result_expression('Caption unik', target)
        cases = [
            ([['Caption unik','Scheduled Nov 11, 9:30 PM']], True),
            ([['Caption unik','Nov 11, 9:30 PM']], False),
            ([['Caption unik','Scheduled Nov 11, 10:00 PM']], False),
            ([['Caption unik','Scheduled Nov 12, 9:30 PM']], False),
            ([['Caption lainnya','Scheduled Nov 11, 9:30 PM']], False),
            ([['Caption unik','Scheduled Nov 11, 9:30 PM']]*2, False),
            ([['Caption unik','Nov 11, 9:30 PM (GMT+7) Jakarta',True]], True),
            ([['Caption unik','Nov 11, 10:00 PM (GMT+7) Jakarta',True]], False),
            ([['Caption unik','Nov 11, 9:30 PM (GMT+7) Jakarta',False]], False),
            ([['Caption unik','Nov 11, 9:30 PM',True]], False),
        ]
        for rows, expected in cases:
            script = '''function run(){const source=%s;const document={querySelectorAll:()=>source.map(r=>({querySelector:s=>({innerText:s.includes('PostInfoCell_a')?r[0]:r[1],querySelector:()=>r[2]?{}:null})}))};return JSON.stringify(eval(%s));}''' % (json.dumps(rows),json.dumps(expression))
            result = subprocess.run(['/usr/bin/osascript','-l','JavaScript','-e',script],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(result.stdout),expected,rows)

    def test_metadata_history_survives_manual_not_posted_and_retry(self):
        self.videos(1)
        batch = self.batch()
        c = self.claim()
        target = store.reserve(self.conn,c,batch,self.now)
        queue.begin_submission(self.conn,c,'caption',quota_day=batch['quota_day'],scheduled=True)
        queue.finish_failure(self.conn,c,'caption','unknown')
        resolve(self.conn,c.content_id,'not-posted')
        c = self.claim()
        later = store.reserve(self.conn,c,batch,self.now+timedelta(hours=1))
        queue.begin_submission(self.conn,c,'caption',quota_day=batch['quota_day'],scheduled=True)
        queue.finish_success(self.conn,c,'schedule')
        history = self.service.state()['history']
        self.assertEqual([h['status'] for h in history],['scheduled','failed'])
        self.assertEqual([h['scheduled_at'] for h in history],[later.isoformat(),target.isoformat()])

    def test_batch_with_past_midnight_targets_consumes_original_day_quota(self):
        self.videos(2)
        batch = self.batch()
        late = self.now.replace(hour=23,minute=40)
        for _ in range(2):
            c = self.claim()
            target = store.reserve(self.conn,c,batch,late)
            self.assertEqual(target.date(), (late+timedelta(days=1)).date())
            queue.begin_submission(self.conn,c,'caption',daily_limit=2,quota_day=batch['quota_day'],scheduled=True)
            queue.finish_success(self.conn,c,'schedule')
        self.assertEqual(queue.daily_post_count(self.conn,1,'2026-11-11'),2)
        self.assertEqual(queue.daily_post_count(self.conn,1,'2026-11-12'),0)

    def test_draft_test_overrides_schedule_without_allocating_slots(self):
        self.videos(1)
        self.service.update_account({'account':'akun1','mode':'schedule'})
        modes=[]
        def upload(port,path,caption,product,mode,before_submit=None,**kwargs):
            modes.append(mode)
            self.assertNotIn('schedule_context',kwargs)
            before_submit()
            return True,None
        args=['bot','akun1','--publish','--retest-content','1','--test-draft']
        with patch.object(bot,'DB_PATH',self.db),patch.object(sys,'argv',args),patch.object(bot,'generate_unique_caption',return_value='draft caption'),patch.object(bot,'post_one_video',side_effect=upload),patch.object(bot,'prepare_browser',return_value=True),patch.object(bot,'browser_slot',return_value=contextlib.nullcontext()),patch.object(bot,'close_account_browser',return_value=True),contextlib.redirect_stdout(io.StringIO()):
            bot.main()
        self.assertEqual(modes,['manual'])
        self.assertFalse((self.base/'db/schedule_posts.db').exists())
        self.assertEqual(self.service.account('akun1')['publish_mode'],'schedule')
        self.assertEqual(self.conn.execute('SELECT status FROM posts_log').fetchone()[0],'draft')

    def test_schedule_copyright_confirmation_preserves_verified_target_once(self):
        target=store.now_wib()+timedelta(hours=2)
        context={'target':target,'caption':'caption'}
        with patch.object(bot,'eval_js',side_effect=[{'x':10,'y':20},True]),patch.object(bot,'click',return_value=5) as click:
            done,_=bot.resolve_copyright_prompt(None,'schedule',1,context)
        self.assertTrue(done)
        click.assert_called_once()

    def test_schedule_copyright_mismatch_or_late_target_never_confirms(self):
        for target,intact in [(store.now_wib()+timedelta(hours=2),False),(store.now_wib()+timedelta(minutes=5),True)]:
            with patch.object(bot,'eval_js',side_effect=[{'x':10,'y':20},intact]),patch.object(bot,'click') as click,self.assertRaises(bot.StepFailed):
                bot.resolve_copyright_prompt(None,'schedule',1,{'target':target})
            click.assert_not_called()

    def test_draft_popup_cancels_then_saves_draft_without_post_now(self):
        with patch.object(bot,'eval_js',side_effect=[{'x':99,'y':99},{'x':1,'y':2},'checking','ready']),patch.object(bot,'click',side_effect=lambda ws,x,y,mid:mid+1) as click,patch.object(bot,'find_with_retry',return_value=({'x':3,'y':4},10)),patch.object(bot.time,'sleep'):
            done,_=bot.resolve_copyright_prompt(None,'manual',1,check_attempts=2)
        self.assertTrue(done)
        self.assertEqual([(c.args[1],c.args[2]) for c in click.call_args_list],[(1,2),(3,4)])

    def test_draft_failed_check_does_not_retry_save_or_click_post_now(self):
        with patch.object(bot,'eval_js',side_effect=[{'x':99,'y':99},{'x':1,'y':2},'failed']),patch.object(bot,'click',return_value=5) as click,patch.object(bot,'find_with_retry') as find,self.assertRaises(bot.StepFailed):
            bot.resolve_copyright_prompt(None,'manual',1)
        click.assert_called_once_with(None,1,2,3)
        find.assert_not_called()

    def test_submission_poll_confirms_schedule_prompt_only_once(self):
        context={'target':store.now_wib()+timedelta(hours=2),'caption':'caption'}
        def evaluate(ws,expression,mid):
            if expression=='location.href':return 'https://www.tiktok.com/tiktokstudio/upload'
            if expression==bot.POST_NOW_BUTTON_JS:return {'x':10,'y':20}
            return True
        with patch.object(bot,'eval_js',side_effect=evaluate),patch.object(bot,'click',return_value=10) as click,patch.object(bot.time,'sleep'):
            success,_,_=bot.wait_for_submission(None,'schedule',1,attempts=3,schedule_context=context)
        self.assertFalse(success)
        click.assert_called_once()


if __name__ == '__main__':
    unittest.main()
