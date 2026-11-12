"""Kontrol Schedule yang dipetakan di Studio; gagal tertutup bila UI berbeda."""
import json
import time
from datetime import datetime, timedelta
from schedule_store import MIN_LEAD_MINUTES, PLATFORM_MIN_LEAD_MINUTES, now_wib


# Dua varian Studio: combobox baru dan input readonly pada widget lama.
SNAPSHOT_JS = r'''(()=>{
 const visible=e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0&&getComputedStyle(e).visibility!=='hidden'};
 const radios=Array.from(document.querySelectorAll('input[name="postSchedule"][value="schedule"]'));
 if(radios.length!==1)return null;
 const root=radios[0].closest('.scheduled-container');if(!root)return null;
 const dates=Array.from(root.querySelectorAll('[aria-label="Select date"]')).filter(visible);
 const times=Array.from(root.querySelectorAll('[aria-label="Select time"]')).filter(visible);
 const inputs=Array.from(root.querySelectorAll('.scheduled-picker input[readonly]')).filter(visible);
 const legacyDates=inputs.filter(e=>/^\d{4}-\d{2}-\d{2}$/.test(e.value));
 const legacyTimes=inputs.filter(e=>/^\d{2}:\d{2}$/.test(e.value));
 const modern=dates.length===1&&times.length===1&&!legacyDates.length&&!legacyTimes.length;
 const legacy=!dates.length&&!times.length&&legacyDates.length===1&&legacyTimes.length===1;
 if(!modern&&!legacy)return null;
 const zones=Array.from(root.querySelectorAll('input')).map(e=>e.value).filter(v=>/Local Time/.test(v));
 const browserZone=Intl.DateTimeFormat().resolvedOptions().timeZone;
 const timezone=zones.length===1?zones[0]:legacy&&zones.length===0&&browserZone==='Asia/Jakarta'?'(GMT+7) Local Time':null;
 const buttons=Array.from(document.querySelectorAll('button[data-e2e="post_video_button"]')).filter(visible);
 if(buttons.length!==1)return null;
 return {variant:modern?'modern':'legacy',selected:radios[0].checked,
 date:modern?dates[0].innerText.trim():legacyDates[0].value,
 time:modern?times[0].innerText.trim():legacyTimes[0].value,
 timezone,button:buttons[0].innerText.trim()};})()'''


def matches_target(snapshot, target):
    expected = target.strftime('%b ') + str(target.day) + target.strftime(', %Y')
    return bool(snapshot and snapshot.get('selected') is True
                and snapshot.get('date') in (expected, target.strftime('%Y-%m-%d'))
                and snapshot.get('time') == target.strftime('%H:%M')
                and snapshot.get('button') == 'Schedule'
                and snapshot.get('timezone') == '(GMT+7) Local Time')


LEGACY_DATE = "Array.from(document.querySelectorAll('.scheduled-container .scheduled-picker input[readonly]')).filter(e=>/^\\d{4}-\\d{2}-\\d{2}$/.test(e.value))"
LEGACY_TIME = "Array.from(document.querySelectorAll('.scheduled-container .scheduled-picker input[readonly]')).filter(e=>/^\\d{2}:\\d{2}$/.test(e.value))"


def configure_legacy(target, snapshot, read, press_nodes, press, wait_snapshot):
    """Widget readonly/kalender span; tidak menulis value atau melewati validasi UI."""
    if snapshot['timezone'] != '(GMT+7) Local Time':
        raise ValueError('Zona waktu widget Schedule lama bukan WIB.')
    if snapshot['date'] != target.strftime('%Y-%m-%d'):
        press_nodes(LEGACY_DATE, 'tanggal Schedule readonly')
        for _ in range(13):
            calendar = read("""(()=>{const roots=Array.from(document.querySelectorAll('.calendar-wrapper')).filter(e=>e.getBoundingClientRect().width>0);
                if(roots.length!==1)return null;const root=roots[0];
                const title=root.querySelector('.month-title')?.innerText.trim().toLowerCase();
                const year=Number(root.querySelector('.year-title')?.innerText.trim());
                const names=Array.from({length:12},(_,i)=>['en-US','id-ID'].map(locale=>new Intl.DateTimeFormat(locale,{month:'long'}).format(new Date(2026,i,1)).toLowerCase()));
                const month=names.findIndex(options=>options.includes(title))+1;
                const days=Array.from(root.querySelectorAll('.days-wrapper .day')).map(e=>({day:Number(e.innerText.trim()),valid:e.classList.contains('valid')}));
                return {month,year,days};})()""")
            if not calendar or not calendar['month'] or not calendar['year']:
                raise ValueError('Kalender Schedule lama tidak dikenali.')
            shown=(calendar['year'],calendar['month'])
            if shown == (target.year,target.month):
                days=calendar['days']
                first=next((i for i,d in enumerate(days) if d['day']==1),None)
                index=first+target.day-1 if first is not None else -1
                if index<0 or index>=len(days) or days[index]['day']!=target.day or not days[index]['valid']:
                    raise ValueError('Tanggal Schedule ditolak kalender TikTok.')
                press_nodes("Array.from(document.querySelectorAll('.calendar-wrapper .days-wrapper .day')).slice(%d,%d)" % (index,index+1), 'tanggal kalender Schedule')
                break
            direction=1 if shown<(target.year,target.month) else 0
            press_nodes("Array.from(document.querySelectorAll('.calendar-wrapper .month-header-wrapper .arrow')).slice(%d,%d)" % (direction,direction+1), 'navigasi bulan Schedule')
        else:
            raise ValueError('Tanggal Schedule di luar kalender yang dapat dipilih.')
        wait_snapshot(lambda s:s['date']==target.strftime('%Y-%m-%d'), 'Tanggal Schedule tidak berubah sesuai pilihan.')
    if snapshot['time'] != target.strftime('%H:%M'):
        press_nodes(LEGACY_TIME, 'jam Schedule readonly')
        def option(side, value):
            return "Array.from(document.querySelectorAll('.tiktok-timepicker-option-text.tiktok-timepicker-%s')).filter(e=>e.innerText.trim()===%s)" % (side,json.dumps('%02d' % value))
        press_nodes(option('left', target.hour), 'jam tayang Schedule')
        wait_snapshot(lambda s:s['time'].startswith(target.strftime('%H:')), 'Jam Schedule belum sesuai setelah animasi picker.')
        press_nodes(option('right', target.minute), 'menit tayang Schedule')
        wait_snapshot(lambda s:s['time']==target.strftime('%H:%M'), 'Menit Schedule belum sesuai setelah animasi picker.')
        press('.scheduled-container .scheduled-title-container')


def configure(ws, mid, target, evaluate, click):
    """Atur dan baca ulang radio/tanggal/jam. Tidak pernah klik tombol submit."""
    def read(expression):
        nonlocal mid
        result = evaluate(ws, expression, mid)
        mid += 1
        return result

    def press_nodes(expression, label):
        nonlocal mid
        position_js = """(()=>{const matches=(%s)
            .filter(e=>!e.disabled&&e.getAttribute('aria-disabled')!=='true'&&e.getBoundingClientRect().width>0&&e.getBoundingClientRect().height>0&&getComputedStyle(e).visibility!=='hidden');
            if(matches.length!==1)return null;const el=matches[0];
            el.scrollIntoView({block:'center',inline:'center',behavior:'instant'});
            const r=el.getBoundingClientRect(),x=r.x+r.width/2,y=r.y+r.height/2;
            return el.contains(document.elementFromPoint(x,y))?{x,y}:null;})()""" % expression
        position = None
        for _ in range(15):
            position = read(position_js)
            if position:
                break
            time.sleep(.15)
        if not position:
            raise ValueError('Kontrol Schedule tidak tersedia/tertutup: ' + label)
        mid = click(ws, position['x'], position['y'], mid)
        time.sleep(.3)

    def press(selector):
        press_nodes('Array.from(document.querySelectorAll(%s))' % json.dumps(selector), selector)

    def wait_snapshot(predicate, message):
        for _ in range(25):
            snapshot = read(SNAPSHOT_JS)
            if snapshot and predicate(snapshot):
                return snapshot
            time.sleep(.15)
        raise ValueError(message)

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
    snapshot = wait_snapshot(lambda s: s.get('selected') is True, 'Kontrol tanggal/jam Schedule belum siap atau ambigu.')
    if snapshot['variant'] == 'legacy':
        configure_legacy(target, snapshot, read, press_nodes, press, wait_snapshot)
        snapshot = read(SNAPSHOT_JS)
        if not matches_target(snapshot, target):
            raise ValueError('Tanggal/jam/zona waktu Schedule belum terverifikasi; submit dibatalkan.')
        if target < now_wib() + timedelta(minutes=PLATFORM_MIN_LEAD_MINUTES):
            raise ValueError('Slot Schedule terlalu dekat setelah pengaturan; submit dibatalkan.')
        return mid
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
    snapshot = read(SNAPSHOT_JS)
    if not matches_target(snapshot, target):
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
    """Verifikasi identik untuk kedua widget sebelum konfirmasi copyright."""
    dates=[target.strftime('%Y-%m-%d'),target.strftime('%b ')+str(target.day)+target.strftime(', %Y')]
    return "(()=>{const s=%s;return !!s&&s.selected===true&&%s.includes(s.date)&&s.time===%s&&s.button==='Schedule'&&s.timezone==='(GMT+7) Local Time';})()" % (SNAPSHOT_JS,json.dumps(dates),json.dumps(target.strftime('%H:%M')))
