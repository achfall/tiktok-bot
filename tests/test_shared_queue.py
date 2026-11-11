"""Antrean multi-akun dan E2E lokal; semua koneksi browser/API diganti simulator."""
import contextlib
from datetime import datetime
import io
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import content_queue as q
import daily_orchestrator as bot
from archive_used import archive_content
from manage_concepts import assign, rename_concept
from bot_settings import defaults
from upload_overlays import TOUR_CLOSE_JS


class SharedQueueTests(unittest.TestCase):
    def test_upload_waits_for_delayed_file_input_without_upload_or_reload(self):
        calls=[]
        def cdp(ws, method, params=None, msg_id=None):
            calls.append(method)
            if method=='DOM.getDocument':return {'result':{'root':{'nodeId':len(calls)}}}
            return {'result':{'nodeId':42 if calls.count('DOM.querySelector')==8 else 0}}
        with patch.object(bot,'send_cdp',side_effect=cdp),patch.object(bot.time,'sleep') as sleep,contextlib.redirect_stdout(io.StringIO()):
            node,mid=bot.wait_for_upload_input(None,1)
        self.assertEqual(node,42)
        self.assertEqual(mid,17)
        self.assertEqual(sleep.call_count,7)
        self.assertEqual(set(calls),{'DOM.getDocument','DOM.querySelector'})

    def test_missing_upload_input_stops_after_bounded_wait(self):
        def cdp(ws,method,params=None,msg_id=None):
            return {'result':{'root':{'nodeId':1}}} if method=='DOM.getDocument' else {'result':{'nodeId':0}}
        with patch.object(bot,'send_cdp',side_effect=cdp),patch.object(bot.time,'sleep') as sleep,contextlib.redirect_stdout(io.StringIO()),self.assertRaises(bot.StepFailed):
            bot.wait_for_upload_input(None,1)
        self.assertEqual(sleep.call_count,10)

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.db=self.base/'test.db'
        self.conn=sqlite3.connect(self.db,timeout=30);self.addCleanup(self.conn.close)
        self.conn.executescript((ROOT/'scripts/db/schema.sql').read_text())
        for gid,name in [(1,'video-jahit'),(2,'konsep-B')]:
            folder=self.base/'content'/name;folder.mkdir(parents=True)
            self.conn.execute('INSERT INTO content_groups VALUES (?,?,?)',(gid,name,str(folder)))
        self.conn.commit()

    def add_account(self,aid,gid=1,mode='manual_review'):
        root=Path(self.conn.execute('SELECT folder_path FROM content_groups WHERE id=?',(gid,)).fetchone()[0]);folder=root/'Jingsu';folder.mkdir(exist_ok=True)
        self.conn.execute("INSERT INTO accounts (id,slug,chrome_profile_path,cdp_port,content_group_id,publish_mode) VALUES (?,?,?,?,?,?)",(aid,f'akun{aid}',str(self.base/'chrome-profiles'/f'tiktok{aid}.id'),9220+aid,gid,mode))
        self.conn.execute("INSERT INTO products (id,account_id,folder_path,folder_name,sort_order,tiktok_product_id) VALUES (?,?,?,'Jingsu',1,'1737713282535425495')",(aid,aid,str(folder)))
        self.conn.commit();return folder

    def video(self,folder,name='video.mp4'):
        path=folder/name;path.write_bytes(b'video data');return path

    def test_five_accounts_per_concept_concurrent_scan_claim(self):
        for aid in range(1,11):self.add_account(aid,1 if aid<=5 else 2)
        for gid in (1,2):
            folder=Path(self.conn.execute('SELECT folder_path FROM content_groups WHERE id=?',(gid,)).fetchone()[0])/'Jingsu'
            for n in range(5):self.video(folder,f'{n}.mp4')
        barrier=threading.Barrier(10)
        def worker(aid):
            with contextlib.closing(sqlite3.connect(self.db,timeout=30)) as conn:
                barrier.wait()
                q.scan_account(conn,aid)
                return q.claim_video(conn,aid,aid)
        with ThreadPoolExecutor(max_workers=10) as workers:claims=list(workers.map(worker,range(1,11)))
        self.assertTrue(all(claims));self.assertEqual(len({c.content_id for c in claims}),10)
        for c in claims:self.assertIn('video-jahit' if c.account_id<=5 else 'konsep-B',c.path)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM contents').fetchone()[0],10)

    def test_uncertain_stays_locked_pre_submit_failure_releases(self):
        folder=self.add_account(1);self.add_account(2);path=self.video(folder)
        q.scan_account(self.conn,1)
        first=q.claim_video(self.conn,1,1)
        self.assertIsNone(q.claim_video(self.conn,2,2))
        q.finish_failure(self.conn,first,None,'Caption failed')
        second=q.claim_video(self.conn,2,2)
        self.assertEqual(first.content_id,second.content_id)
        with self.assertRaises(ValueError):q.begin_submission(self.conn,first,'stale token')
        q.begin_submission(self.conn,second,'caption')
        q.finish_failure(self.conn,second,'caption','Timeout after Post')
        self.assertIsNone(q.claim_video(self.conn,1,1))
        self.assertEqual(self.conn.execute('SELECT status FROM contents').fetchone()[0],'uncertain')
        self.assertTrue(path.exists())
        q.resume_submission(self.conn,second)
        with self.assertRaises(ValueError):q.resume_submission(self.conn,second)
        q.finish_success(self.conn,second,'auto')
        archived=archive_content(self.conn,second.content_id)
        self.assertEqual(archived.parent,folder/'sudah-dipakai/tiktok2.id')
        self.assertFalse(path.exists())
        q.scan_account(self.conn,1);self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM contents').fetchone()[0],1)

    def test_daily_count_uses_local_day_and_includes_uncertain_but_not_failures(self):
        folder=self.add_account(1);self.add_account(2)
        self.video(folder);q.scan_account(self.conn,1)
        cid=self.conn.execute('SELECT id FROM contents').fetchone()[0]
        for status in ('success','draft','submitting','uncertain','failed'):
            self.conn.execute("INSERT INTO posts_log(account_id,product_id,content_id,status,published_at) VALUES(1,1,?,?,datetime('now'))",(cid,status))
        self.conn.execute("INSERT INTO posts_log(account_id,product_id,content_id,status,published_at) VALUES(1,1,?,'success',datetime('now','-2 days'))",(cid,))
        self.conn.commit()
        self.assertEqual(q.daily_post_count(self.conn,1),4)
        self.assertEqual(q.daily_post_count(self.conn,2),0)
        claim=q.claim_video(self.conn,1,1)
        with self.assertRaisesRegex(ValueError,'Batas video'):
            q.begin_submission(self.conn,claim,'caption',daily_limit=4)
        self.assertEqual(self.conn.execute('SELECT status FROM contents WHERE id=?',(cid,)).fetchone()[0],'claimed')
        self.assertEqual(q.daily_post_count(self.conn,1),4)

    def test_legacy_restart_uses_remaining_daily_quota(self):
        folder=self.add_account(1);self.video(folder)
        for pid in (2,3):
            extra=folder.parent/f'produk{pid}';extra.mkdir();self.video(extra)
            self.conn.execute("INSERT INTO products(id,account_id,folder_path,folder_name,sort_order,tiktok_product_id) VALUES(?,1,?,?,?,'12345')",(pid,str(extra),extra.name,pid))
        self.conn.commit();q.scan_account(self.conn,1)
        claim=q.claim_video(self.conn,1,1)
        q.begin_submission(self.conn,claim,'earlier');q.finish_success(self.conn,claim,'auto')
        runtime={**defaults(),'max_per_day':2}
        def upload(port,path,caption,pid,mode,before_submit,**kwargs):
            before_submit();return True,None
        with patch('bot_settings.effective_settings',return_value=runtime),patch.object(bot,'browser_slot',return_value=contextlib.nullcontext()),patch.object(bot,'prepare_browser',return_value=True),patch.object(bot,'close_account_browser',return_value=True),patch.object(bot,'DB_PATH',self.db),patch.object(sys,'argv',['bot','akun1','--publish','--limit','99']),patch.object(bot,'generate_unique_caption',return_value='caption'),patch.object(bot,'post_one_video',side_effect=upload) as posting,patch.object(bot,'wait_for_window'),patch.object(bot,'wait_until'),contextlib.redirect_stdout(io.StringIO()):
            bot.main();bot.main()
        posting.assert_called_once()
        self.assertEqual(q.daily_post_count(self.conn,1),2)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM contents WHERE status='unused'").fetchone()[0],1)

    def test_retest_copy_keeps_original_history(self):
        folder=self.add_account(1);source=self.video(folder);q.scan_account(self.conn,1)
        claim=q.claim_video(self.conn,1,1);q.begin_submission(self.conn,claim,'caption');q.finish_success(self.conn,claim,'manual')
        archived=archive_content(self.conn,claim.content_id)
        cid=q.prepare_retest(self.conn,1,1,claim.content_id)
        self.assertNotEqual(cid,claim.content_id)
        new=q.claim_video(self.conn,1,1,cid)
        self.assertTrue(archived.exists());self.assertTrue(Path(new.path).exists());self.assertFalse(source.exists())
        self.assertEqual(self.conn.execute('SELECT status FROM contents WHERE id=?',(claim.content_id,)).fetchone()[0],'used')

    def test_legacy_assign_migration_preserves_paths_history(self):
        old=self.base/'content/infosehat.ofc/Jingsu';old.mkdir(parents=True)
        archive=old/'sudah-dipakai';archive.mkdir();video=self.video(archive)
        self.conn.execute("INSERT INTO accounts (id,slug,chrome_profile_path,cdp_port) VALUES (1,'akun1','/profiles/infosehat.ofc',9221)")
        self.conn.execute("INSERT INTO products (id,account_id,folder_path,folder_name,sort_order) VALUES (1,1,?,'Jingsu',1)",(str(old),))
        self.conn.execute("INSERT INTO contents (product_id,file_path,status) VALUES (1,?,'used')",(str(video),))
        self.conn.execute("INSERT INTO posts_log (account_id,product_id,content_id,status) VALUES (1,1,1,'draft')");self.conn.commit()
        assign(self.conn,'akun1','video-jahit',self.base,True)
        root=self.base/'content/video-jahit/Jingsu'
        path,status=self.conn.execute('SELECT file_path,status FROM contents').fetchone()
        self.assertEqual(Path(path),root/'sudah-dipakai/video.mp4');self.assertTrue(Path(path).exists());self.assertEqual(status,'used')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM posts_log').fetchone()[0],1)
        moved=archive_content(self.conn,1)
        self.assertEqual(moved.parent,root/'sudah-dipakai/infosehat.ofc')
        assign(self.conn,'akun1','video-jahit',self.base,True)  # idempotent

    def test_conflicting_migration_rolls_back(self):
        old=self.base/'content/old/Jingsu';old.mkdir(parents=True);self.video(old)
        (self.base/'content/video-jahit/Jingsu').mkdir()
        self.conn.execute("INSERT INTO accounts (id,slug,chrome_profile_path,cdp_port) VALUES (1,'akun1','/profile',9221)")
        self.conn.execute("INSERT INTO products (account_id,folder_path,folder_name,sort_order) VALUES (1,?,'Jingsu',1)",(str(old),));self.conn.commit()
        with self.assertRaises(ValueError):assign(self.conn,'akun1','video-jahit',self.base,True)
        self.assertTrue((old/'video.mp4').exists())
        self.assertIsNone(self.conn.execute('SELECT content_group_id FROM accounts').fetchone()[0])

    def test_move_member_does_not_move_old_shared_stock(self):
        old=self.add_account(1);self.add_account(2);file=self.video(old)
        q.scan_account(self.conn,1)
        assign(self.conn,'akun1','konsep-B',self.base)
        self.assertTrue(file.exists())
        self.assertIsNone(q.claim_video(self.conn,1,1))
        claim=q.claim_video(self.conn,2,2)
        self.assertEqual(claim.path,str(file))

    def test_schema_migration_is_additive_and_repeatable(self):
        with contextlib.closing(sqlite3.connect(self.base/'legacy.db')) as conn:
            conn.executescript("""
                CREATE TABLE accounts (id INTEGER PRIMARY KEY,slug TEXT);
                CREATE TABLE products (id INTEGER PRIMARY KEY);
                CREATE TABLE contents (id INTEGER PRIMARY KEY,product_id INTEGER,file_path TEXT,status TEXT);
                CREATE TABLE posts_log (id INTEGER PRIMARY KEY);
                INSERT INTO accounts VALUES (1,'akun1');
                INSERT INTO contents VALUES (7,1,'old.mp4','used');
            """)
            q.migrate(conn);q.migrate(conn)
            self.assertEqual(conn.execute('SELECT id,file_path,status FROM contents').fetchall(),[(7,'old.mp4','used')])
            self.assertEqual(conn.execute('SELECT slug,caption_style FROM accounts').fetchall(),[('akun1','generic')])

    def test_new_account_uses_concept_without_private_content_folder(self):
        import setup_account
        (self.base/'db').mkdir()
        with contextlib.closing(sqlite3.connect(self.base/'db/tiktok_bot.db')) as destination:
            self.conn.backup(destination)
        with patch.object(setup_account,'BASE',self.base),patch.object(sys,'argv',['setup','akun1','--name','infosehat.ofc','--concept','video-jahit']),contextlib.redirect_stdout(io.StringIO()):
            setup_account.main()
        self.assertTrue((self.base/'chrome-profiles/infosehat.ofc').is_dir())
        self.assertFalse((self.base/'content/infosehat.ofc').exists())
        with contextlib.closing(sqlite3.connect(self.base/'db/tiktok_bot.db')) as result:
            self.assertEqual(result.execute('SELECT content_group_id FROM accounts').fetchone()[0],1)

    def test_rename_concept_updates_all_paths_and_preserves_history(self):
        folder=self.add_account(1)
        self.video(folder)
        q.scan_account(self.conn,1)
        claim=q.claim_video(self.conn,1,1)
        with self.assertRaises(ValueError):
            rename_concept(self.conn,'video-jahit','video-baru',self.base)
        q.begin_submission(self.conn,claim,'caption')
        q.finish_success(self.conn,claim,'manual')
        archive_content(self.conn,claim.content_id)
        old_log=self.conn.execute('SELECT * FROM posts_log').fetchall()
        rename_concept(self.conn,'video-jahit','video-baru',self.base)
        new=self.base/'content/video-baru/Jingsu'
        file,source,status=self.conn.execute('SELECT file_path,source_folder,status FROM contents').fetchone()
        self.assertTrue(Path(file).is_file())
        self.assertEqual(Path(source),new)
        self.assertEqual(status,'used')
        self.assertEqual(self.conn.execute('SELECT folder_path FROM products').fetchone()[0],str(new))
        self.assertEqual(old_log,self.conn.execute('SELECT * FROM posts_log').fetchall())
        self.assertFalse((self.base/'content/video-jahit').exists())

    def run_e2e(self,mode,timeout=False,force_draft=False,link_name=None,music_enabled=False,music_failure=False,copyright_prompt=False):
        runtime=defaults()
        runtime['music_enabled']=music_enabled
        if link_name is not None:runtime['product_link_name']=link_name
        folder=self.add_account(1,mode='auto' if force_draft else mode);source=self.video(folder)
        if force_draft:
            q.scan_account(self.conn,1)
            original=q.claim_video(self.conn,1,1)
            q.begin_submission(self.conn,original,'caption lama');q.finish_success(self.conn,original,'auto');archive_content(self.conn,original.content_id)
        state={'caption':'video','product':'old','dialog':True,'target':None,'selected':None,'submitted':False,'confirmed':False,'uploads':0,'picker_stage':'none'}
        response=MagicMock();response.json.return_value={'id':'tab','webSocketDebuggerUrl':'ws://127.0.0.1:9221/devtools/page/tab'}
        (self.base/'chrome-profiles/tiktok1.id/Default').mkdir(parents=True,exist_ok=True)
        def evaluate(ws,expr,mid):
            if "#profile_path" in expr:
                return str(self.base/'chrome-profiles/tiktok1.id/Default')
            if expr==bot.PRODUCT_PICKER_STATE_JS:return {'stage':state['picker_stage']}
            if expr==bot.ADD_LINK_BUTTON_JS:state['target']='open_link';return {'x':1,'y':2}
            if expr==bot.ADD_LINK_NEXT_JS:state['target']='link_next';return {'x':1,'y':2}
            if expr==bot.CAPTION_SELECT_JS:state['selected']='caption';return True
            if expr==bot.CAPTION_READ_JS:return state['caption']
            if 'anchor_container' in expr:return True
            if expr==bot.PRODUCT_NAME_SELECT_JS:state['selected']='product';return True
            if expr==bot.PRODUCT_NAME_VALUE_JS:return state['product'] if state['dialog'] else None
            if expr==bot.PRODUCT_ADD_BUTTON_JS:state['target']='attach';return {'x':1,'y':2,'disabled':False}
            if expr==bot.PRODUCT_NAME_FIELD_JS:return {'x':1,'y':2}
            if expr==bot.POST_NOW_BUTTON_JS:
                if state['submitted'] and not state['confirmed'] and (mode=='auto' or copyright_prompt):state['target']='confirm';return {'x':1,'y':2}
                return None
            if expr=='location.href':
                if state['submitted'] and not timeout and ((mode in ('manual_review','schedule') and not copyright_prompt) or state['confirmed']):return 'https://www.tiktok.com/tiktokstudio/content'+('?tab=draft' if mode=='manual_review' else '')
                return 'https://www.tiktok.com/tiktokstudio/upload'
            if state.get('schedule_target') and expr==bot.schedule_ui.confirmation_guard(state['schedule_target']):return True
            if 'components_PostInfoCell_FlexColumn' in expr:
                return bool(state['submitted'] and not timeout and state.get('schedule_verified'))
            if "toLowerCase()==='my shop'" in expr:return False
            if "trim()==='Post'" in expr or "trim()==='Save draft'" in expr:state['target']='submit'
            if 'post_video_button' in expr and 'Schedule' in expr:state['target']='submit'
            return {'x':1,'y':2,'disabled':False}
        def send(ws,method,params=None,msg_id=1):
            state.setdefault('methods',[]).append(method)
            if method=='Input.insertText' and state['selected']:state[state['selected']]=params['text']
            if method=='DOM.getDocument':return {'result':{'root':{'nodeId':1}}}
            if method=='DOM.querySelector':return {'result':{'nodeId':2}}
            if method=='DOM.setFileInputFiles':

                if force_draft:
                    self.assertIn('-tes-',params['files'][0]);self.assertTrue(Path(params['files'][0]).is_file())
                else:self.assertEqual(params['files'],[str(source)])
                state['uploads']+=1
            return {'result':{}}
        def click(ws,x,y,mid):
            state['selected']=None
            target=state.pop('target',None)
            if target=='open_link':state['picker_stage']='link_type'
            if target=='link_next':state['picker_stage']='picker'
            if target=='attach':
                self.assertEqual(state['product'],runtime['product_link_name']);state['dialog']=False
            if target=='submit':
                if music_enabled:self.assertTrue(state.get('music_verified'))
                if mode=='schedule':self.assertTrue(state.get('schedule_verified'))
                with contextlib.closing(sqlite3.connect(self.db)) as check:
                    self.assertEqual(check.execute('SELECT status FROM contents').fetchone()[0],'submitting')
                self.assertEqual(state['caption'],'Caption E2E');state['submitted']=True
            if target=='confirm':state['confirmed']=True
            return mid+2
        args=['bot','akun1','--publish']+(['--retest-content','1','--test-draft'] if force_draft else [])
        output=io.StringIO()
        def apply_music(ws,mid,base,account,content_id,evaluate,click):
            self.assertEqual(account,'akun1')
            self.assertEqual(self.conn.execute('SELECT status FROM contents WHERE id=?',(content_id,)).fetchone()[0],'claimed')
            self.assertFalse(state['submitted'])
            if music_failure:raise RuntimeError('Favorites kosong')
            state['music_verified']=True
            return mid+1
        def configure_schedule(ws,mid,target,evaluate,click):
            self.assertFalse(state['submitted'])
            self.assertGreater(target,datetime.now(target.tzinfo))
            state['schedule_verified']=True
            state['schedule_target']=target
            return mid+1
        with patch.object(bot.schedule_ui,'configure',side_effect=configure_schedule),patch.object(bot,'apply_favorite_music',side_effect=apply_music) as music_step,patch('bot_settings.effective_settings',return_value=runtime),patch.object(bot,'browser_slot',return_value=contextlib.nullcontext()),patch.object(bot,'prepare_browser',return_value=True),patch.object(bot,'close_account_browser') as close_browser,patch.object(bot,'DB_PATH',self.db),patch.object(sys,'argv',args),patch.object(bot,'generate_unique_caption',return_value='Caption E2E'),patch.object(bot.requests,'put',return_value=response),patch.object(bot.requests,'get'),patch.object(bot.websocket,'create_connection',return_value=MagicMock()),patch.object(bot,'eval_js',side_effect=evaluate),patch.object(bot,'send_cdp',side_effect=send),patch.object(bot,'click',side_effect=click),patch.object(bot.time,'sleep'),patch.object(bot,'wait_until'),contextlib.redirect_stdout(output):
            bot.main()
        if music_enabled:music_step.assert_called_once()
        else:music_step.assert_not_called()
        if music_failure:
            self.assertFalse(state['submitted']);close_browser.assert_called_once_with(9221)
            self.assertEqual(self.conn.execute('SELECT status FROM contents').fetchone()[0],'unused')
            self.assertTrue(source.exists())
            return state
        if timeout:
            close_browser.assert_not_called()
        else:
            close_browser.assert_called_once_with(9221)
        self.assertEqual(state['uploads'],1)
        self.assertNotIn('Input.enable',state['methods'])
        self.assertNotIn('Browser.grantPermissions',state['methods'])
        self.assertTrue(state['submitted'],output.getvalue())
        status,path=self.conn.execute('SELECT status,file_path FROM contents ORDER BY id DESC LIMIT 1').fetchone()
        if timeout:
            self.assertEqual(status,'uncertain');self.assertTrue(source.exists())
        else:
            self.assertEqual(status,'used');self.assertFalse(source.exists())
            self.assertEqual(Path(path).parent,folder/'sudah-dipakai/tiktok1.id')
            self.assertTrue(Path(path).exists())
        return state

    def test_e2e_draft_with_real_orchestrator_and_simulated_cdp(self):
        self.run_e2e('manual_review')

    def test_e2e_schedule_with_product_and_music_using_simulated_cdp(self):
        result=self.run_e2e('schedule',music_enabled=True)
        self.assertFalse(result['confirmed'])
        self.assertEqual(self.conn.execute('SELECT status FROM posts_log').fetchone()[0],'scheduled')

    def test_e2e_schedule_timeout_holds_claim_without_post_now(self):
        result=self.run_e2e('schedule',timeout=True)
        self.assertFalse(result['confirmed'])
        self.assertEqual(self.conn.execute('SELECT status FROM posts_log').fetchone()[0],'uncertain')

    def test_e2e_schedule_resolves_copyright_prompt_then_verifies_scheduled_row(self):
        result=self.run_e2e('schedule',copyright_prompt=True)
        self.assertTrue(result['confirmed'])
        self.assertEqual(self.conn.execute('SELECT status FROM posts_log').fetchone()[0],'scheduled')

    def test_e2e_custom_product_link_name_draft(self):
        self.run_e2e('manual_review',link_name='Cek Produk Pilihan')

    def test_e2e_custom_product_link_name_publish(self):
        self.run_e2e('auto',link_name='Pilihan Hari Ini')

    def test_e2e_music_then_draft(self):
        self.run_e2e('manual_review',music_enabled=True)

    def test_e2e_music_then_publish(self):
        self.run_e2e('auto',music_enabled=True)

    def test_e2e_music_failure_prevents_submit(self):
        self.run_e2e('auto',music_enabled=True,music_failure=True)

    def test_product_link_mismatch_does_not_click_add(self):
        with patch.object(bot,'find_with_retry',return_value=({'x':1,'y':1},2)) as find,patch.object(bot,'click',return_value=3),patch.object(bot,'eval_js',side_effect=[True,'truncated']),patch.object(bot,'send_cdp',return_value={}),patch.object(bot.time,'sleep'),contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(bot.StepFailed):bot.attach_product_link(MagicMock(),1,'Cek Produk Pilihan')
        find.assert_called_once()

    def test_product_picker_retries_lost_add_click_and_waits_for_dialog(self):
        state={'stage':'none','target':None,'add':0,'next':0}
        def evaluate(ws,expr,mid):
            if expr==TOUR_CLOSE_JS:return None
            if expr==bot.PRODUCT_PICKER_STATE_JS:return {'stage':state['stage']}
            if expr==bot.ADD_LINK_BUTTON_JS:state['target']='add';return {'x':1,'y':2}
            if expr==bot.ADD_LINK_NEXT_JS:state['target']='next';return {'x':1,'y':2}
            self.fail('Unexpected picker expression')
        def click(ws,x,y,mid):
            state[state['target']]+=1
            if state['target']=='add' and state['add']==2:state['stage']='link_type'
            if state['target']=='next':state['stage']='picker'
            return mid+2
        with patch.object(bot,'eval_js',side_effect=evaluate),patch.object(bot,'click',side_effect=click),patch.object(bot.time,'sleep'),contextlib.redirect_stdout(io.StringIO()):
            bot.open_product_picker(None,1)
        self.assertEqual((state['add'],state['next']),(2,1))

    def test_product_picker_does_not_click_add_again_when_dialog_loading(self):
        stages=iter([{'stage':'none'},{'stage':'link_type'},{'stage':'unknown'},{'stage':'unknown'},{'stage':'picker'}])
        def evaluate(ws,expr,mid):
            return next(stages) if expr==bot.PRODUCT_PICKER_STATE_JS else {'x':1,'y':2}
        with patch.object(bot,'eval_js',side_effect=evaluate),patch.object(bot,'click',side_effect=lambda ws,x,y,mid:mid+2) as click,patch.object(bot.time,'sleep'),contextlib.redirect_stdout(io.StringIO()):
            bot.open_product_picker(None,1)
        self.assertEqual(click.call_count,2)

    def test_product_picker_already_open_does_not_click(self):
        with patch.object(bot,'eval_js',return_value={'stage':'picker'}),patch.object(bot,'click') as click:
            bot.open_product_picker(None,1)
        click.assert_not_called()

    def test_product_picker_timeout_does_not_click_obstructed_button(self):
        def evaluate(ws,expr,mid):
            return {'stage':'none'} if expr==bot.PRODUCT_PICKER_STATE_JS else None
        with patch.object(bot,'eval_js',side_effect=evaluate),patch.object(bot,'click') as click,patch.object(bot.time,'sleep'):
            with self.assertRaises(bot.StepFailed):bot.open_product_picker(None,1)
        click.assert_not_called()

    def test_window_waits_until_configured_minute(self):
        with patch.object(bot,'WINDOW_START_HOUR',21),patch.object(bot,'WINDOW_START_MINUTE',30),patch.object(bot,'datetime') as clock,patch.object(bot,'wait_until') as sleep,contextlib.redirect_stdout(io.StringIO()):
            clock.now.return_value=datetime(2026,11,5,21,0)
            bot.wait_for_window()
            sleep.assert_called_once_with(datetime(2026,11,5,21,30).timestamp())
            sleep.reset_mock()
            clock.now.return_value=datetime(2026,11,5,21,30)
            bot.wait_for_window()
            sleep.assert_not_called()

    def test_close_browser_uses_account_browser_endpoint(self):
        response=MagicMock()
        response.json.return_value={'webSocketDebuggerUrl':'ws://127.0.0.1:9223/devtools/browser/test'}
        ws=MagicMock()
        with patch.object(bot,'browser_ready',return_value=False),patch.object(bot.requests,'get',return_value=response) as get,patch.object(bot.websocket,'create_connection',return_value=ws),contextlib.redirect_stdout(io.StringIO()):
            bot.close_account_browser(9223)
        get.assert_called_once_with('http://127.0.0.1:9223/json/version',timeout=5)
        import json
        self.assertEqual(json.loads(ws.send.call_args.args[0])['method'],'Browser.close')
        ws.close.assert_called_once()

    def test_close_browser_failure_preserves_result_and_rejects_wrong_port(self):
        response=MagicMock()
        response.json.return_value={'webSocketDebuggerUrl':'ws://127.0.0.1:9221/devtools/browser/other'}
        with patch.object(bot.requests,'get',return_value=response),patch.object(bot.websocket,'create_connection') as connect,contextlib.redirect_stdout(io.StringIO()):
            bot.close_account_browser(9223)
        connect.assert_not_called()

    def test_e2e_publish_handles_post_now_then_archives(self):
        state=self.run_e2e('auto');self.assertTrue(state['confirmed'])

    def test_e2e_retest_draft_keeps_config_auto_and_history(self):
        self.run_e2e('manual_review',force_draft=True)
        self.assertEqual(self.conn.execute('SELECT publish_mode FROM accounts').fetchone()[0],'auto')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM contents').fetchone()[0],2)

    def test_e2e_timeout_keeps_file_and_holds_queue(self):
        self.run_e2e('auto',timeout=True)
        self.assertEqual(self.conn.execute('SELECT status FROM posts_log').fetchone()[0],'uncertain')


if __name__=='__main__':unittest.main()
