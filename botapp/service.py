"""Lapisan aplikasi bersama HTTP/CLI. Semua operasi bot memakai CLI/fungsi domain."""
from contextlib import closing
from datetime import datetime
from pathlib import Path
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import urllib.request
from dotenv import dotenv_values, set_key
from botapp import API_VERSION

CODE_ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(CODE_ROOT/'scripts'))
from bot_settings import FIELDS, configuration_lock, load_document, save_document, effective_settings, validate_values
from botapp.jobs import Jobs
from diagnostic_options import MIN_LEAD_MINUTES, MAX_SCHEDULE_DAYS, SCHEDULE_STEP_MINUTES
from account_sources import source as account_source, read_ids, save_ids

NAME=re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}')
KEYS=('GEMINI_API_KEY','DEEPSEEK_API_KEY')


class ControlService:
    def __init__(self,base,command_runner=None):
        self.base=Path(base).resolve();self.db=self.base/'db/tiktok_bot.db'
        if not self.db.exists():
            self.db.parent.mkdir(parents=True,exist_ok=True)
            with closing(sqlite3.connect(self.db)) as conn,conn:
                conn.executescript((CODE_ROOT/'scripts/db/schema.sql').read_text())
        self.runner=command_runner or subprocess.run
        self.jobs=Jobs(self.base,CODE_ROOT)
        self.mutation_lock=threading.RLock()

    def connection(self):
        if not self.db.is_file():raise ValueError('Database bot belum tersedia. Jalankan setup akun dahulu.')
        conn=sqlite3.connect(self.db.as_uri()+'?mode=ro',uri=True,timeout=15)
        conn.row_factory=sqlite3.Row
        return conn

    def redact(self,text):
        for key,value in dotenv_values(self.base/'.env').items():
            if key in KEYS and value:text=text.replace(value,'[disamarkan]')
        return text

    def command(self,script,*args):
        path=CODE_ROOT/'scripts'/script
        result=self.runner([sys.executable,str(path),*[str(v) for v in args]],cwd=CODE_ROOT,env={**os.environ,'TIKTOK_BOT_HOME':str(self.base),'TIKTOK_BOT_PYTHON':sys.executable},text=True,capture_output=True,timeout=45)
        output=self.redact((result.stdout or '')+(result.stderr or ''))
        if result.returncode:raise ValueError(output[-1800:] or 'Operasi gagal.')
        return output.strip()

    def account(self,slug):
        with closing(self.connection()) as conn:
            row=conn.execute('SELECT a.*,g.name concept,g.folder_path concept_path FROM accounts a LEFT JOIN content_groups g ON g.id=a.content_group_id WHERE a.slug=?',(slug,)).fetchone()
        if not row:raise ValueError('Akun tidak ditemukan.')
        return dict(row)

    def product(self,pid):
        if isinstance(pid,bool):raise ValueError('Produk tidak valid.')
        with closing(self.connection()) as conn:
            row=conn.execute('SELECT p.*,a.slug FROM products p JOIN accounts a ON a.id=p.account_id WHERE p.id=?',(int(pid),)).fetchone()
        if not row:raise ValueError('Produk tidak ditemukan.')
        return dict(row)

    def idle(self,slug=None):
        if self.jobs.active(slug):raise ValueError('Hentikan proses akun terlebih dahulu sebelum mengubah konfigurasi sumber.')
        with closing(self.connection()) as conn:
            if slug:
                row=conn.execute("SELECT 1 FROM contents c JOIN accounts a ON a.id=c.claimed_by WHERE a.slug=? AND c.status IN ('claimed','submitting','uncertain')",(slug,)).fetchone()
            else:row=conn.execute("SELECT 1 FROM contents WHERE status IN ('claimed','submitting','uncertain')").fetchone()
        if row:raise ValueError('Masih ada video tertahan. Periksa halaman Antrean sebelum melanjutkan.')

    def state(self):
        with closing(self.connection()) as conn:
            accounts=[dict(r) for r in conn.execute('SELECT a.*,g.name concept,g.folder_path concept_path FROM accounts a LEFT JOIN content_groups g ON g.id=a.content_group_id ORDER BY a.id')]
            concepts=[dict(r) for r in conn.execute('SELECT * FROM content_groups ORDER BY name')]
            products=[dict(r) for r in conn.execute('SELECT p.*,a.slug account,g.name concept FROM products p JOIN accounts a ON a.id=p.account_id LEFT JOIN content_groups g ON g.id=a.content_group_id ORDER BY a.id,p.sort_order')]
            queue=[dict(r) for r in conn.execute('''SELECT c.*,p.folder_name product,a.slug claimed_account,
                COALESCE(u.chrome_profile_path,o.chrome_profile_path) used_profile FROM contents c
                JOIN products p ON p.id=c.product_id LEFT JOIN accounts a ON a.id=c.claimed_by
                LEFT JOIN accounts u ON u.id=c.used_by LEFT JOIN accounts o ON o.id=p.account_id
                ORDER BY c.id DESC LIMIT 250''')]
            history=[dict(r) for r in conn.execute('SELECT l.*,a.slug account,p.folder_name product FROM posts_log l JOIN accounts a ON a.id=l.account_id LEFT JOIN products p ON p.id=l.product_id ORDER BY l.id DESC LIMIT 150')]
            from schedule_store import history_metadata
            metadata=history_metadata(conn,[row['id'] for row in history])
            for row in history:
                row.update(metadata.get(row['id'],{}))
            stats={r['status']:r['n'] for r in conn.execute('SELECT status,COUNT(*) n FROM contents GROUP BY status')}
            for p in products:
                p['available']=conn.execute("SELECT COUNT(*) FROM contents WHERE status='unused' AND ((source_folder IS NOT NULL AND source_folder=?) OR (source_folder IS NULL AND product_id=?))",(p['folder_path'],p['id'])).fetchone()[0]
            for account in accounts:
                flat=account_source(conn,account['id'])
                account['source_path']=flat[0] if flat else None
                account['product_ids']=[];account['source_error']=None
                if flat:
                    try:account['product_ids']=read_ids(flat[0])
                    except (ValueError,OSError) as error:account['source_error']=str(error)
            for item in queue:
                owner=conn.execute('SELECT account_id FROM products WHERE id=?',(item['product_id'],)).fetchone()
                if owner and account_source(conn,owner[0]):
                    pair=conn.execute('SELECT tiktok_product_id FROM content_assignments WHERE content_id=?',(item['id'],)).fetchone()
                    if pair:item['product']='ID '+pair[0]
                    elif item['status']=='unused':item['product']='Belum dipasangkan'
        runs=self.jobs.list()
        active_runs=self.jobs.active()
        for account in accounts:
            account['name']=Path(account['chrome_profile_path']).name
            account['profile_exists']=Path(account['chrome_profile_path']).is_dir()
            account['running']=next((r for r in active_runs if r['account']==account['slug'] and r['status'] in ('queued','running','stopping')),None)
            account['settings']=effective_settings(self.base,account['slug'])
        for row in queue:
            row['filename']=Path(row['file_path']).name
            row['file_exists']=Path(row['file_path']).is_file()
            row['used_account']=Path(row['used_profile']).name if row.get('used_profile') else None
        for concept in concepts:
            concept['members']=[a['slug'] for a in accounts if a['content_group_id']==concept['id']]
        config=load_document(self.base)
        env=dotenv_values(self.base/'.env')
        for row in history:
            if row['error_message']:row['error_message']=self.redact(row['error_message'])
        return {'diagnostics':{'schedule_min_lead_minutes':MIN_LEAD_MINUTES,'schedule_max_days':MAX_SCHEDULE_DAYS,'schedule_step_minutes':SCHEDULE_STEP_MINUTES},'api_version':API_VERSION,'schema':FIELDS,'accounts':accounts,'concepts':concepts,'products':products,'queue':queue,'history':history,'stats':stats,'runs':runs,'config':config,'effective':effective_settings(self.base),'keys':{k:bool(env.get(k)) for k in KEYS},'local_time':datetime.now().isoformat(timespec='seconds'),'root':str(self.base),'scheduler_note':'Jadwal berjalan saat panel lokal aktif. Saat Mac sleep, proses aktif tertunda dan lanjut otomatis setelah bangun. Internet harus tersedia. Jadwal mulai yang terlewat tidak dijalankan otomatis.'}

    def create_account(self,data):
        name=str(data.get('name','')).strip();concept=str(data.get('concept','')).strip()
        if not NAME.fullmatch(name):raise ValueError('Nama TikTok: huruf, angka, titik, garis bawah atau tanda hubung.')
        with closing(self.connection()) as conn:
            rows=conn.execute('SELECT slug,chrome_profile_path FROM accounts').fetchall()
            if any(Path(r['chrome_profile_path']).name.casefold()==name.casefold() for r in rows):raise ValueError('Nama akun sudah digunakan.')
            if concept and not conn.execute('SELECT 1 FROM content_groups WHERE name=?',(concept,)).fetchone():raise ValueError('Konsep tidak ditemukan.')
        number=max([int(r['slug'][4:]) for r in rows if re.fullmatch(r'akun[1-9][0-9]*',r['slug'])]+[0])+1
        slug=f'akun{number}'
        args=['--name',name]
        if concept:args+=['--concept',concept]
        self.command('setup_account.py',slug,*args)
        return f'Akun {name} dibuat dalam mode draft. Buka Chrome untuk login TikTok.'

    def update_account(self,data):
        slug=data.get('account');self.account(slug);self.idle(slug)
        mode=data.get('mode')
        if mode not in ('auto','manual_review','schedule'):raise ValueError('Mode tidak valid.')
        values=None
        if 'values' in data:
            values=validate_values(data['values'],partial=True)
            if any(next(f for f in FIELDS if f['key']==key)['group']!='orchestrator' for key in values):
                raise ValueError('Pengaturan provider caption bersifat global.')
            validate_values({**effective_settings(self.base,slug),**values})
        concept=data.get('concept')
        if concept and concept!=self.account(slug)['concept']:
            self.command('manage_concepts.py','assign',slug,concept)
        with configuration_lock(self.base):
            config=load_document(self.base)
            import copy
            previous=copy.deepcopy(config)
            if values is not None:
                config.setdefault('accounts',{}).setdefault(slug,{}).update(values)
                save_document(self.base,config)
            try:
                self.command('setup_account.py',slug,'--publish-mode',mode)
            except BaseException:
                if values is not None:
                    save_document(self.base,previous)
                raise
        return 'Pengaturan akun disimpan. Berlaku untuk proses berikutnya.'

    def create_concept(self,data):
        name=str(data.get('name','')).strip()
        if not NAME.fullmatch(name):raise ValueError('Nama konsep tidak valid.')
        self.command('manage_concepts.py','create',name)
        return f'Konsep {name} dibuat. Hubungkan akun dan tambahkan produk untuk mengisi antrean.'

    def rename_concept(self,data):
        self.idle()
        self.command('manage_concepts.py','rename',str(data.get('name','')),str(data.get('new_name','')))
        return 'Nama konsep dan seluruh path terkait diperbarui.'

    def create_product(self,data):
        slug=data.get('account');a=self.account(slug);self.idle(slug)
        with closing(self.connection()) as conn:
            if account_source(conn,a['id']):raise ValueError('Kelola daftar ID-produk.txt melalui pengaturan akun.')
        folder=str(data.get('folder','')).strip();pid=str(data.get('product_id','')).strip()
        if not folder or len(folder)>100 or Path(folder).name!=folder or folder in ('.','..','sudah-dipakai') or '/' in folder or '\\' in folder:raise ValueError('Nama folder produk tidak valid.')
        if not re.fullmatch(r'[0-9]{5,30}',pid):raise ValueError('ID produk harus berupa angka, bukan URL.')
        root=Path(a['concept_path']) if a['concept_path'] else self.base/'content'/Path(a['chrome_profile_path']).name
        target=root/folder
        self.safe_content(target)
        target.mkdir(parents=True,exist_ok=True)
        self.command('manage_products.py','add',slug,folder,pid)
        return 'Produk terdaftar. Tambahkan video lewat tombol Unggah video atau folder lokal.'

    def update_product(self,data):
        p=self.product(data.get('id'));self.idle(p['slug'])
        with closing(self.connection()) as conn:
            if account_source(conn,p['account_id']):raise ValueError('Kelola daftar ID-produk.txt melalui pengaturan akun.')
        if 'product_id' in data:
            pid=str(data['product_id']).strip()
            if not re.fullmatch(r'[0-9]{5,30}',pid):raise ValueError('ID produk tidak valid.')
            self.command('manage_products.py','set-id',p['slug'],p['folder_name'],pid)
        if 'active' in data:
            if not isinstance(data['active'],bool):raise ValueError('Status produk tidak valid.')
            self.command('manage_products.py','enable' if data['active'] else 'disable',p['slug'],p['folder_name'])
        return 'Produk diperbarui.'

    def update_source(self,data):
        a=self.account(data.get('account'));self.idle(a['slug'])
        with closing(self.connection()) as conn:flat=account_source(conn,a['id'])
        if not flat:raise ValueError('Migrasikan sumber akun terlebih dahulu melalui CLI.')
        text=data.get('ids')
        if not isinstance(text,str) or len(text)>100000:raise ValueError('Daftar ID tidak valid/terlalu besar.')
        save_ids(self.safe_content(flat[0]),text)
        self.command('db/scan_contents.py',a['slug'])
        return 'Daftar ID disimpan. Urutan berulang; pasangan video lama tetap dipertahankan.'

    def save_settings(self,data):
        values=validate_values(data.get('values',{}),partial=True)
        account=data.get('account')
        if account:
            self.account(account)
            self.idle(account)
            if any(next(f for f in FIELDS if f['key']==key)['group']!='orchestrator' for key in values):raise ValueError('Pengaturan provider caption bersifat global.')
        with configuration_lock(self.base):
            config=load_document(self.base)
            if account:config.setdefault('accounts',{})[account]=values
            else:config['values']=values
            save_document(self.base,config)
        return 'Konfigurasi disimpan. Berlaku untuk proses berikutnya.'

    def save_schedule(self,data):
        slug=data.get('account');self.account(slug)
        days=data.get('days');clock=data.get('time');enabled=data.get('enabled')
        if not isinstance(days,list) or not days or any(isinstance(v,bool) or not isinstance(v,int) or v not in range(7) for v in days):raise ValueError('Pilih minimal satu hari.')
        if not isinstance(clock,str) or not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]',clock):raise ValueError('Jam jadwal tidak valid.')
        if not isinstance(enabled,bool):raise ValueError('Status jadwal tidak valid.')
        runtime=effective_settings(self.base,slug)
        if int(clock[:2])*60+int(clock[3:])<runtime['window_start_hour']*60+runtime['window_start_minute']:
            raise ValueError('Jam jadwal mendahului jendela posting. Ubah jam mulai di pengaturan orchestrator akun dahulu.')
        with configuration_lock(self.base):
            config=load_document(self.base)
            config.setdefault('schedules',{})[slug]={'days':sorted(set(days)),'time':clock,'enabled':enabled}
            save_document(self.base,config)
        return 'Jadwal disimpan'+(' dan aktif selama panel berjalan.' if enabled else ' dalam keadaan nonaktif.')

    def save_keys(self,data):
        values=data.get('keys',{})
        if not isinstance(values,dict) or set(values)-set(KEYS):raise ValueError('Provider API tidak dikenal.')
        values={key:value.strip() for key,value in values.items() if isinstance(value,str) and value.strip()}
        if any(len(v)>1024 or '\n' in v or '\r' in v for v in values.values()):raise ValueError('Format API key tidak valid.')
        if not values:return 'Tidak ada API key yang diubah.'
        with configuration_lock(self.base):
            self.base.mkdir(parents=True,exist_ok=True)
            fd,name=tempfile.mkstemp(prefix='.env-edit-',dir=self.base)
            try:
                with os.fdopen(fd,'w') as stream:
                    path=self.base/'.env'
                    stream.write(path.read_text() if path.exists() else '')
                for key,value in values.items():set_key(name,key,value)
                os.chmod(name,0o600);os.replace(name,self.base/'.env')
            finally:
                if os.path.exists(name):os.unlink(name)
        return 'API key diperbarui. Nilainya tidak dikirim kembali ke browser.'

    def safe_content(self,path):
        resolved=Path(path).resolve();root=(self.base/'content').resolve()
        if resolved==root or root not in resolved.parents:raise ValueError('Lokasi harus berada di folder konten proyek.')
        return resolved

    def upload_target(self,product_id,filename):
        product=self.product(product_id)
        if not filename or Path(filename).name!=filename or '/' in filename or '\\' in filename or len(filename)>240 or Path(filename).suffix.lower() not in ('.mp4','.mov'):raise ValueError('Pilih file MP4/MOV dengan nama valid.')
        folder=self.safe_content(product['folder_path']);folder.mkdir(parents=True,exist_ok=True)
        target=folder/filename
        if target.exists():raise ValueError('Nama file sudah ada. Ganti nama file baru; file lama tidak ditimpa.')
        return product,target

    def scan(self,data):
        slug=data.get('account');self.account(slug)
        return self.command('db/scan_contents.py',slug)

    def open_chrome(self,data):
        a=self.account(data.get('account'))
        env={**os.environ,'TIKTOK_BOT_HOME':str(self.base),'TIKTOK_BOT_PYTHON':sys.executable}
        result=self.runner(['bash',str(CODE_ROOT/'scripts/start-chrome-profile.sh'),a['slug'],str(a['cdp_port'])],cwd=CODE_ROOT,env=env,capture_output=True,text=True,timeout=10)
        if result.returncode:raise ValueError(self.redact(result.stderr or 'Chrome tidak bisa dibuka.'))
        return f'Chrome {Path(a["chrome_profile_path"]).name} diminta dibuka. Login TikTok secara manual.'

    def chrome_status(self,data):
        a=self.account(data.get('account'))
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{a["cdp_port"]}/json/version',timeout=2) as response:
                import json
                version=json.load(response).get('Browser','Chrome')
            return {'connected':True,'message':f'{version} terhubung. Akun TikTok yang login perlu diperiksa manual.'}
        except OSError:return {'connected':False,'message':'Chrome belum dapat dihubungi. Buka Chrome khusus akun terlebih dahulu.'}

    def start(self,data,scheduled_key=None):
        a=self.account(data.get('account'))
        kind=data.get('kind','configured')
        if kind not in ('configured','draft_test','e2e_test'):raise ValueError('Jenis proses tidak valid.')
        test_options=None
        if kind=='e2e_test':
            from diagnostic_options import validate_options
            test_options=validate_options(data.get('test_options'))
            if test_options['mode'] in ('auto','schedule') and data.get('confirmed') is not True:
                raise ValueError('Konfirmasi publish/Schedule tes diperlukan.')
        if not a['active']:raise ValueError('Akun tidak aktif.')
        with closing(self.connection()) as conn:
            held=conn.execute("SELECT 1 FROM contents WHERE claimed_by=? AND status IN ('claimed','submitting','uncertain')",(a['id'],)).fetchone()
            if held:raise ValueError('Akun mempunyai video tertahan. Selesaikan pemeriksaan antrean dahulu.')
        if kind=='configured' and a['publish_mode'] in ('auto','schedule') and data.get('confirmed') is not True:raise ValueError('Konfirmasi publish/Schedule diperlukan.')
        limit_count=data.get('limit')
        if limit_count is not None and (isinstance(limit_count,bool) or not isinstance(limit_count,int) or not 1<=limit_count<=100):raise ValueError('Batas proses 1–100.')
        content_id=None
        if kind in ('draft_test','e2e_test'):
            content_id=data.get('content_id')
            if isinstance(content_id,bool) or not isinstance(content_id,int) or content_id<1:raise ValueError('Pilih video untuk tes.')
            with closing(self.connection()) as conn:
                row=conn.execute('SELECT file_path,source_folder,product_id FROM contents WHERE id=?',(content_id,)).fetchone()
                products=conn.execute('SELECT id,folder_path FROM products WHERE account_id=? AND active=1',(a['id'],)).fetchall()
            if not row or not Path(row['file_path']).is_file() or not any(row['source_folder']==p['folder_path'] if row['source_folder'] else row['product_id']==p['id'] for p in products):raise ValueError('Video tes tidak tersedia untuk akun ini.')
        options={'test_options':test_options} if kind=='e2e_test' else {}
        return {'run_id':self.jobs.launch(a['slug'],kind,limit_count,content_id,scheduled_key,**options),'message':'Proses dimulai. Pantau log; jangan jalankan akun yang sama lewat Terminal bersamaan.'}

    def resolve(self,data):
        if data.get('checked') is not True:raise ValueError('Periksa TikTok dan hentikan proses akun sebelum rekonsiliasi.')
        with closing(self.connection()) as conn:
            row=conn.execute('SELECT a.slug FROM contents c JOIN accounts a ON a.id=c.claimed_by WHERE c.id=?',(data.get('id'),)).fetchone()
        if row and self.jobs.active(row['slug']):raise ValueError('Proses akun masih aktif. Hentikan sebelum rekonsiliasi.')
        result=data.get('result')
        if result not in ('success','draft','scheduled','not-posted'):raise ValueError('Hasil pemeriksaan tidak valid.')
        return self.command('manage_queue.py','resolve',int(data['id']),'--result',result,'--checked-tiktok')

    def open_folder(self,data):
        allowed={str(self.base/'content')}
        with closing(self.connection()) as conn:
            allowed|={r[0] for r in conn.execute('SELECT folder_path FROM content_groups')}
            allowed|={r[0] for r in conn.execute('SELECT folder_path FROM products')}
            for account in conn.execute('SELECT id FROM accounts'):
                flat=account_source(conn,account[0])
                if flat:allowed.add(flat[0])
        path=str(data.get('path',''))
        if path not in allowed:raise ValueError('Folder tidak terdaftar.')
        if not self.safe_content(path).is_dir():raise ValueError('Folder sumber tidak ditemukan.')
        command=['open',path] if sys.platform=='darwin' else ['xdg-open',path]
        result=self.runner(command,capture_output=True,text=True,timeout=10)
        if result.returncode:raise ValueError('Folder tidak bisa dibuka dari sesi ini. Gunakan path yang ditampilkan.')
        return 'Folder dibuka.'

    def dispatch(self,action,data):
        routes={'sources/update':self.update_source,'accounts/create':self.create_account,'accounts/update':self.update_account,'concepts/create':self.create_concept,'concepts/rename':self.rename_concept,'products/create':self.create_product,'products/update':self.update_product,'settings':self.save_settings,'schedules':self.save_schedule,'keys':self.save_keys,'scan':self.scan,'chrome/open':self.open_chrome,'chrome/status':self.chrome_status,'runs/start':self.start,'queue/resolve':self.resolve,'folders/open':self.open_folder}
        if action=='runs/stop':return self.jobs.stop(str(data.get('id','')))
        if action not in routes:raise ValueError('Aksi tidak dikenal. Muat ulang aplikasi.')
        with self.mutation_lock:return routes[action](data)

    def schedule_tick(self,now=None):
        now=now or datetime.now()
        for slug,schedule in load_document(self.base).get('schedules',{}).items():
            if not schedule.get('enabled') or now.weekday() not in schedule.get('days',[]) or schedule.get('time')!=now.strftime('%H:%M'):continue
            try:
                with self.mutation_lock:
                    self.start({'account':slug,'kind':'configured','confirmed':True},scheduled_key=now.strftime('%Y-%m-%d %H:%M'))
            except ValueError:
                pass  # Run aktif/occurrence yang sama tidak diduplikasi pada tick berikutnya.
