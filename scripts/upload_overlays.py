"""Penutup tour yang dikenali; dialog produk, submit, dan peringatan tidak disentuh."""
TOUR_CLOSE_JS = r'''(() => {
 const visible=e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0&&getComputedStyle(e).visibility!=='hidden'};
 const marker=/tour|onboarding|coachmark|introjs|driver-popover/i;
 const roots=Array.from(document.querySelectorAll('[class],[data-e2e],[data-testid]')).filter(e=>visible(e)&&marker.test([typeof e.className==='string'?e.className:'',e.getAttribute('data-e2e')||'',e.getAttribute('data-testid')||''].join(' ')));
 const normalize=text=>text.replace(/\s+/g,' ').replace(/’/g,"'").trim().toLowerCase();
 // Tour dari screenshot operator: kenali isi lengkap tanpa bergantung pada
 // nama class atau label tombol yang berubah-ubah.
 const known=[
   ['new editing features added',"now it's easier than ever before to create professional and engaging videos"],
   ['preview your video on your phone','now you can view your video as it will appear on tiktok'],
 ];
 for(const heading of Array.from(document.querySelectorAll('h1,h2,h3,h4,[role="heading"],div,span,p,strong')).filter(e=>visible(e)&&known.some(([title])=>normalize(e.innerText||'')===title))){
   let root=heading.parentElement;
   for(let depth=0;root&&root!==document.body&&root!==document.documentElement&&depth<8;depth++,root=root.parentElement){
     const text=normalize(root.innerText||'');
     if(text.length>1800)break;
     if(known.some(([title,description])=>text.includes(title)&&text.includes(description))){
       if(!roots.includes(root))roots.push(root);
       break;
     }
   }
 }
 const allowed=new Set(['got it','skip','skip tour','finish','done','close tour','mengerti','lewati','selesai']);
 const results=[],safeRoots=[];
 for(const root of roots){
   const text=(root.innerText||'').trim();
   if(text.length<5||text.length>1800)continue;
   if(root.querySelector('input,textarea,select,[contenteditable="true"],[data-e2e="post_video_button"],[data-e2e="save_draft_button"]'))continue;
   // Jangan melewati consent, pemeriksaan keamanan, maupun keputusan konten.
   if(/copyright|violation|community guidelines|consent|agree|privacy|verification|captcha|still checking|continue to post/i.test(text))continue;
   safeRoots.push(root);
   const buttons=Array.from(root.querySelectorAll('button,[role="button"]')).filter(visible);
   for(const button of buttons){
     const label=(button.innerText||button.getAttribute('aria-label')||'').trim().toLowerCase();
     if(!allowed.has(label)||button.disabled||button.getAttribute('aria-disabled')==='true')continue;
     const r=button.getBoundingClientRect(),x=r.x+r.width/2,y=r.y+r.height/2;
     if(!button.contains(document.elementFromPoint(x,y)))continue;
     if(!results.some(item=>item.button===button))results.push({button,x,y,label,text:text.slice(0,250)});
   }
 }
 // Utamakan penutup yang jelas. Jika label berbeda, klik hanya backdrop yang
 // benar-benar menerima pointer, bukan tombol/form di bawahnya.
 if(results.length===1){const {x,y,label,text}=results[0];return {x,y,label,text};}
 if(results.length>1)return null;
 if(!ALLOW_BACKDROP)return null;
 const tours=safeRoots.filter(root=>!safeRoots.some(other=>other!==root&&root.contains(other)));
 if(tours.length!==1)return null;
 const root=tours[0];
 for(const dialog of Array.from(document.querySelectorAll('[role="dialog"],[role="alertdialog"],dialog')).filter(visible)){
   if(dialog.contains(root)||root.contains(dialog))continue;
   const r=dialog.getBoundingClientRect(),hit=document.elementFromPoint(Math.min(innerWidth-1,Math.max(0,r.x+r.width/2)),Math.min(innerHeight-1,Math.max(0,r.y+r.height/2)));
   if(hit&&dialog.contains(hit))return null;
 }
 const points=[[12,12],[innerWidth-12,12],[12,innerHeight-12],[innerWidth-12,innerHeight-12],[innerWidth*.1,innerHeight*.5],[innerWidth*.9,innerHeight*.5]];
 for(const [x,y] of points){
   const hit=document.elementFromPoint(x,y);
   if(!hit||hit===document.body||hit===document.documentElement||root.contains(hit))continue;
   if(hit.closest('button,a,input,textarea,select,[role="button"],[role="combobox"],[contenteditable="true"]'))continue;
   const identity=[typeof hit.className==='string'?hit.className:'',hit.getAttribute('data-e2e')||'',hit.getAttribute('data-testid')||''].join(' ');
   if(!/mask|backdrop|overlay/i.test(identity))continue;
   const r=hit.getBoundingClientRect(),style=getComputedStyle(hit);
   if(!['fixed','absolute'].includes(style.position)||r.width<innerWidth*.5||r.height<innerHeight*.5)continue;
   return {x,y,label:'outside tour',text:(root.innerText||'').trim().slice(0,250)};
 }
 return null;
})()'''
TOUR_BUTTON_CLOSE_JS = TOUR_CLOSE_JS.replace('ALLOW_BACKDROP', 'false')
TOUR_CLOSE_JS = TOUR_CLOSE_JS.replace('ALLOW_BACKDROP', 'true')

UPLOAD_OBSTRUCTION_JS = r'''(() => {
 const visible=e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0&&getComputedStyle(e).visibility!=='hidden'};
 const popups=Array.from(document.querySelectorAll('[role="dialog"],[role="alert"],[role="tooltip"],dialog,[class*="tour"],[class*="Tour"],[class*="onboarding"],[class*="coachmark"]')).filter(visible);
 const details=popups.map(e=>({role:e.getAttribute('role'),class:typeof e.className==='string'?e.className:'',text:(e.innerText||'').trim().slice(0,800),buttons:Array.from(e.querySelectorAll('button,[role="button"]')).filter(visible).map(b=>({label:(b.innerText||b.getAttribute('aria-label')||'').trim().slice(0,80),disabled:!!b.disabled}))}));
 const button=Array.from(document.querySelectorAll('[data-e2e="anchor_container"] button')).find(e=>(e.innerText||'').trim()==='Add'&&visible(e));
 let blocker=null;
 if(button){const r=button.getBoundingClientRect(),x=r.x+r.width/2,y=r.y+r.height/2;const hit=document.elementFromPoint(x,y);
 if(hit&&!button.contains(hit)){const root=hit.closest('[role="dialog"],[role="tooltip"],[class*="tour"],[class*="Tour"],[class*="popover"],[class*="Popover"]')||hit;
 blocker={tag:hit.tagName,role:root.getAttribute('role'),class:typeof root.className==='string'?root.className:'',text:(root.innerText||'').trim().slice(0,800)};}}
 return {popups:details.slice(-8),addLinkBlocker:blocker};
})()'''


def dismiss_known_tours(ws, mid, evaluate, click, sleep, *, allow_backdrop=True):
    expression = TOUR_CLOSE_JS if allow_backdrop else TOUR_BUTTON_CLOSE_JS
    previous=None
    for _ in range(3):
        target=evaluate(ws,expression,mid);mid+=1
        if not isinstance(target,dict) or not all(key in target for key in ('x','y','label','text')):
            return mid
        signature=(target['label'],target['text'])
        if signature==previous:
            raise RuntimeError('Tour onboarding masih menutupi halaman setelah ditutup; proses dihentikan untuk pemeriksaan.')
        print(f"  Tour onboarding: {target['label']} — {target['text'][:160]}",flush=True)
        mid=click(ws,target['x'],target['y'],mid)
        previous=signature
        sleep(.5)
    target=evaluate(ws,expression,mid);mid+=1
    if isinstance(target,dict) and all(key in target for key in ('x','y','label','text')):
        raise RuntimeError('Tour onboarding belum selesai setelah tiga langkah; proses dihentikan untuk pemeriksaan.')
    return mid
