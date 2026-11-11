"""Kontrol Schedule yang dipetakan di Studio; gagal tertutup bila UI berbeda."""
import json
import time
from datetime import datetime, timedelta
from schedule_store import MIN_LEAD_MINUTES, PLATFORM_MIN_LEAD_MINUTES, now_wib


def configure(ws, mid, target, evaluate, click):
    """Atur dan baca ulang radio/tanggal/jam. Tidak pernah klik tombol submit."""
    def read(expression):
        nonlocal mid
        result = evaluate(ws, expression, mid)
        mid += 1
        return result

    def press(selector):
        nonlocal mid
        position = read("""(()=>{const matches=Array.from(document.querySelectorAll(%s))
            .filter(e=>!e.disabled&&e.getAttribute('aria-disabled')!=='true'&&e.getBoundingClientRect().width>0);
            if(matches.length!==1)return null;const el=matches[0];
            el.scrollIntoView({block:'center',inline:'center',behavior:'instant'});
            const r=el.getBoundingClientRect(),x=r.x+r.width/2,y=r.y+r.height/2;
            return el.contains(document.elementFromPoint(x,y))?{x,y}:null;})()""" % json.dumps(selector))
        if not position:
            raise ValueError('Kontrol Schedule tidak tersedia/tertutup: ' + selector)
        mid = click(ws, position['x'], position['y'], mid)
        time.sleep(.3)

    if target < now_wib() + timedelta(minutes=MIN_LEAD_MINUTES - 1):
        raise ValueError('Slot Schedule terlalu dekat; siapkan ulang sebelum submit.')
    timezone = read("Array.from(document.querySelectorAll('input')).map(e=>e.value).find(v=>/Local Time/.test(v))||Intl.DateTimeFormat().resolvedOptions().timeZone")
    if timezone not in ('Asia/Jakarta', '(GMT+7) Local Time'):
        raise ValueError('Zona waktu Studio bukan WIB; periksa pengaturan sebelum Schedule.')
    if read("document.querySelector('input[name=postSchedule][value=schedule]')?.checked") is not True:
        press('label:has(input[name="postSchedule"][value="schedule"])')
    consent = read("Array.from(document.querySelectorAll('[role=dialog]')).some(e=>e.innerText.includes('Allow your video to be saved for scheduled posting?'))")
    if consent:
        # Persetujuan eksplisit melalui aktivasi automasi Schedule di panel/CLI.
        allowed = read("""(()=>{const d=Array.from(document.querySelectorAll('[role=dialog]')).find(e=>e.innerText.includes('Allow your video to be saved for scheduled posting?'));
            const b=d&&Array.from(d.querySelectorAll('button')).find(e=>e.innerText.trim()==='Allow');
            if(!b||b.disabled)return null;const r=b.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2};})()""")
        if not allowed:
            raise ValueError('Konfirmasi Allow untuk Schedule tidak ditemukan.')
        mid = click(ws, allowed['x'], allowed['y'], mid)
        time.sleep(.3)
    press('[role="combobox"][aria-label="Select date"]')
    # Gunakan format label tanggal yang dipetakan di browser akun.
    target_iso = target.isoformat()
    date_label = read("new Intl.DateTimeFormat('id-ID',{weekday:'long',day:'numeric',month:'long',year:'numeric',timeZone:'Asia/Jakarta'}).format(new Date(%s))" % json.dumps(target_iso))
    date_selector = 'button.DatePicker__day[aria-label=' + json.dumps(date_label) + ']'
    for _ in range(13):
        state = read("""(()=>{const e=document.querySelector(%s);return e?{found:true,disabled:e.disabled}:null;})()""" % json.dumps(date_selector))
        if state:
            if state['disabled']:
                raise ValueError('Tanggal Schedule ditolak kalender TikTok: ' + date_label)
            break
        month = read("document.querySelector('.DatePicker__titleMonth')?.innerText")
        year = read("document.querySelector('.DatePicker__titleYear')?.innerText")
        names = ('January','February','March','April','May','June','July','August','September','October','November','December')
        if month not in names or not year or not str(year).isdigit():
            raise ValueError('Bulan kalender Schedule tidak dikenali.')
        direction = 'Next month' if (int(year), names.index(month)+1) < (target.year,target.month) else 'Previous month'
        press('button[aria-label="' + direction + '"]')
    else:
        raise ValueError('Tanggal Schedule di luar kalender yang dapat dipilih.')
    press(date_selector)
    press('[role="combobox"][aria-label="Select time"]')
    press('button[role="option"][aria-label="Hours %02d"]' % target.hour)
    press('button[role="option"][aria-label="Minutes %02d"]' % target.minute)
    expanded = read("document.querySelector('[aria-label=\"Select time\"]')?.getAttribute('aria-expanded')")
    if expanded == 'true':
        press('[role="combobox"][aria-label="Select time"]')
    snapshot = read("""(()=>{const get=s=>document.querySelector(s);return {
        selected:get('input[name=postSchedule][value=schedule]')?.checked,
        date:get('[aria-label="Select date"]')?.innerText,
        time:get('[aria-label="Select time"]')?.innerText,
        button:get('button[data-e2e="post_video_button"]')?.innerText,
        timezone:Array.from(document.querySelectorAll('input')).map(e=>e.value).find(v=>/Local Time/.test(v))};})()""")
    expected_date = target.strftime('%b ') + str(target.day) + target.strftime(', %Y')
    if not snapshot or snapshot.get('selected') is not True or snapshot.get('date') != expected_date or snapshot.get('time') != target.strftime('%H:%M') or snapshot.get('button','').strip() != 'Schedule' or snapshot.get('timezone') != '(GMT+7) Local Time':
        raise ValueError('Tanggal/jam/zona waktu Schedule belum terverifikasi; submit dibatalkan.')
    if target < now_wib() + timedelta(minutes=PLATFORM_MIN_LEAD_MINUTES):
        raise ValueError('Slot Schedule terlalu dekat setelah pengaturan; submit dibatalkan.')
    return mid


def result_expression(caption, target):
    """Bukti dari baris video yang sama, bukan sekadar redirect halaman."""
    return """(()=>{const normalize=s=>(s||'').replace(/\\s+/g,' ').trim().toLowerCase();
      const caption=normalize(%s),target=new Date(%s),opts={timeZone:'Asia/Jakarta'};
      const dates=[%s,...['en-US','id-ID'].map(locale=>new Intl.DateTimeFormat(locale,{...opts,month:'short',day:'numeric'}).format(target))].map(normalize);
      const times=['en-US','en-GB'].map(locale=>new Intl.DateTimeFormat(locale,{...opts,hour:'numeric',minute:'2-digit'}).format(target)).map(normalize);
      const rows=Array.from(document.querySelectorAll('[data-tt="components_PostInfoCell_FlexColumn"]'));
      const matches=rows.filter(e=>{const title=normalize(e.querySelector('[data-tt="components_PostInfoCell_a"]')?.innerText);
        const stageElement=e.querySelector('[data-tt="components_PublishStageLabel_FlexCenter"]');
        const stage=normalize(stageElement?.innerText);
        const alarm=!!stageElement?.querySelector?.('[data-icon="Alarm"],[data-testid="Alarm"]');
        const scheduled=/scheduled|terjadwal|dijadwalkan/i.test(stage)||(alarm&&stage.includes('(gmt+7)'));
        return title===caption&&scheduled&&times.some(t=>stage.includes(t))&&dates.some(d=>stage.includes(d));});
      return matches.length===1;
    })()""" % (json.dumps(caption), json.dumps(target.isoformat()), json.dumps(target.strftime('%Y-%m-%d')))


def confirmation_guard(target):
    """Dialog copyright boleh dilanjutkan hanya jika pilihan Schedule tetap utuh."""
    expected_date = target.strftime('%b ') + str(target.day) + target.strftime(', %Y')
    return """(()=>{const get=s=>document.querySelector(s);return (
      get('input[name=postSchedule][value=schedule]')?.checked===true&&
      get('[aria-label="Select date"]')?.innerText===%s&&
      get('[aria-label="Select time"]')?.innerText===%s&&
      get('button[data-e2e="post_video_button"]')?.innerText.trim()==='Schedule'&&
      Array.from(document.querySelectorAll('input')).some(e=>e.value==='(GMT+7) Local Time'));})()""" % (json.dumps(expected_date),json.dumps(target.strftime('%H:%M')))
