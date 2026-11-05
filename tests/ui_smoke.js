// Uji template dan dialog dengan DOM stub, bukan pengganti QA visual browser.
function run(argv) {
  ObjC.import('Foundation');
  function read(path) { return $.NSString.stringWithContentsOfFileEncodingError(path, $.NSUTF8StringEncoding, null).js; }
  var root=argv[0], state=JSON.parse(read(argv[1])), source=read(root+'/web/app.js');
  var nodes={},listeners={};
  function node(selector) {
    if(!nodes[selector])nodes[selector]={innerHTML:'',textContent:'',open:false,disabled:false,className:'',classList:{add:function(){},remove:function(){}},close:function(){this.open=false;},showModal:function(){this.open=true;}};
    return nodes[selector];
  }
  var document={hidden:false,querySelector:function(selector){
    if(['#settings-form','#model-form','#keys-form','#browser-form','#diagnostics-form','#table-search'].indexOf(selector)>=0 && node('#main').innerHTML.indexOf('id="'+selector.slice(1)+'"')<0)return null;
    return node(selector);
  },querySelectorAll:function(){return [];},addEventListener:function(name,handler){listeners[name]=handler;}};
  var window={addEventListener:function(){}},location={hash:'#overview'};
  var fetch=function(path,options){if(options&&options.method==='POST')throw Error('Smoke test must not mutate data');return Promise.resolve({ok:true,json:function(){return Promise.resolve(state);}});};
  var code=source+'\nreturn {state:function(s){S=s;token="smoke";},readSettings:readSettings,render:render,sourceIds:sourceIds,newAccount:newAccount,newConcept:newConcept,editAccount:editAccount,accountRuntime:accountRuntime,newProduct:newProduct,editProduct:editProduct,upload:upload,schedule:schedule,start:start,testDraft:testDraft,renameConcept:renameConcept,assignConcept:assignConcept};';
  var app=new Function('document','window','location','fetch','setInterval','setTimeout','clearTimeout',code)(document,window,location,fetch,function(){},function(){},function(){});
  app.state(state);
  var pages=['overview','accounts','products','queue','schedules','history','diagnostics','settings'];
  pages.forEach(function(page){location.hash='#'+page;app.render();if(node('#main').innerHTML.indexOf('<h1>')<0)throw Error('Heading missing: '+page);if(node('#navigation').innerHTML.indexOf('aria-current="page"')<0)throw Error('Active nav missing: '+page);});
  if(node('#navigation').innerHTML.indexOf('#concepts')>=0)throw Error('Concept menu must be removed');
  var globalSettings=node('#main').innerHTML;
  if(globalSettings.indexOf('Orchestrator')>=0||globalSettings.indexOf('id="settings-form"')>=0||globalSettings.indexOf('name="max_per_day"')>=0)throw Error('Per-account settings must not appear on the global settings page');
  ['browser-form','model-form','keys-form'].forEach(function(id){if(globalSettings.indexOf('id="'+id+'"')<0)throw Error('Global form missing: '+id);});
  [function(){app.newAccount();},function(){app.newConcept();},function(){app.editAccount('akun1');},function(){app.accountRuntime('akun1');},function(){app.newProduct();},function(){app.editProduct(1);},function(){app.upload(1);},function(){app.schedule('akun1');},function(){app.start('akun1');},function(){app.renameConcept('video-jahit');},function(){app.assignConcept('video-jahit');}].forEach(function(open){open();if(!node('#modal').open)throw Error('Dialog did not open');if(!node('#modal-content').innerHTML)throw Error('Dialog body missing');node('#modal').close();});
  state.accounts[0].source_path='/fixture/content/test.id';state.accounts[0].product_ids=['11111','22222'];state.accounts[0].source_error=null;
  location.hash='#accounts';app.render();app.sourceIds('akun1');
  if(node('#modal-content').innerHTML.indexOf('11111')<0)throw Error('Flat source ID dialog missing');
  var cards=node('#main').innerHTML;
  if(cards.indexOf('data-action="open-folder" data-value="/fixture/content/test.id"')<0)throw Error('Account folder button must target the source folder');
  if(cards.indexOf('data-action="test-draft"')>=0)throw Error('Draft test must not clutter account cards');
  if(cards.indexOf('Automasi tidak aktif')<0)throw Error('Idle account status missing');
  ['queued','running','stopping'].forEach(function(status,index){state.accounts[0].running={status:status};location.hash='#accounts';app.render();if(node('#main').innerHTML.indexOf(['Menunggu proses','Automasi berjalan','Menghentikan'][index])<0)throw Error('Account automation status missing: '+status);});
  state.accounts[0].running=null;state.config.schedules={akun1:{enabled:true}};app.render();
  if(node('#main').innerHTML.indexOf('Menunggu jadwal')<0)throw Error('Scheduled account status missing');
  state.config.schedules={};location.hash='#diagnostics';app.render();
  if(node('#main').innerHTML.indexOf('id="diagnostics-form"')<0||node('#main').innerHTML.indexOf('salinan video')<0)throw Error('Draft test diagnostics page missing');
  if(cards.indexOf('daily-limit-form')>=0||cards.indexOf('ID-produk.txt')>=0||cards.indexOf('name="max_per_day"')>=0)throw Error('Account cards must stay compact');
  app.editAccount('akun1');
  var settings=node('#modal-content').innerHTML;
  var advanced=settings.slice(settings.indexOf('<details class="settings-advanced">'));
  if(advanced.indexOf('name="product_name"')<0||advanced.indexOf('name="step_delay"')<0)throw Error('Card product label must be in advanced settings');
  var emptyLabel={elements:{namedItem:function(key){return key==='product_name'?{value:''}:null;}}};
  if(app.readSettings(emptyLabel).product_name!=='')throw Error('Card product label must support clearing');
  ['max_per_day','min_interval_minutes','max_interval_minutes','product_link_name'].forEach(function(key){if(settings.indexOf('name="'+key+'"')<0)throw Error('Missing account setting: '+key);});
  var linkField=state.schema.filter(function(f){return f.key==='product_link_name';})[0];
  if(settings.indexOf('name="product_link_name" value="'+linkField.default+'" maxlength="'+linkField.maxLength+'"')<0)throw Error('Link label must read schema default and limit');
  if(settings.indexOf('name="min_interval_minutes" type="number"')<0&&settings.indexOf('name="min_interval_minutes"')<0)throw Error('Interval missing');
  if(settings.indexOf('min="60"')<0)throw Error('Minimum interval must be 60 minutes');
  if(settings.indexOf('name="confirmed"')>=0||settings.indexOf('<select name="mode"')>=0)throw Error('Mode should use direct choices without confirmation');
  if((settings.match(/type="radio" name="mode"/g)||[]).length!==2||settings.indexOf('value="manual_review" checked')<0)throw Error('Both mode choices and current selection must be visible');
  if(settings.indexOf('class="settings-fields"')<0||settings.indexOf('value="10:00"')<0||settings.indexOf('placeholder="10:00 atau 21:00"')<0)throw Error('Compact settings and clock format missing');
  if(settings.indexOf('name="window_start_minute"')>=0)throw Error('Minutes should be part of the clock input');
  if(settings.indexOf('name="music_enabled" value="false" checked')<0||settings.indexOf('name="music_enabled" value="true"')<0)throw Error('Music toggle must default off');
  var musicForm={elements:{namedItem:function(key){return key==='music_enabled'?{value:'true'}:null;}}};
  if(app.readSettings(musicForm).music_enabled!==true)throw Error('Music setting must be a boolean');
  var clockInput={elements:{namedItem:function(key){return key==='window_start_hour'?{value:'21:30'}:null;}}};
  var parsed=app.readSettings(clockInput);
  if(parsed.window_start_hour!==21||parsed.window_start_minute!==30)throw Error('Clock parsing must preserve minutes');
  clockInput.elements.namedItem=function(key){return key==='window_start_hour'?{value:'25:00'}:null;};
  var rejected=false;try{app.readSettings(clockInput);}catch(error){rejected=true;}if(!rejected)throw Error('Invalid clock accepted');
  node('#modal').close();
  state.accounts[0].running={status:'running'};
  app.editAccount('akun1');
  if(!node('#modal-submit').disabled||node('#modal-content').innerHTML.indexOf('setelah proses selesai')<0)throw Error('Running account must explain why settings are read-only');
  state.accounts[0].running=null;node('#modal').close();
  location.hash='#products';app.render();
  state.accounts[0].name='<img src=x onerror=alert(1)>';
  state.accounts[0].settings.product_name='<b>jingsu</b>';
  location.hash='#accounts';app.render();
  if(node('#main').innerHTML.indexOf('<img src=x')>=0)throw Error('User text not escaped');
  if(node('#main').innerHTML.indexOf('<b>jingsu</b>')>=0||node('#main').innerHTML.indexOf('&lt;b&gt;jingsu&lt;/b&gt;')<0)throw Error('Card product name must be escaped');
  location.hash='#overview';app.render();
  if(node('#main').innerHTML.indexOf('#concepts')>=0||node('#main').innerHTML.indexOf('Konsep konten')>=0)throw Error('Overview must not expose concept navigation');
  return '8 halaman dan 11 dialog dirender; escaping teks pengguna OK (DOM stub).';
}
