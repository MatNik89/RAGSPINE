"""HTML za /ui/posta — konektori e-pošte (M365 Graph / on-prem Exchange):
katalog tipova, dinamička forma, Test prije spremanja, popis konfiguriranih sa
statusom. XSS-safe (createElement/textContent)."""
from atlas.web.templates_ui import page_shell

_JS = r"""
function $(id){ return document.getElementById(id); }
var TYPES = {};
var STCLR = {connected:'var(--ok)', pending:'var(--warn)', error:'var(--bad)', disabled:'var(--muted)'};
var STTXT = {connected:'spojeno', pending:'čeka autorizaciju', error:'greška', disabled:'isključeno'};

async function loadTypes(){
  var res = await fetch('/connector-types', {credentials:'same-origin'});
  var arr = res.ok ? await res.json() : [];
  var sel = $('kind'); sel.textContent='';
  arr.forEach(function(t){ TYPES[t.kind]=t;
    var o=document.createElement('option'); o.value=t.kind; o.textContent=t.label; sel.appendChild(o); });
  renderFields();
}

function renderFields(){
  var t = TYPES[$('kind').value]; var box=$('fields'); box.textContent='';
  if(!t) return;
  t.fields.forEach(function(f){
    var lab=document.createElement('label'); lab.textContent = f.label + (f.required?' *':'');
    var inp=document.createElement('input'); inp.id='f_'+f.key;
    inp.type = f.type==='password' ? 'password' : 'text';
    box.appendChild(lab); box.appendChild(inp);
  });
}

function collect(){
  var t = TYPES[$('kind').value]; var cfg={};
  t.fields.forEach(function(f){ cfg[f.key] = ($('f_'+f.key).value||'').trim(); });
  return cfg;
}

async function testConn(){
  var res = await fetch('/connectors/test', {method:'POST', credentials:'same-origin',
    headers:{'Content-Type':'application/json'}, body: JSON.stringify({kind:$('kind').value, config:collect()})});
  var j = await res.json().catch(function(){return {};});
  var m=$('form-msg');
  if(!res.ok){ m.style.color='var(--bad)'; m.textContent = j.detail || 'Greška.'; return; }
  m.style.color = STCLR[j.status]||'var(--text)'; m.textContent = (STTXT[j.status]||j.status) + ' — ' + (j.detail||'');
}

async function saveConn(){
  var name = $('cname').value.trim();
  if(!name){ $('form-msg').textContent='Upiši naziv.'; return; }
  var res = await fetch('/connectors', {method:'POST', credentials:'same-origin',
    headers:{'Content-Type':'application/json'}, body: JSON.stringify({kind:$('kind').value, name:name, config:collect()})});
  var j = await res.json().catch(function(){return {};});
  if(res.ok){ $('form-msg').textContent='✓ Spremljeno ('+(STTXT[j.status]||j.status)+').'; $('cname').value=''; loadList(); }
  else { $('form-msg').style.color='var(--bad)'; $('form-msg').textContent = j.detail || 'Greška.'; }
}

async function loadList(){
  var res = await fetch('/connectors', {credentials:'same-origin'});
  var rows = res.ok ? await res.json() : [];
  var body=$('list'); body.textContent='';
  if(!rows.length){ var tr=document.createElement('tr'); var td=document.createElement('td');
    td.colSpan=4; td.textContent='Još ništa nije konfigurirano.'; tr.appendChild(td); body.appendChild(tr); return; }
  rows.forEach(function(r){
    var tr=document.createElement('tr');
    var t1=document.createElement('td'); t1.textContent=r.name; tr.appendChild(t1);
    var t2=document.createElement('td'); t2.textContent=r.label; tr.appendChild(t2);
    var t3=document.createElement('td');
    var b=document.createElement('span'); b.textContent=(STTXT[r.status]||r.status)+(r.last_error?(' — '+r.last_error):'');
    b.style.color=STCLR[r.status]||'var(--text)'; b.style.fontWeight='600'; t3.appendChild(b); tr.appendChild(t3);
    var t4=document.createElement('td');
    var del=document.createElement('button'); del.className='btn btn-ghost'; del.textContent='Ukloni';
    del.addEventListener('click', function(){
      fetch('/connectors/'+r.id, {method:'DELETE', credentials:'same-origin'}).then(loadList); });
    t4.appendChild(del); tr.appendChild(t4);
    body.appendChild(tr);
  });
}

document.addEventListener('DOMContentLoaded', function(){
  $('kind').addEventListener('change', function(){ renderFields(); $('form-msg').textContent=''; });
  $('test').addEventListener('click', testConn);
  $('save').addEventListener('click', saveConn);
  $('pair').addEventListener('click', async function(){
    var res = await fetch('/telegram/pairing', {method:'POST', credentials:'same-origin'});
    var j = await res.json().catch(function(){return {};});
    $('pair-out').textContent = res.ok ? ('Pošalji botu:  ' + j.command) : (j.detail || 'Greška.');
  });
  loadTypes(); loadList();
});
"""

_BODY = """
<style>
  table{ width:100%; border-collapse:collapse; } td,th{ padding:6px 8px; text-align:left; }
  th{ color:var(--muted); font-weight:600; border-bottom:1px solid var(--border); }
  input,select{ display:block; width:100%; max-width:420px; padding:8px; margin:4px 0 10px;
    border:1px solid var(--border-2); border-radius:8px; box-sizing:border-box; }
  label{ font-size:.9em; color:var(--muted); }
</style>
<h1>E-pošta i Telegram</h1>
<p class="muted">Poveži uredsku e-poštu (Microsoft 365 ili Exchange server). Nakon
  upisa podataka <b>Testiraj</b> pa <b>Spremi</b>.</p>

<div class="card">
  <h2>Dodaj konektor</h2>
  <label>Tip</label>
  <select id="kind"></select>
  <div id="fields"></div>
  <label>Naziv (za tebe)</label>
  <input id="cname" placeholder="npr. Uredski mail / Info sandučić">
  <button class="btn" id="test">Testiraj</button>
  <button class="btn primary" id="save">Spremi</button>
  <p id="form-msg" class="muted"></p>
</div>

<div class="card">
  <h2>Konfigurirano</h2>
  <table>
    <thead><tr><th>Naziv</th><th>Tip</th><th>Status</th><th></th></tr></thead>
    <tbody id="list"></tbody>
  </table>
</div>
<div class="card">
  <h2>Telegram — uparivanje korisnika</h2>
  <p class="muted">Nakon što dodaš „Telegram" konektor gore (bot token), generiraj
    token uparivanja i pošalji ga botu porukom <code>/start &lt;token&gt;</code> —
    time se tvoj Telegram veže na ATLAS (neuparen nema pristup).</p>
  <button class="btn" id="pair">Generiraj token uparivanja</button>
  <p id="pair-out" class="muted"></p>
</div>
<script>__SCRIPT__</script>
"""


def connectors_page() -> str:
    return page_shell("E-pošta i Telegram", _BODY.replace("__SCRIPT__", _JS), active="postavke")
