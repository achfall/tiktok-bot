"""Kontrak konfigurasi bersama UI/CLI. Tambahkan field di registry ini untuk form baru."""
import copy
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import math
import fcntl
from contextlib import contextmanager

SCHEMA_VERSION = 1
SCHEDULE_MIN_LEAD_MINUTES = 35


def first_schedule_minutes(settings):
    """Offset dari tengah malam; hasil dapat berada pada hari berikutnya."""
    start = settings['window_start_hour'] * 60 + settings['window_start_minute']
    earliest = ((start + SCHEDULE_MIN_LEAD_MINUTES + 4) // 5) * 5
    requested = settings['schedule_start_hour'] * 60 + settings['schedule_start_minute']
    if earliest >= 1440 and requested < start:
        requested += 1440
    return max(earliest, requested)


FIELDS = [
    {'key':'schedule_start_hour','label':'Jam tayang pertama (WIB)','type':'integer','default':17,'min':0,'max':23,'group':'orchestrator','ui':'clock','minute_key':'schedule_start_minute','placeholder':'17:30','help':'Minimal 35 menit dari mulai; dibulatkan ke atas per 5 menit.','min_lead_minutes':SCHEDULE_MIN_LEAD_MINUTES},
    {'key':'schedule_start_minute','label':'Menit tayang pertama','type':'integer','default':30,'min':0,'max':55,'group':'orchestrator','ui':'clock-minute','step':5},
    {'key':'schedule_interval_minutes','label':'Jeda tayang (menit)','type':'integer','default':60,'min':5,'max':1440,'step':5,'group':'orchestrator','help':'Kelipatan 5 menit.'},
    {'key':'chrome_profile_directory','label':'Profil internal Chrome','type':'text','default':'Default','maxLength':40,'group':'orchestrator','help':'Default atau Profile N sesuai akun login.'},
    {'key':'max_per_day','label':'Batas video per hari','type':'integer','default':15,'min':1,'max':100,'group':'orchestrator','help':'Kuota bersama publish, draft, dan Schedule.'},
    {'key':'window_start_hour','label':'Mulai posting pukul','type':'integer','default':10,'min':0,'max':23,'group':'orchestrator','ui':'clock','minute_key':'window_start_minute','placeholder':'10:00 atau 21:00','help':'Jam lokal, format 24 jam.'},
    {'key':'window_start_minute','label':'Menit mulai posting','type':'integer','default':0,'min':0,'max':59,'group':'orchestrator','ui':'clock-minute'},
    {'key':'min_interval_minutes','label':'Jeda minimum (menit)','type':'integer','default':60,'min':60,'max':1440,'group':'orchestrator'},
    {'key':'max_interval_minutes','label':'Jeda maksimum (menit)','type':'integer','default':90,'min':60,'max':1440,'group':'orchestrator'},
    {'key':'step_delay','label':'Jeda langkah browser (detik)','type':'number','default':1.0,'min':0.5,'max':10,'group':'orchestrator','help':'Jeda setelah klik dan sebelum langkah.'},
    {'key':'product_name','label':'Nama produk','type':'text','default':'','allowEmpty':True,'maxLength':80,'group':'orchestrator','placeholder':'Contoh: jingsu atau manjakani','help':'Label pada kartu akun.'},
    {'key':'product_link_name','label':'Nama link produk','type':'text','default':'Promo Tiktok Disini','maxLength':30,'group':'orchestrator','layout':'full','help':'Teks link produk untuk akun ini.'},
    {'key':'music_enabled','label':'Musik dari Favorit','type':'boolean','default':False,'group':'orchestrator','layout':'full','help':'Favorit diputar berurutan. Isi Favorit terlebih dahulu.'},
    {'key':'max_parallel_browsers','label':'Maksimum Chrome bersamaan','type':'integer','default':2,'min':1,'max':10,'group':'browser','help':'Batas browser otomatis untuk seluruh akun.'},
    {'key':'gemini_model','label':'Model Gemini','type':'text','default':'gemini-3.8-flash','maxLength':100,'group':'caption','help':'Model caption yang tersedia di akun API.'},
]
REGISTRY = {f['key']:f for f in FIELDS}
LOCK = threading.RLock()


@contextmanager
def configuration_lock(base):
    """Serialisasi read-modify-write panel dan CLI lintas proses."""
    with LOCK:
        folder=Path(base)/'config'
        folder.mkdir(parents=True,exist_ok=True)
        with (folder/'.settings.lock').open('a+') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            yield


def defaults():
    return {f['key']:f['default'] for f in FIELDS}


def validate_values(values, partial=False):
    if not isinstance(values,dict) or set(values)-set(REGISTRY):
        raise ValueError('Pengaturan tidak dikenali; muat ulang halaman.')
    result = {} if partial else defaults()
    for key,value in values.items():
        field=REGISTRY[key]
        if field['type']=='boolean':
            if not isinstance(value,bool):raise ValueError(f'{field["label"]} harus aktif atau nonaktif.')
            result[key]=value
        elif field['type'] in ('integer','number'):
            if isinstance(value,bool) or not isinstance(value,(int,float)):
                raise ValueError(f'{field["label"]} harus berupa angka.')
            if isinstance(value,float) and not math.isfinite(value):
                raise ValueError(f'{field["label"]} harus berupa angka terbatas.')
            if field['type']=='integer' and int(value)!=value:
                raise ValueError(f'{field["label"]} harus bilangan bulat.')
            if not field['min']<=value<=field['max']:
                raise ValueError(f'{field["label"]}: nilai {field["min"]}–{field["max"]}.')
            result[key]=int(value) if field['type']=='integer' else float(value)
            if field.get('step') and result[key] % field['step']:
                raise ValueError(f'{field["label"]} harus kelipatan {field["step"]}.')
        else:
            if not isinstance(value,str) or (not value.strip() and not field.get('allowEmpty')) or len(value)>field['maxLength'] or '\n' in value or '\r' in value:
                raise ValueError(f'{field["label"]} tidak valid.')
            if key=='chrome_profile_directory' and not re.fullmatch(r'Default|Profile [1-9][0-9]*',value):
                raise ValueError('Profil internal harus Default atau Profile N.')
            result[key]=value.strip()
    effective={**defaults(),**result}
    if not partial and effective['min_interval_minutes']>effective['max_interval_minutes']:
        raise ValueError('Jeda minimum tidak boleh melebihi jeda maksimum.')
    if not partial:
        first = first_schedule_minutes(result) % 1440
        result['schedule_start_hour'], result['schedule_start_minute'] = divmod(first, 60)
    return result


def load_document(base):
    path=Path(base)/'config/control-settings.json'
    if not path.exists():return {'version':SCHEMA_VERSION,'values':{},'accounts':{},'schedules':{}}
    data=json.loads(path.read_text())
    if data.get('version')!=SCHEMA_VERSION:raise ValueError('Versi konfigurasi tidak didukung; perbarui aplikasi.')
    validate_values(data.get('values',{}))
    return data


def effective_settings(base,account=None):
    data=load_document(base)
    baseline=defaults()
    if 'gemini_model' not in data.get('values',{}):
        from dotenv import dotenv_values
        model=os.environ.get('GEMINI_MODEL') or dotenv_values(Path(base)/'.env').get('GEMINI_MODEL')
        if model:baseline['gemini_model']=model
    merged={**baseline,**data.get('values',{}),**data.get('accounts',{}).get(account,{})}
    return validate_values(merged)


def save_document(base,document):
    data=copy.deepcopy(document)
    if data.get('version')!=SCHEMA_VERSION:raise ValueError('Versi konfigurasi tidak cocok.')
    data['values']=validate_values(data.get('values',{}),partial=True)
    for account,values in data.get('accounts',{}).items():
        if not re.fullmatch(r'akun[1-9][0-9]*',account):raise ValueError('ID akun tidak valid.')
        data['accounts'][account]=validate_values(values,partial=True)
        validate_values({**defaults(),**data['values'],**data['accounts'][account]})
    validate_values({**defaults(),**data['values']})
    folder=Path(base)/'config';folder.mkdir(parents=True,exist_ok=True)
    fd,temp=tempfile.mkstemp(prefix='.settings-',dir=folder)
    try:
        with os.fdopen(fd,'w') as stream:
            json.dump(data,stream,ensure_ascii=False,indent=2)
            stream.flush();os.fsync(stream.fileno())
        os.replace(temp,folder/'control-settings.json')
    finally:
        if os.path.exists(temp):os.unlink(temp)
