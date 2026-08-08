"""HTML za /ui/uredjaji — skeneri i pisači: pronađi (mDNS), dodaj u registar,
skeniraj s ODABRANOG uređaja. XSS-safe (createElement/textContent), bez
vanjskih resursa."""
from atlas.web.templates_ui import page_shell

_DEV_JS = """
function $(id){ return document.getElementById(id); }
var KIND = {scanner: 'Skener', printer: 'Pisa\\u010d', 'radna-stanica': 'Radna stanica'};

function row(parent, cells){
  var tr = document.createElement('tr');
  cells.forEach(function(c){ var td=document.createElement('td');
    if(c instanceof Node) td.appendChild(c); else td.textContent=c;
    tr.appendChild(td); });
  parent.appendChild(tr);
  return tr;
}

function btn(label, cls, fn){
  var b = document.createElement('button');
  b.type='button'; b.className='btn '+(cls||''); b.textContent=label;
  b.addEventListener('click', fn);
  return b;
}

async function loadDevices(){
  var res = await fetch('/devices', {credentials:'same-origin'});
  if(!res.ok) return;
  var rows = (await res.json()).filter(function(d){ return d.kind !== 'radna-stanica'; });
  var body = $('dev-body'); body.textContent='';
  if(!rows.length){ row(body, ['Nema dodanih ure\\u0111aja \\u2014 klikni \\u201eProna\\u0111i ure\\u0111aje\\u201c.', '', '', '']); }
  rows.forEach(function(d){
    var act = document.createElement('span');
    if(d.kind === 'scanner'){
      act.appendChild(btn('Skeniraj', '', function(ev){
        var b = ev.target; b.disabled=true; b.textContent='Skeniram\\u2026';
        fetch('/devices/'+d.id+'/scan', {method:'POST', credentials:'same-origin'})
          .then(function(r){ return r.json().then(function(j){ return {ok:r.ok, j:j}; }); })
          .then(function(x){
            b.disabled=false; b.textContent='Skeniraj';
            $('scan-result').textContent = x.ok
              ? 'Sken gotov: ' + x.j.path + ' (' + x.j.pages + ' str.)'
              : 'Gre\\u0161ka: ' + (x.j.detail || 'sken nije uspio');
          });
      }));
    }
    act.appendChild(document.createTextNode(' '));
    act.appendChild(btn('Ukloni', 'btn-ghost', function(){
      fetch('/devices/'+d.id, {method:'DELETE', credentials:'same-origin'}).then(loadDevices);
    }));
    row(body, [KIND[d.kind]||d.kind, d.name, d.url, act]);
  });
}

function capsSummary(caps){
  if(!caps) return '\\u2013';
  var parts = [];
  if(caps.wol) parts.push('WOL');
  if(caps.shutdown_order !== null && caps.shutdown_order !== undefined) parts.push('ga\\u0161enje #' + caps.shutdown_order);
  if(caps.run_programs) parts.push('pokre\\u0107e programe');
  if(caps.monitor_only) parts.push('samo nadzor');
  return parts.length ? parts.join(', ') : '\\u2013';
}

async function loadWorkstations(){
  var res = await fetch('/devices?kind=radna-stanica', {credentials:'same-origin'});
  if(!res.ok) return;
  var rows = await res.json();
  var body = $('ws-body'); body.textContent='';
  if(!rows.length){ row(body, ['Nema dodanih radnih stanica.', '', '', '', '', '']); }
  rows.forEach(function(d){
    var act = btn('Ukloni', 'btn-ghost', function(){
      fetch('/devices/'+d.id, {method:'DELETE', credentials:'same-origin'}).then(loadWorkstations);
    });
    row(body, [d.name, d.host || '\\u2013', d.mac || '\\u2013',
      d.worker_username || '\\u2013', capsSummary(d.caps), act]);
  });
}

async function loadWorkerOptions(){
  var sel = $('ws-worker');
  var res = await fetch('/radnici', {credentials:'same-origin'});
  if(!res.ok) return;  // ne-admin: dropdown ostaje prazan, sekcija se ionako ne koristi
  var rows = await res.json();
  rows.forEach(function(w){
    var opt = document.createElement('option');
    opt.value = w.user; opt.textContent = w.user;
    sel.appendChild(opt);
  });
}

async function addWorkstation(){
  var caps = {
    wol: $('ws-cap-wol').checked,
    run_programs: $('ws-cap-run').checked,
    monitor_only: $('ws-cap-monitor').checked,
    shutdown_order: $('ws-cap-shutdown').checked && $('ws-shutdown-order').value !== ''
      ? parseInt($('ws-shutdown-order').value, 10) : null,
  };
  var res = await fetch('/devices', {method:'POST', credentials:'same-origin',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({kind: 'radna-stanica', name: $('ws-name').value.trim(),
      host: $('ws-host').value.trim(), mac: $('ws-mac').value.trim(),
      worker_username: $('ws-worker').value || null, caps: caps})});
  if(!res.ok){ var e = await res.json().catch(function(){return {};}); toast('Gre\\u0161ka: '+(e.detail||res.status), 'bad'); return; }
  $('ws-name').value=''; $('ws-host').value=''; $('ws-mac').value='';
  $('ws-cap-wol').checked=false; $('ws-cap-run').checked=false; $('ws-cap-monitor').checked=false;
  $('ws-cap-shutdown').checked=false; $('ws-shutdown-order').value='';
  loadWorkstations();
}

async function discover(){
  var b = $('discover'); b.disabled=true; b.textContent='Tra\\u017eim\\u2026';
  var res = await fetch('/devices/discover', {credentials:'same-origin'});
  b.disabled=false; b.textContent='Prona\\u0111i ure\\u0111aje';
  var body = $('found-body'); body.textContent='';
  if(!res.ok){ row(body, ['Gre\\u0161ka pri pretrazi.', '', '']); return; }
  var rows = await res.json();
  if(!rows.length){ row(body, ['Ni\\u0161ta prona\\u0111eno \\u2014 ure\\u0111aj mo\\u017ee\\u0161 dodati i ru\\u010dno ispod.', '', '']); return; }
  rows.forEach(function(d){
    row(body, [KIND[d.kind]||d.kind, d.name + ' \\u2014 ' + d.url,
      btn('Dodaj', '', function(){
        fetch('/devices', {method:'POST', credentials:'same-origin',
          headers:{'Content-Type':'application/json'},
          body: JSON.stringify({kind:d.kind, name:d.name, url:d.url})})
          .then(loadDevices);
      })]);
  });
}

async function addManual(){
  var res = await fetch('/devices', {method:'POST', credentials:'same-origin',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({kind: $('m-kind').value, name: $('m-name').value.trim(),
                          url: $('m-url').value.trim()})});
  if(!res.ok){ var e = await res.json().catch(function(){return {};}); toast('Gre\\u0161ka: '+(e.detail||res.status), 'bad'); return; }
  $('m-name').value=''; $('m-url').value='';
  loadDevices();
}

$('discover').addEventListener('click', discover);
$('m-add').addEventListener('click', addManual);
$('ws-add').addEventListener('click', addWorkstation);
loadDevices();
loadWorkstations();
loadWorkerOptions();
"""


def devices_page() -> str:
    body = f"""<h1>Uređaji</h1>
<p class="meta">Skeneri i pisači u mreži ureda. Pronađi ih automatski ili dodaj ručno;
kod skeniranja i printanja uvijek se bira <strong>s kojeg uređaja</strong>.
<a href="/ui/postavke">← natrag na Postavke</a></p>

<div class="card">
  <h2>Dodani uređaji</h2>
  <table class="ledger">
    <thead><tr><th>Vrsta</th><th>Naziv</th><th>Adresa</th><th></th></tr></thead>
    <tbody id="dev-body"></tbody>
  </table>
  <p id="scan-result" class="meta"></p>
</div>

<div class="card">
  <h2>Pronađi u mreži</h2>
  <button type="button" class="btn" id="discover">Pronađi uređaje</button>
  <table class="ledger" style="margin-top:.75rem">
    <tbody id="found-body"></tbody>
  </table>
</div>

<div class="card" style="max-width:640px">
  <h2>Dodaj ručno</h2>
  <form class="stack" onsubmit="return false">
    <label for="m-kind">Vrsta</label>
    <select id="m-kind"><option value="scanner">Skener</option><option value="printer">Pisač</option></select>
    <label for="m-name">Naziv</label>
    <input type="text" id="m-name" placeholder="Skener uz recepciju">
    <label for="m-url">Adresa (URL)</label>
    <input type="text" id="m-url" placeholder="http://192.168.1.50:8080/eSCL">
    <button type="button" class="btn" id="m-add">Dodaj</button>
  </form>
</div>

<div class="card">
  <h2>Radne stanice</h2>
  <p class="meta">Računala radnika: veže se uz radnika, host/MAC za buđenje na daljinu
  (WOL) i sposobnosti koje agent kasnije poštuje (ovdje se samo bilježe podaci).</p>
  <table class="ledger">
    <thead><tr><th>Naziv</th><th>Host</th><th>MAC</th><th>Radnik</th><th>Sposobnosti</th><th></th></tr></thead>
    <tbody id="ws-body"></tbody>
  </table>
</div>

<div class="card" style="max-width:640px">
  <h2>Dodaj radnu stanicu</h2>
  <form class="stack" onsubmit="return false">
    <label for="ws-name">Naziv</label>
    <input type="text" id="ws-name" placeholder="Računalo — recepcija">
    <label for="ws-host">Host (IP/ime, opcionalno)</label>
    <input type="text" id="ws-host" placeholder="192.168.1.20">
    <label for="ws-mac">MAC adresa (opcionalno, za WOL)</label>
    <input type="text" id="ws-mac" placeholder="AA:BB:CC:DD:EE:FF">
    <label for="ws-worker">Radnik</label>
    <select id="ws-worker"><option value="">— nije vezano —</option></select>
    <label><input type="checkbox" id="ws-cap-shutdown"> Gasi pri nestanku struje, redni broj:
      <input type="number" id="ws-shutdown-order" min="0" style="width:5rem"></label>
    <label><input type="checkbox" id="ws-cap-wol"> Probudi na LAN-u (WOL)</label>
    <label><input type="checkbox" id="ws-cap-run"> Smije pokretati programe</label>
    <label><input type="checkbox" id="ws-cap-monitor"> Samo nadzor</label>
    <button type="button" class="btn" id="ws-add">Dodaj</button>
  </form>
</div>
<script>{_DEV_JS}</script>"""
    return page_shell("Uređaji", body, active="postavke")
