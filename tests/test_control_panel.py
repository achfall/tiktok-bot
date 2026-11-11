"""Panel API/services dengan DB sementara; tidak pernah membuka Chrome/TikTok."""
from contextlib import closing
from datetime import datetime
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
import json
import sqlite3
import sys
import tempfile
import shutil
import subprocess
import time
import unittest
from unittest.mock import patch, MagicMock

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'scripts'))
from botapp.service import ControlService
from botapp.server import Handler
from bot_settings import effective_settings, load_document


class FakeSocket:
    def __init__(self,data):self.input=BytesIO(data);self.output=bytearray()
    def makefile(self,*args):return self.input
    def sendall(self,data):self.output.extend(data)


class PanelTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);(self.base/'db').mkdir()
        self.db=self.base/'db/tiktok_bot.db'
        self.folder=self.base/'content/video-jahit/Jingsu';self.folder.mkdir(parents=True)
        profile=self.base/'chrome-profiles/infosehat.ofc';profile.mkdir(parents=True)
        with closing(sqlite3.connect(self.db)) as conn,conn:
            conn.executescript((ROOT/'scripts/db/schema.sql').read_text())
            conn.execute("INSERT INTO content_groups VALUES (1,'video-jahit',?)",(str(self.folder.parent),))
            conn.execute("INSERT INTO accounts (slug,chrome_profile_path,cdp_port,content_group_id) VALUES ('akun1',?,9221,1)",(str(profile),))
            conn.execute("INSERT INTO products (account_id,folder_path,folder_name,sort_order,tiktok_product_id) VALUES (1,?,'Jingsu',1,'1737713282535425495')",(str(self.folder),))
        (self.base/'.env').write_text('GEMINI_API_KEY=secret-sentinel-do-not-expose\nUNRELATED=keep-me\n')
        self.service=ControlService(self.base)

    def request(self,path='/',method='GET',data=None,headers=None,raw=None):
        body=raw if raw is not None else json.dumps(data or {}).encode() if method=='POST' else b''
        hdr={'Host':'127.0.0.1:8765','Connection':'close','Content-Length':str(len(body))}
        if method=='POST':hdr.update({'X-Panel-Token':'test-token','Content-Type':'application/json','Origin':'http://127.0.0.1:8765'})
        hdr.update(headers or {})
        wire=f'{method} {path} HTTP/1.1\r\n'+''.join(f'{k}: {v}\r\n' for k,v in hdr.items())+'\r\n'
        socket=FakeSocket(wire.encode()+body)
        server=SimpleNamespace(service=self.service,token='test-token',allowed_hosts={'127.0.0.1:8765','localhost:8765'})
        Handler(socket,('127.0.0.1',9000),server)
        response=bytes(socket.output);head,payload=response.split(b'\r\n\r\n',1)
        return int(head.split(b' ')[1]),head,payload

    def test_state_static_api_and_keys_are_masked(self):
        status,headers,body=self.request('/api/v1/state')
        self.assertEqual(status,200);state=json.loads(body)
        self.assertEqual(state['api_version'],2)
        self.assertEqual(state['accounts'][0]['name'],'infosehat.ofc')
        self.assertIs(state['keys']['GEMINI_API_KEY'],True)
        self.assertNotIn(b'secret-sentinel',body)
        status,headers,body=self.request('/')
        self.assertEqual(status,200);self.assertIn(b'Content-Security-Policy:',headers);self.assertIn(b'Ruang Konten',body)
        self.assertEqual(self.request('/.env')[0],404)

    def test_cross_origin_and_missing_token_cannot_mutate(self):
        self.assertEqual(self.request('/api/v1/concepts/create','POST',{'name':'evil'},headers={'Origin':'http://evil.example'})[0],403)
        self.assertEqual(self.request('/api/v1/concepts/create','POST',{'name':'evil'},headers={'X-Panel-Token':''})[0],403)
        self.assertEqual(self.request('/api/v1/state',headers={'Host':'evil.example'})[0],403)
        self.assertFalse((self.base/'content/evil').exists())

    def test_schedule_mode_http_settings_and_confirmation_without_launch(self):
        status,_,body=self.request('/api/v1/accounts/update','POST',{'account':'akun1','mode':'schedule','values':{'schedule_start_hour':17,'schedule_start_minute':30,'schedule_interval_minutes':30}})
        self.assertEqual(status,200,body)
        state=json.loads(self.request('/api/v1/state')[2])
        self.assertEqual(state['api_version'],2)
        self.assertEqual(state['accounts'][0]['publish_mode'],'schedule')
        self.assertEqual(state['accounts'][0]['settings']['schedule_interval_minutes'],30)
        self.assertEqual(self.request('/api/v1/runs/start','POST',{'account':'akun1','kind':'configured'})[0],400)
        self.assertEqual(self.service.jobs.list(),[])

    def test_low_disk_upload_is_rejected_before_writing_video(self):
        with patch('botapp.server.shutil.disk_usage',return_value=SimpleNamespace(free=1)):
            status,_,body=self.request('/api/v1/upload?product=1','POST',headers={'X-File-Name':'new.mp4'},raw=b'video')
        self.assertEqual(status,400,body)
        self.assertFalse((self.folder/'new.mp4').exists())
        self.assertEqual(list(self.folder.glob('.upload-*')),[])

    def test_settings_endpoint_cannot_change_running_account(self):
        with closing(self.service.jobs.connect()) as conn,conn:
            conn.execute("INSERT INTO runs(id,account,kind,status,created_at,log_path) VALUES('fixture-running','akun1','configured','running','2026-01-01','unused.log')")
        self.assertEqual(self.request('/api/v1/settings','POST',{'account':'akun1','values':{'max_per_day':2}})[0],400)

    def test_concept_account_product_services_use_real_cli_with_isolated_data(self):
        self.service.create_concept({'name':'konten-ai'})
        self.service.create_account({'name':'jingsu.id','concept':'konten-ai'})
        self.service.create_product({'account':'akun2','folder':'Produk AI','product_id':'1737713282535425495'})
        self.assertTrue((self.base/'chrome-profiles/jingsu.id').is_dir())
        self.assertTrue((self.base/'content/konten-ai/Produk AI').is_dir())
        self.assertFalse((self.base/'content/akun2').exists())
        state=self.service.state();self.assertEqual(len(state['accounts']),2)
        self.assertEqual(state['accounts'][1]['publish_mode'],'manual_review')
        self.service.rename_concept({'name':'konten-ai','new_name':'video-ai'})
        self.assertTrue((self.base/'content/video-ai/Produk AI').is_dir())
        self.service.update_product({'id':state['products'][1]['id'],'active':False})
        self.assertEqual(self.service.state()['products'][1]['active'],0)

    def test_settings_overrides_validation_and_secrets_preserve_unrelated_env(self):
        self.service.save_settings({'values':{'max_per_day':3,'min_interval_minutes':60,'max_interval_minutes':90}})
        self.service.save_settings({'account':'akun1','values':{'max_per_day':1}})
        self.assertEqual(effective_settings(self.base,'akun1')['max_per_day'],1)
        self.assertEqual(effective_settings(self.base)['max_per_day'],3)
        before=(self.base/'config/control-settings.json').read_bytes()
        with self.assertRaises(ValueError):self.service.save_settings({'values':{'min_interval_minutes':120,'max_interval_minutes':20}})
        self.assertEqual((self.base/'config/control-settings.json').read_bytes(),before)
        with self.assertRaises(ValueError):self.service.save_settings({'values':{'unknown':True}})
        self.service.save_keys({'keys':{'DEEPSEEK_API_KEY':'second-secret'}})
        self.assertIn('UNRELATED=keep-me',(self.base/'.env').read_text())
        self.assertNotIn('second-secret',json.dumps(self.service.state()))
        self.assertEqual(self.service.redact('second-secret secret-sentinel-do-not-expose'),'[disamarkan] [disamarkan]')

    def test_mode_saves_without_confirmation_and_start_is_separate(self):
        self.assertEqual(self.request('/api/v1/accounts/update','POST',{'account':'akun1','mode':'auto'})[0],200)
        self.assertEqual(self.service.account('akun1')['publish_mode'],'auto')
        self.assertEqual(self.service.state()['runs'],[])
        self.assertEqual(self.request('/api/v1/accounts/update','POST',{'account':'akun1','mode':'manual_review'})[0],200)
        self.assertEqual(self.service.account('akun1')['publish_mode'],'manual_review')
        self.service.update_account({'account':'akun1','mode':'auto'})
        with self.assertRaises(ValueError):self.service.start({'account':'akun1','kind':'configured'})
        self.assertEqual(self.service.jobs.list(),[])

    def test_scheduler_is_inactive_by_default_and_deduplicates_occurrences(self):
        self.service.schedule_tick(datetime(2026,10,29,10,0))
        self.assertEqual(self.service.jobs.list(),[])
        self.service.save_schedule({'account':'akun1','days':[3],'time':'10:00','enabled':True})
        with patch('botapp.jobs.subprocess.Popen') as popen:
            popen.return_value.poll.return_value=None
            self.service.schedule_tick(datetime(2026,10,29,10,0))
            self.service.schedule_tick(datetime(2026,10,29,10,0))
            self.service.schedule_tick(datetime(2026,10,29,10,1))
            self.assertEqual(popen.call_count,1)
            self.assertIsInstance(popen.call_args.args[0],list)
        runs=self.service.jobs.list();self.assertEqual(len(runs),1)
        self.service.jobs.stop(runs[0]['id'])
        self.assertEqual(self.service.jobs.list()[0]['stop_requested'],1)
        with self.assertRaises(ValueError):self.service.save_schedule({'account':'akun1','days':[3],'time':'08:00','enabled':True})

    def test_stream_upload_scan_duplicate_and_path_traversal(self):
        headers={'Content-Type':'application/octet-stream','X-File-Name':'video-baru.mp4'}
        response=self.request('/api/v1/upload?product=1','POST',headers=headers,raw=b'video-content')
        self.assertEqual(response[0],200,response[2])
        self.assertEqual((self.folder/'video-baru.mp4').read_bytes(),b'video-content')
        self.assertEqual(self.service.state()['stats']['unused'],1)
        response=self.request('/api/v1/upload?product=1','POST',headers=headers,raw=b'overwrite')
        self.assertEqual(response[0],400)
        self.assertEqual((self.folder/'video-baru.mp4').read_bytes(),b'video-content')
        headers['X-File-Name']='..%2Fescape.mp4'
        self.assertEqual(self.request('/api/v1/upload?product=1','POST',headers=headers,raw=b'data')[0],400)
        self.assertFalse((self.folder.parent/'escape.mp4').exists())

    def test_draft_test_reserves_job_without_changing_main_publish_mode(self):
        (self.folder/'video.mp4').write_bytes(b'video')
        self.service.scan({'account':'akun1'})
        self.service.update_account({'account':'akun1','mode':'auto','confirmed':True})
        with patch('botapp.jobs.subprocess.Popen') as process:
            process.return_value.poll.return_value=None
            self.service.start({'account':'akun1','kind':'draft_test','content_id':1})
            with self.assertRaises(ValueError):self.service.start({'account':'akun1','kind':'draft_test','content_id':1})
        self.assertEqual(self.service.account('akun1')['publish_mode'],'auto')
        self.assertEqual(self.service.jobs.list()[0]['kind'],'draft_test')
        before=effective_settings(self.base,'akun1')
        with self.assertRaises(ValueError):self.service.update_account({'account':'akun1','mode':'manual_review','values':{'max_per_day':7}})
        self.assertEqual(effective_settings(self.base,'akun1'),before)
        self.assertEqual(self.service.account('akun1')['publish_mode'],'auto')

    def test_e2e_http_requires_explicit_real_submit_confirmation(self):
        (self.folder/'video.mp4').write_bytes(b'video')
        self.service.scan({'account':'akun1'})
        data={'account':'akun1','kind':'e2e_test','content_id':1,'test_options':{'mode':'auto','music':'off'}}
        with patch('botapp.jobs.subprocess.Popen') as process:
            process.return_value.poll.return_value=None
            self.assertEqual(self.request('/api/v1/runs/start','POST',data)[0],400)
            self.assertEqual(self.request('/api/v1/runs/start','POST',{**data,'test_options':{'mode':'schedule'}})[0],400)
            process.assert_not_called()
            status,_,body=self.request('/api/v1/runs/start','POST',{**data,'confirmed':True})
            self.assertEqual(status,200,body)
            process.assert_called_once()
        self.assertEqual(self.service.account('akun1')['publish_mode'],'manual_review')

    def test_accounts_can_share_the_same_limits_and_start_time(self):
        self.service.create_account({'name':'same-settings.id'})
        values={'max_per_day':12,'window_start_hour':13,'window_start_minute':0,'min_interval_minutes':60,'max_interval_minutes':90}
        for account in ['akun1','akun2']:
            self.service.update_account({'account':account,'mode':'manual_review','values':values})
        state=self.service.state()
        self.assertEqual(state['accounts'][0]['settings'],state['accounts'][1]['settings'])
        self.assertEqual(state['runs'],[])

    def test_real_worker_with_empty_queue_finishes_without_api_or_upload(self):
        self.service.save_settings({'values':{'window_start_hour':0}})
        result=self.service.start({'account':'akun1','kind':'configured','limit':1})
        deadline=time.monotonic()+12
        while time.monotonic()<deadline:
            run=next(r for r in self.service.jobs.list() if r['id']==result['run_id'])
            if run['status'] not in ('queued','running','stopping'):
                break
            time.sleep(.1)
        self.assertEqual(run['status'],'finished',self.service.jobs.logs(result['run_id']))
        self.assertIn('SKIP',self.service.jobs.logs(result['run_id']))
        self.assertEqual(self.service.state()['history'],[])
        self.assertEqual(self.service.state()['queue'],[])

    def test_account_settings_dialog_api_preserves_other_accounts(self):
        self.service.create_account({'name':'second.id'})
        self.service.save_settings({'account':'akun1','values':{'min_interval_minutes':70,'max_interval_minutes':95}})
        original=self.service.state()
        values={**original['config']['accounts']['akun1'],'max_per_day':7}
        self.assertEqual(self.request('/api/v1/accounts/update','POST',{'account':'akun1','mode':'manual_review','values':values})[0],200)
        state=json.loads(self.request('/api/v1/state')[2])
        self.assertEqual(state['accounts'][0]['settings']['max_per_day'],7)
        self.assertEqual(state['accounts'][0]['settings']['min_interval_minutes'],70)
        self.assertEqual(state['accounts'][0]['settings']['max_interval_minutes'],95)
        self.assertEqual(state['accounts'][1]['settings'],original['accounts'][1]['settings'])
        field=next(f for f in state['schema'] if f['key']=='max_per_day')
        before=(self.base/'config/control-settings.json').read_bytes()
        for bad in [field['min']-1,field['max']+1,1.5,True,'7']:
            self.assertEqual(self.request('/api/v1/settings','POST',{'account':'akun1','values':{**values,'max_per_day':bad}})[0],400)
            self.assertEqual((self.base/'config/control-settings.json').read_bytes(),before)
        for interval in [{'min_interval_minutes':59},{'max_interval_minutes':59},{'min_interval_minutes':100,'max_interval_minutes':90}]:
            self.assertEqual(self.request('/api/v1/accounts/update','POST',{'account':'akun1','mode':'auto','confirmed':True,'values':{**values,**interval}})[0],400)
            self.assertEqual((self.base/'config/control-settings.json').read_bytes(),before)
            self.assertEqual(self.service.account('akun1')['publish_mode'],'manual_review')
        self.assertEqual(state['runs'],[])
        self.assertEqual(state['config']['schedules'],{})

    def test_posting_clock_minutes_and_schedule_boundary(self):
        status,_,_=self.request('/api/v1/accounts/update','POST',{'account':'akun1','mode':'manual_review','values':{'window_start_hour':21,'window_start_minute':30}})
        self.assertEqual(status,200)
        runtime=effective_settings(self.base,'akun1')
        self.assertEqual((runtime['window_start_hour'],runtime['window_start_minute']),(21,30))
        schedule={'account':'akun1','days':[0],'time':'21:29','enabled':False}
        self.assertEqual(self.request('/api/v1/schedules','POST',schedule)[0],400)
        self.assertEqual(self.request('/api/v1/schedules','POST',{**schedule,'time':'21:30'})[0],200)
        self.assertEqual(self.request('/api/v1/settings','POST',{'account':'akun1','values':{'window_start_minute':60}})[0],400)

    def test_global_browser_cap_is_validated_and_not_per_account(self):
        state=self.service.state()
        field=next(f for f in state['schema'] if f['key']=='max_parallel_browsers')
        self.assertEqual(state['effective']['max_parallel_browsers'],field['default'])
        self.assertEqual(self.request('/api/v1/settings','POST',{'values':{'max_parallel_browsers':1}})[0],200)
        self.assertEqual(self.service.state()['accounts'][0]['settings']['max_parallel_browsers'],1)
        for bad in [field['min']-1,field['max']+1,True]:
            self.assertEqual(self.request('/api/v1/settings','POST',{'values':{'max_parallel_browsers':bad}})[0],400)
        self.assertEqual(self.request('/api/v1/settings','POST',{'account':'akun1','values':{'max_parallel_browsers':1}})[0],400)
        self.assertEqual(self.service.state()['runs'],[])

    def test_custom_link_name_per_account_validation_and_default(self):
        self.service.create_account({'name':'custom-link.id'})
        state=self.service.state()
        field=next(f for f in state['schema'] if f['key']=='product_link_name')
        self.assertEqual(state['accounts'][0]['settings']['product_link_name'],field['default'])
        self.assertEqual(self.request('/api/v1/accounts/update','POST',{'account':'akun2','mode':'manual_review','values':{'product_link_name':'Pilihan Hari Ini'}})[0],200)
        self.assertEqual(effective_settings(self.base,'akun2')['product_link_name'],'Pilihan Hari Ini')
        self.assertEqual(effective_settings(self.base,'akun1')['product_link_name'],field['default'])
        before=(self.base/'config/control-settings.json').read_bytes()
        for bad in ['', '  ', 'a'*(field['maxLength']+1), 'baris\nbaru', 123]:
            self.assertEqual(self.request('/api/v1/accounts/update','POST',{'account':'akun2','mode':'manual_review','values':{'product_link_name':bad}})[0],400)
            self.assertEqual((self.base/'config/control-settings.json').read_bytes(),before)
        self.assertEqual(self.service.state()['runs'],[])

    def test_music_enabled_is_per_account_and_requires_boolean(self):
        self.service.create_account({'name':'music-test.id'})
        self.assertIs(effective_settings(self.base,'akun1')['music_enabled'],False)
        self.assertEqual(self.request('/api/v1/accounts/update','POST',{'account':'akun1','mode':'manual_review','values':{'music_enabled':True}})[0],200)
        self.assertIs(effective_settings(self.base,'akun1')['music_enabled'],True)
        self.assertIs(effective_settings(self.base,'akun2')['music_enabled'],False)
        before=(self.base/'config/control-settings.json').read_bytes()
        for bad in ['true','false',0,1,None]:
            self.assertEqual(self.request('/api/v1/accounts/update','POST',{'account':'akun1','mode':'manual_review','values':{'music_enabled':bad}})[0],400)
            self.assertEqual((self.base/'config/control-settings.json').read_bytes(),before)
        self.assertEqual(self.service.state()['runs'],[])

    def test_card_product_name_is_independent_and_can_be_cleared(self):
        self.service.create_account({'name':'label-only.id'})
        original=effective_settings(self.base,'akun1')['product_link_name']
        for label in ['jingsu','manjakani','']:
            self.assertEqual(self.request('/api/v1/accounts/update','POST',{'account':'akun1','mode':'manual_review','values':{'product_name':label}})[0],200)
            state=json.loads(self.request('/api/v1/state')[2])
            self.assertEqual(state['accounts'][0]['settings']['product_name'],label)
            self.assertEqual(state['accounts'][0]['settings']['product_link_name'],original)
            self.assertEqual(state['accounts'][1]['settings']['product_name'],'')
        self.service.update_account({'account':'akun1','mode':'manual_review','values':{'product_link_name':'Cek Produk Pilihan'}})
        self.assertEqual(effective_settings(self.base,'akun1')['product_name'],'')
        field=next(f for f in self.service.state()['schema'] if f['key']=='product_name')
        for bad in ['x'*(field['maxLength']+1),'dua\nbaris',123]:
            self.assertEqual(self.request('/api/v1/accounts/update','POST',{'account':'akun1','mode':'manual_review','values':{'product_name':bad}})[0],400)
        self.assertEqual(self.service.state()['runs'],[])

    @unittest.skipUnless(shutil.which('osascript'),'JavaScriptCore test hanya tersedia di macOS')
    def test_frontend_templates_and_dialogs_with_dom_stub(self):
        fixture=self.base/'ui-state.json'
        fixture.write_text(json.dumps(self.service.state()))
        result=subprocess.run(['osascript','-l','JavaScript',str(ROOT/'tests/ui_smoke.js'),str(ROOT),str(fixture)],capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('6 menu, 3 tab',result.stdout)

    def test_open_account_folder_without_product_ids(self):
        self.service.create_account({'name':'new-folder.id'})
        state=self.service.state();account=state['accounts'][1]
        self.assertEqual(account['product_ids'],[])
        self.assertFalse(any(p['account']=='akun2' for p in state['products']))
        with patch.object(self.service,'runner',return_value=MagicMock(returncode=0)) as runner:
            self.assertEqual(self.request('/api/v1/folders/open','POST',{'path':account['source_path']})[0],200)
            opener='open' if sys.platform=='darwin' else 'xdg-open'
            self.assertEqual(runner.call_args.args[0],[opener,account['source_path']])
            self.assertEqual(self.request('/api/v1/folders/open','POST',{'path':str(self.base/'.env')})[0],400)
            self.assertEqual(self.request('/api/v1/folders/open','POST',{'path':str(self.base/'content/unregistered')})[0],400)
            runner.assert_called_once()
        self.assertEqual(self.service.state()['runs'],[])

    def test_empty_install_bootstraps_database_without_accounts_or_schedules(self):
        empty=ControlService(self.base/'fresh-install')
        state=empty.state()
        self.assertEqual(state['accounts'],[])
        self.assertEqual(state['config']['schedules'],{})
        self.assertEqual(state['runs'],[])


if __name__=='__main__':unittest.main()
