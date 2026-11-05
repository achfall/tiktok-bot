"""Folder akun nyata/SQLite sementara; semua upload disimulasikan."""
import contextlib
import io
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'scripts'))
import content_queue as q
import daily_orchestrator as bot
from account_sources import save_ids, read_ids
from archive_used import archive_content
from migrate_account_source import convert
from botapp.service import ControlService


class AccountSourcesTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name).resolve();(self.base/'db').mkdir()
        self.db=self.base/'db/tiktok_bot.db'
        self.conn=sqlite3.connect(self.db);self.addCleanup(self.conn.close)
        self.conn.executescript((ROOT/'scripts/db/schema.sql').read_text())
        self.service=ControlService(self.base)
        self.service.create_account({'name':'jingsu.id'})
        self.folder=self.base/'content/jingsu.id'
        save_ids(self.folder,'11111\n22222\n')

    def videos(self,n=3):
        for index in range(n):(self.folder/f'{index:03}.mp4').write_bytes(b'video')
        q.scan_account(self.conn,1)

    def claim(self):
        pid=self.conn.execute('SELECT id FROM products WHERE account_id=1 AND active=1 ORDER BY sort_order LIMIT 1').fetchone()[0]
        return q.claim_video(self.conn,1,pid)

    def success(self,c):
        q.begin_submission(self.conn,c,'caption');q.finish_success(self.conn,c,'manual')
        return archive_content(self.conn,c.content_id)

    def test_rotation_restart_retry_and_archive(self):
        self.videos()
        c=self.claim();first=c.product_id
        q.finish_failure(self.conn,c,None,'caption failed')
        # File order/list changes do not reassign failed videos.
        save_ids(self.folder,'22222\n11111\n');q.scan_account(self.conn,1)
        c=self.claim();self.assertEqual(c.product_id,first)
        self.assertEqual(self.success(c).parent,self.folder/'sudah-dipakai')
        self.conn.close();self.conn=sqlite3.connect(self.db);self.addCleanup(self.conn.close)
        save_ids(self.folder,'11111\n22222\n');q.scan_account(self.conn,1)
        c=self.claim();self.assertNotEqual(c.product_id,first);self.success(c)
        c=self.claim();self.assertEqual(c.product_id,first);self.success(c)
        self.assertEqual(q.scan_account(self.conn,1),0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM contents').fetchone()[0],3)
        self.assertIsNone(self.claim())

    def test_isolation_uncertain_and_invalid_ids(self):
        self.service.create_account({'name':'azzaraofficial'})
        other=self.base/'content/azzaraofficial';save_ids(other,'33333')
        (other/'000.mp4').write_bytes(b'other');q.scan_account(self.conn,2)
        self.videos(1);c=self.claim()
        self.assertEqual(Path(c.path).parent,self.folder)
        q.begin_submission(self.conn,c,'caption');q.finish_failure(self.conn,c,'caption','timeout')
        with self.assertRaises(ValueError):self.claim()
        before=(self.folder/'ID-produk.txt').read_text()
        for text in ('','abc','11111\n11111','11111\nhttps://example.com'):
            with self.assertRaises(ValueError):save_ids(self.folder,text)
        self.assertEqual((self.folder/'ID-produk.txt').read_text(),before)
        (other/'ID-produk.txt').write_text('invalid')
        with self.assertRaises(ValueError):q.scan_account(self.conn,2)

    def test_removed_id_retry_keeps_original_pair_and_cursor(self):
        self.videos(2);c=self.claim()
        q.finish_failure(self.conn,c,None,'before submit')
        save_ids(self.folder,'22222');q.scan_account(self.conn,1)
        retry=self.claim()
        self.assertEqual(retry.product_id,c.product_id)
        self.assertEqual(self.conn.execute('SELECT tiktok_product_id FROM content_assignments WHERE content_id=?',(c.content_id,)).fetchone()[0],'11111')
        self.assertEqual(self.conn.execute('SELECT next_index FROM account_sources WHERE account_id=1').fetchone()[0],1)
        self.success(retry)
        next_claim=self.claim()
        self.assertEqual(self.conn.execute('SELECT tiktok_product_id FROM content_assignments WHERE content_id=?',(next_claim.content_id,)).fetchone()[0],'22222')

    def test_concurrent_claim_does_not_assign_twice(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor
        self.videos(2)
        pid=self.conn.execute('SELECT id FROM products WHERE account_id=1 AND active=1 ORDER BY sort_order LIMIT 1').fetchone()[0]
        barrier=threading.Barrier(2)
        def worker(_):
            with contextlib.closing(sqlite3.connect(self.db,timeout=10)) as conn:
                barrier.wait()
                try:return q.claim_video(conn,1,pid)
                except ValueError:return None
        with ThreadPoolExecutor(max_workers=2) as pool:claims=list(pool.map(worker,range(2)))
        self.assertEqual(sum(c is not None for c in claims),1)
        self.assertEqual(self.conn.execute('SELECT next_index FROM account_sources WHERE account_id=1').fetchone()[0],1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM content_assignments').fetchone()[0],1)

    def test_panel_ids_and_direct_upload_target(self):
        self.service.update_source({'account':'akun1','ids':'11111\n22222'})
        state=self.service.state();a=state['accounts'][0]
        self.assertEqual(a['product_ids'],['11111','22222'])
        self.assertEqual(a['source_path'],str(self.folder))
        self.videos(1)
        self.assertEqual(self.service.state()['queue'][0]['product'],'Belum dipasangkan')
        pid=state['products'][0]['id']
        self.assertEqual(self.service.upload_target(pid,'new.mov')[1],self.folder/'new.mov')
        with self.assertRaises(ValueError):self.service.update_product({'id':pid,'product_id':'99999'})

    def test_orchestrator_rotates_within_daily_video_limit(self):
        self.videos(4);seen=[];events=[]
        def upload(port,path,caption,pid,mode,before_submit,product_link_name=None,music_context=None):
            events.append('upload')
            seen.append((Path(path).name,pid));before_submit();return True,None
        def close(port):
            self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM contents WHERE status='used'").fetchone()[0],len(seen))
            events.append('close')
            return True
        with patch.object(bot,'browser_slot',return_value=contextlib.nullcontext()),patch.object(bot,'prepare_browser',side_effect=lambda *args:events.append('open')) as prepare,patch.object(bot,'close_account_browser',side_effect=close) as close_browser,patch.object(bot,'DB_PATH',self.db),patch.object(sys,'argv',['bot','akun1','--publish','--limit','3']),patch.object(bot,'generate_unique_caption',return_value='caption'),patch.object(bot,'post_one_video',side_effect=upload),patch.object(bot,'wait_for_window',side_effect=lambda:events.append('window')),patch.object(bot.time,'sleep',side_effect=lambda _:events.append('wait')),contextlib.redirect_stdout(io.StringIO()):
            bot.main();bot.main()
        self.assertEqual(close_browser.call_count,3)
        self.assertEqual(prepare.call_count,3)
        self.assertEqual([pid for _,pid in seen],['11111','22222','11111'])
        self.assertEqual(len({name for name,_ in seen}),3)
        self.assertEqual(events,['window','open','upload','close','wait','window','open','upload','close','wait','window','open','upload','close'])

    def test_launcher_failure_releases_claim_without_upload(self):
        self.videos(1)
        with patch.object(bot,'browser_slot',return_value=contextlib.nullcontext()),patch.object(bot,'prepare_browser',side_effect=RuntimeError('CDP not ready')),patch.object(bot,'post_one_video') as upload,patch.object(bot,'close_account_browser') as close,patch.object(bot,'DB_PATH',self.db),patch.object(sys,'argv',['bot','akun1','--publish']),patch.object(bot,'generate_unique_caption',return_value='caption'),patch.object(bot,'wait_for_window'),contextlib.redirect_stdout(io.StringIO()):
            bot.main()
        upload.assert_not_called();close.assert_not_called()
        self.assertEqual(self.conn.execute('SELECT status FROM contents').fetchone()[0],'unused')

    def test_close_failure_preserves_used_video_and_stops_next_upload(self):
        self.videos(2)
        def upload(port,path,caption,pid,mode,before_submit,product_link_name=None,music_context=None):
            before_submit();return True,None
        with patch.object(bot,'browser_slot',return_value=contextlib.nullcontext()),patch.object(bot,'prepare_browser'),patch.object(bot,'post_one_video',side_effect=upload) as posting,patch.object(bot,'close_account_browser',return_value=False),patch.object(bot,'DB_PATH',self.db),patch.object(sys,'argv',['bot','akun1','--publish','--limit','2']),patch.object(bot,'generate_unique_caption',return_value='caption'),patch.object(bot,'wait_for_window'),patch.object(bot.time,'sleep') as sleep,contextlib.redirect_stdout(io.StringIO()):
            bot.main()
        posting.assert_called_once();sleep.assert_not_called()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM contents WHERE status='used'").fetchone()[0],1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM contents WHERE status='unused'").fetchone()[0],1)

    def legacy(self):
        # Separate fixture source/account, including old nested archive.
        old=self.base/'content/concept/Jingsu';old.mkdir(parents=True)
        archived=old/'sudah-dipakai/old.id/old.mp4';archived.parent.mkdir(parents=True);archived.write_bytes(b'old')
        self.conn.execute("INSERT INTO content_groups VALUES(1,'concept',?)",(str(old.parent),))
        self.conn.execute("INSERT INTO accounts(id,slug,chrome_profile_path,cdp_port,content_group_id) VALUES(9,'akun9',?,9229,1)",(str(self.base/'chrome-profiles/old.id'),))
        pid=self.conn.execute("INSERT INTO products(account_id,folder_path,folder_name,sort_order,tiktok_product_id) VALUES(9,?,'Jingsu',1,'99999')",(str(old),)).lastrowid
        cid=self.conn.execute("INSERT INTO contents(product_id,file_path,status,source_folder) VALUES(?,?,'used',?)",(pid,str(archived),str(old))).lastrowid
        self.conn.execute("INSERT INTO posts_log(account_id,product_id,content_id,status) VALUES(9,?,?,'draft')",(pid,cid));self.conn.commit()
        return old,cid

    def test_migration_history_idempotence_conflict_and_rollback(self):
        old,cid=self.legacy();target=self.base/'content/old.id'
        self.conn.execute("CREATE TRIGGER fail_source BEFORE INSERT ON account_sources WHEN NEW.account_id=9 BEGIN SELECT RAISE(ABORT,'fixture'); END;");self.conn.commit()
        with self.assertRaises(sqlite3.IntegrityError):convert(self.conn,'akun9',self.base)
        self.assertTrue(old.is_dir());self.assertFalse(target.exists());self.assertFalse((old/'ID-produk.txt').exists())
        self.conn.execute('DROP TRIGGER fail_source');self.conn.commit()
        target.mkdir()
        with self.assertRaises(ValueError):convert(self.conn,'akun9',self.base)
        target.rmdir();convert(self.conn,'akun9',self.base)
        self.assertEqual(convert(self.conn,'akun9',self.base),target)
        self.assertEqual(read_ids(target),['99999'])
        path,status=self.conn.execute('SELECT file_path,status FROM contents WHERE id=?',(cid,)).fetchone()
        self.assertTrue(Path(path).is_file());self.assertEqual(status,'used')
        self.assertEqual(archive_content(self.conn,cid),Path(path))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM posts_log WHERE account_id=9').fetchone()[0],1)


if __name__=='__main__':unittest.main()
