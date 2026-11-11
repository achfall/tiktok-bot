"""Favorit TikTok: pemetaan UI dan rotasi per akun, tanpa aturan submit antrean."""
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3
import time
from upload_overlays import dismiss_known_tours


class MusicError(RuntimeError):
    pass


def reserve_music(base, account, content_id, favorites):
    """Pasangkan sekali sebelum edit; retry memakai lagu sama tanpa memutar cursor.

    DB metadata terpisah: tidak memigrasikan DB bot atau mengubah klaim konten.
    Urutan mengikuti daftar Favorites saat dipindai. Cursor numerik dimodulo
    panjang daftar baru bila operator mengubah daftar Favorites.
    """
    if not re.fullmatch(r'akun[1-9][0-9]*', account) or type(content_id) is not int or content_id < 1:
        raise MusicError('Identitas akun/video musik tidak valid.')
    keys = [(item['title'], item['detail']) for item in favorites]
    if not keys or any(not title for title, _ in keys) or len(set(keys)) != len(keys):
        raise MusicError('Favorites kosong atau judul/artis/durasinya ambigu.')
    path = Path(base)/'db/music_rotation.db'
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise MusicError('DB rotasi musik tidak boleh berupa symlink.')
    with closing(sqlite3.connect(path, timeout=30)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        version = conn.execute('PRAGMA user_version').fetchone()[0]
        if version == 0:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table'").fetchone():
                raise MusicError('DB rotasi musik tidak dikenali; data dipertahankan.')
            conn.execute('CREATE TABLE music_cursors(account TEXT PRIMARY KEY, next_index INTEGER NOT NULL)')
            conn.execute('CREATE TABLE music_choices(account TEXT NOT NULL, content_id INTEGER NOT NULL, title TEXT NOT NULL, detail TEXT NOT NULL, PRIMARY KEY(account,content_id))')
            conn.execute('PRAGMA user_version=1')
        elif version != 1:
            raise MusicError('Versi DB rotasi musik tidak didukung.')
        previous = conn.execute('SELECT title,detail FROM music_choices WHERE account=? AND content_id=?', (account, content_id)).fetchone()
        if previous:
            if previous not in keys:
                raise MusicError('Lagu pasangan video ini tidak ada di Favorites; retry tidak mengganti lagu otomatis.')
            return {'title': previous[0], 'detail': previous[1]}
        conn.execute('INSERT OR IGNORE INTO music_cursors VALUES (?,0)', (account,))
        cursor = conn.execute('SELECT next_index FROM music_cursors WHERE account=?', (account,)).fetchone()[0]
        title, detail = keys[cursor % len(keys)]
        conn.execute('INSERT INTO music_choices VALUES (?,?,?,?)', (account, content_id, title, detail))
        conn.execute('UPDATE music_cursors SET next_index=? WHERE account=?', (cursor+1, account))
        return {'title': title, 'detail': detail}


EDITOR = '.clip-forge-editor-container'
ROOT = '.MusicPanelContainer__root'
VISIBLE_JS = "const visible=e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0&&getComputedStyle(e).visibility!=='hidden'};"


def button_js(label, scope='document'):
    return f'''(() => {{{VISIBLE_JS}const root={scope};if(!root)return null;
    const buttons=Array.from(root.querySelectorAll('button')).filter(e=>(e.innerText||'').trim()==={json.dumps(label)}&&visible(e));
    if(buttons.length!==1||buttons[0].disabled||buttons[0].getAttribute('data-disabled')==='true')return null;
    buttons[0].scrollIntoView({{block:'center',behavior:'instant'}});const r=buttons[0].getBoundingClientRect();
    // Klik pusat bagian yang benar-benar berada di viewport. Header editor
    // dapat lebih lebar dari jendela sehingga pusat Save berada di luar layar.
    const left=Math.max(0,r.left),right=Math.min(window.innerWidth,r.right);
    const top=Math.max(0,r.top),bottom=Math.min(window.innerHeight,r.bottom);
    if(right<=left||bottom<=top)return null;
    const x=(left+right)/2,y=(top+bottom)/2;
    return buttons[0].contains(document.elementFromPoint(x,y))?{{x,y}}:null;}})()'''


SOUNDS_JS = button_js('Sounds')
FAVORITES_JS = button_js('Favorites', f'document.querySelector({json.dumps(ROOT)})')
SAVE_EDITOR_JS = button_js('Save', f'document.querySelector({json.dumps(EDITOR+" .clip-forge-editor-header-right")})')
CANCEL_EDITOR_JS = button_js('Cancel', f'document.querySelector({json.dumps(EDITOR+" .clip-forge-editor-header-right")})')
AUDIO_TITLES_JS = f"Array.from(document.querySelectorAll({json.dumps(EDITOR+' .AudioClip__title')})).map(e=>(e.innerText||'').trim())"
EDITOR_CLOSED_JS = f"!Array.from(document.querySelectorAll({json.dumps(EDITOR)})).some(e=>e.getBoundingClientRect().width>0)"
FAVORITES_RESET_JS = r'''(() => {const root=document.querySelector('.MusicPanelContainer__root');if(!root)return false;
 const panel=root.querySelector('[role="tabpanel"][data-state="active"]');if(!panel)return false;
 for(const e of [panel,...panel.querySelectorAll('*')])if(e.scrollHeight>e.clientHeight+2&&/auto|scroll/.test(getComputedStyle(e).overflowY))e.scrollTop=0;
 return true;})()'''


def favorites_js(scroll=False):
    return r'''(() => {
 const root=document.querySelector('.MusicPanelContainer__root');if(!root)return null;
 const tab=Array.from(root.querySelectorAll('button')).find(e=>(e.innerText||'').trim()==='Favorites');
 if(!tab||!(tab.getAttribute('data-state')==='active'||tab.getAttribute('aria-selected')==='true'))return null;
 const panel=root.querySelector('[role="tabpanel"][data-state="active"]');if(!panel)return null;
 const items=Array.from(panel.querySelectorAll('.MusicPanelMusicItem__wrap')).map(e=>({title:(e.querySelector('.MusicPanelMusicItem__infoBasicTitle')?.innerText||'').trim(),detail:(e.querySelector('.MusicPanelMusicItem__infoBasicDesc')?.innerText||'').trim()}));
 const scrollers=[panel,...panel.querySelectorAll('*')].filter(e=>e.clientHeight>0&&e.scrollHeight>e.clientHeight+2&&/auto|scroll/.test(getComputedStyle(e).overflowY));
 const scroller=scrollers.find(e=>e.querySelector('.MusicPanelMusicItem__wrap'))||scrollers[0];
 const top=scroller?scroller.scrollTop:0;
 const bottom=!scroller||top+scroller.clientHeight>=scroller.scrollHeight-3;
 const busy=!!panel.querySelector('[aria-busy="true"],[role="progressbar"]')||/(?:^|\n)Loading\.{0,3}(?:\n|$)/i.test(panel.innerText||'');
 SCROLL_ACTION
 return {items,bottom,busy,top};})()'''.replace('SCROLL_ACTION', "if(scroller&&!bottom)scroller.scrollTop+=Math.max(1,scroller.clientHeight*.8);" if scroll else '')


FAVORITES_READ_JS = favorites_js()
FAVORITES_NEXT_JS = favorites_js(True)
UPLOAD_READY_JS = rf'''(() => {{{VISIBLE_JS}if(!({EDITOR_CLOSED_JS}))return false;
 const buttons=Array.from(document.querySelectorAll('[data-e2e="post_video_button"],[data-e2e="save_draft_button"]')).filter(visible);
 return buttons.length>0&&buttons.every(e=>!e.disabled&&e.getAttribute('aria-disabled')!=='true')
 &&!Array.from(document.querySelectorAll('div,span')).some(e=>visible(e)&&e.children.length===0&&/^Loading(?:\.|…)*$/i.test((e.innerText||'').trim()));}})()'''


def add_music_js(choice):
    return f'''(() => {{{VISIBLE_JS}const root=document.querySelector({json.dumps(ROOT)});if(!root)return null;
 const panel=root.querySelector('[role="tabpanel"][data-state="active"]');if(!panel)return null;
 const rows=Array.from(panel.querySelectorAll('.MusicPanelMusicItem__wrap')).filter(e=>(e.querySelector('.MusicPanelMusicItem__infoBasicTitle')?.innerText||'').trim()==={json.dumps(choice['title'])}&&(e.querySelector('.MusicPanelMusicItem__infoBasicDesc')?.innerText||'').trim()==={json.dumps(choice['detail'])});
 if(rows.length!==1)return null;const icon=rows[0].querySelector('svg[data-icon="plus-bold"]');
 if(!icon)return null;icon.scrollIntoView({{block:'center',behavior:'instant'}});if(!visible(icon))return null;
 const disabled=icon.closest('[disabled],[aria-disabled="true"],[data-disabled="true"]');if(disabled)return null;
 const r=icon.getBoundingClientRect(),x=r.x+r.width/2,y=r.y+r.height/2;
 const hit=document.elementFromPoint(x,y),operation=icon.closest('.MusicPanelMusicItem__operation');
 return hit&&(icon.contains(hit)||(operation&&operation.contains(hit)))?{{x,y}}:null;}})()'''


class MusicUI:
    def __init__(self, ws, mid, evaluate, click):
        self.ws, self.mid, self.evaluate_fn, self.click_fn = ws, mid, evaluate, click

    def read(self, expression):
        # Termasuk polling, scroll Favorites, serta verifikasi Save. Target
        # kontrol baru dicari sesudah tour ditutup agar koordinat tidak usang.
        self.mid = dismiss_known_tours(self.ws, self.mid, self.evaluate_fn, self.click_fn, time.sleep, allow_backdrop=False)
        value = self.evaluate_fn(self.ws, expression, self.mid)
        self.mid += 1
        return value

    def wait(self, expression, predicate, label, attempts=30):
        for _ in range(attempts):
            value = self.read(expression)
            if predicate(value):
                return value
            time.sleep(1)
        raise MusicError(f'Musik: {label}; submit tidak dilakukan.')

    def click(self, expression, label):
        point = self.wait(expression, lambda value: isinstance(value, dict) and 'x' in value and 'y' in value, label)
        self.mid = self.click_fn(self.ws, point['x'], point['y'], self.mid)

    def favorites(self):
        self.wait(FAVORITES_RESET_JS, lambda value: value is True, 'daftar Favorites belum siap')
        choices, previous, stable = {}, None, 0
        for _ in range(120):
            state = self.read(FAVORITES_READ_JS)
            if not isinstance(state, dict) or state.get('busy'):
                time.sleep(1)
                continue
            page_keys = [(item['title'], item['detail']) for item in state['items']]
            if len(set(page_keys)) != len(page_keys) or any(not key[0] for key in page_keys):
                raise MusicError('Daftar Favorites memiliki lagu ambigu; submit tidak dilakukan.')
            for key, item in zip(page_keys, state['items']):
                choices.setdefault(key, item)
            signature = (tuple(choices), state['top'], state['bottom'])
            stable = stable+1 if signature == previous else 0
            if state['bottom'] and choices and stable >= 2:
                return list(choices.values())
            if state['bottom'] and not choices and stable >= 10:
                raise MusicError('Favorites kosong. Tambahkan musik favorit di akun TikTok terlebih dahulu.')
            if not state['bottom']:
                self.read(FAVORITES_NEXT_JS)
            previous = signature
            time.sleep(1)
        raise MusicError('Daftar Favorites belum selesai dimuat/dipindai; submit tidak dilakukan.')


def apply_favorite_music(ws, mid, base, account, content_id, evaluate, click):
    ui = MusicUI(ws, mid, evaluate, click)
    ui.click(SOUNDS_JS, 'tombol Sounds tidak ditemukan atau ambigu')
    ui.click(FAVORITES_JS, 'tab Favorites tidak ditemukan')
    favorites = ui.favorites()
    choice = reserve_music(base, account, content_id, favorites)
    print(f"Musik Favorit tersimpan untuk video {content_id}: {choice['title']}", flush=True)
    existing = ui.read(AUDIO_TITLES_JS)
    if existing not in ([], [choice['title']]):
        raise MusicError('Timeline sudah berisi musik lain/lebih dari satu; tidak menambah atau menggantinya otomatis.')
    if not existing:
        ui.wait(FAVORITES_RESET_JS, lambda value: value is True, 'Favorites tidak bisa dibuka ulang')
        target = add_music_js(choice)
        for _ in range(120):
            point = ui.read(target)
            if isinstance(point, dict) and 'x' in point and 'y' in point:
                ui.mid = click(ws, point['x'], point['y'], ui.mid)
                break
            state = ui.read(FAVORITES_READ_JS)
            if isinstance(state, dict) and state['bottom'] and not state['busy']:
                raise MusicError('Lagu pilihan tidak tersedia untuk ditambahkan; submit tidak dilakukan.')
            ui.read(FAVORITES_NEXT_JS)
            time.sleep(1)
        else:
            raise MusicError('Lagu pilihan tidak ditemukan setelah scroll Favorites.')
    ui.wait(AUDIO_TITLES_JS, lambda value: value == [choice['title']], 'musik belum terpasang di timeline')
    # Volume dan fade tidak disentuh: gunakan bawaan TikTok.
    ui.click(SAVE_EDITOR_JS, 'Save editor musik belum siap')
    ui.wait(UPLOAD_READY_JS, lambda value: value is True, 'editor belum kembali ke upload', attempts=90)
    # Label preview upload bisa tetap "Original sound". Verifikasi melalui
    # timeline editor yang dibuka ulang, sesuai mapping browser nyata.
    ui.click(SOUNDS_JS, 'Sounds tidak bisa dibuka untuk verifikasi')
    ui.wait(AUDIO_TITLES_JS, lambda value: value == [choice['title']], 'musik tidak bertahan sesudah Save editor')
    ui.click(CANCEL_EDITOR_JS, 'Cancel editor verifikasi tidak ditemukan')
    ui.wait(UPLOAD_READY_JS, lambda value: value is True, 'editor verifikasi belum tertutup', attempts=90)
    return ui.mid
