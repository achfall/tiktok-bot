"""Kontrak konfigurasi bersama UI/CLI. Tambahkan field di registry ini untuk form baru."""
import copy
import json
import os
from pathlib import Path
import re
import tempfile
import threading

SCHEMA_VERSION = 1
FIELDS = [
    {'key':'max_per_day','label':'Batas video per hari','type':'integer','default':15,'min':1,'max':100,'group':'orchestrator','help':'Folder akun: total video per hari. Konsep lama: maksimal satu video per produk per hari.'},
    {'key':'window_start_hour','label':'Mulai posting pukul','type':'integer','default':10,'min':0,'max':23,'group':'orchestrator','ui':'clock','minute_key':'window_start_minute','placeholder':'10:00 atau 21:00','help':'Jam lokal, format HH:MM (24 jam).'},
    {'key':'window_start_minute','label':'Menit mulai posting','type':'integer','default':0,'min':0,'max':59,'group':'orchestrator','ui':'clock-minute'},
    {'key':'min_interval_minutes','label':'Jeda minimum (menit)','type':'integer','default':60,'min':60,'max':1440,'group':'orchestrator'},
    {'key':'max_interval_minutes','label':'Jeda maksimum (menit)','type':'integer','default':90,'min':60,'max':1440,'group':'orchestrator'},
    {'key':'step_delay','label':'Jeda langkah browser (detik)','type':'number','default':1.0,'min':0.5,'max':10,'group':'orchestrator','help':'Beri waktu halaman TikTok merespons.'},
    {'key':'product_name','label':'Nama produk','type':'text','default':'','allowEmpty':True,'maxLength':80,'group':'orchestrator','placeholder':'Contoh: jingsu atau manjakani','help':'Label di kartu akun. Terpisah dari nama link produk pada video.'},
    {'key':'product_link_name','label':'Nama link produk','type':'text','default':'Promo Tiktok Disini','maxLength':30,'group':'orchestrator','layout':'full','help':'Teks pada link produk di video. Berlaku untuk semua produk akun ini.'},
    {'key':'music_enabled','label':'Musik dari Favorit','type':'boolean','default':False,'group':'orchestrator','layout':'full','help':'Pilih Favorit berurutan lalu mengulang, dengan volume bawaan TikTok. Favorit kosong/tidak tersedia menghentikan proses sebelum submit.'},
    {'key':'max_parallel_browsers','label':'Maksimum Chrome bersamaan','type':'integer','default':2,'min':1,'max':10,'group':'browser','help':'Berlaku untuk semua akun. Akun lain menunggu giliran; Chrome yang dibuka manual tidak termasuk.'},
    {'key':'gemini_model','label':'Model Gemini','type':'text','default':'gemini-3.8-flash','maxLength':100,'group':'caption','help':'Gunakan model yang tersedia untuk API key lo.'},
]
REGISTRY = {f['key']:f for f in FIELDS}
LOCK = threading.RLock()


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
            if field['type']=='integer' and int(value)!=value:
                raise ValueError(f'{field["label"]} harus bilangan bulat.')
            if not field['min']<=value<=field['max']:
                raise ValueError(f'{field["label"]}: nilai {field["min"]}–{field["max"]}.')
            result[key]=int(value) if field['type']=='integer' else float(value)
        else:
            if not isinstance(value,str) or (not value.strip() and not field.get('allowEmpty')) or len(value)>field['maxLength'] or '\n' in value or '\r' in value:
                raise ValueError(f'{field["label"]} tidak valid.')
            result[key]=value.strip()
    effective={**defaults(),**result}
    if not partial and effective['min_interval_minutes']>effective['max_interval_minutes']:
        raise ValueError('Jeda minimum tidak boleh melebihi jeda maksimum.')
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
