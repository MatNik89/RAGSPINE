"""HTML za /ui/kanali — konektori (e-pošta + kanali poruka): katalog dostupnih
tipova, dinamička forma po tipu, Test prije spremanja, popis konfiguriranih sa
statusom. XSS-safe (createElement/textContent)."""
from ragspine.web.templates_ui import page_shell

_JS = r"""
function $(id){ return document.getElementById(id); }
var TYPES = {};
var STCLR = {connected:'#16a34a', pending:'#d97706', error:'#dc2626', disabled:'#6b7280'};
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
  if(!res.ok){ m.style.color='#dc2626'; m.textContent = j.detail || 'Greška.'; return; }
  m.style.color = STCLR[j.status]||'#111'; m.textContent = (STTXT[j.status]||j.status) + ' — ' + (j.detail||'');
}

async function saveConn(){
  var name = $('cname').value.trim();
  if(!name){ $('form-msg').textContent='Upiši naziv.'; return; }
  var res = await fetch('/connectors', {method:'POST', credentials:'same-origin',
    headers:{'Content-Type':'application/json'}, body: JSON.stringify({kind:$('kind').value, name:name, config:collect()})});
  var j = await res.json().catch(function(){return {};});
  if(res.ok){ $('form-msg').textContent='✓ Spremljeno ('+(STTXT[j.status]||j.status)+').'; $('cname').value=''; loadList(); }
  else { $('form-msg').style.color='#dc2626'; $('form-msg').textContent = j.detail || 'Greška.'; }
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
    b.style.color=STCLR[r.status]||'#111'; b.style.fontWeight='600'; t3.appendChild(b); tr.appendChild(t3);
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
  loadTypes(); loadList();
});
"""

_BODY = """
<style>
  table{ width:100%; border-collapse:collapse; } td,th{ padding:6px 8px; text-align:left; }
  th{ color:var(--muted); font-weight:600; border-bottom:1px solid #e5e7eb; }
  input,select{ display:block; width:100%; max-width:420px; padding:8px; margin:4px 0 10px;
    border:1px solid #d1d5db; border-radius:8px; box-sizing:border-box; }
  label{ font-size:.9em; color:var(--muted); }
</style>
<h1>E-pošta i kanali</h1>
<p class="muted">Poveži uredsku e-poštu i kanale poruka (Telegram, WhatsApp). Nakon
  upisa podataka <b>Testiraj</b> pa <b>Spremi</b>. Telegram/WhatsApp traže još
  prijavu/QR — status ostaje „čeka autorizaciju" dok se ne dovrši.</p>

<div class="card">
  <h2>Dodaj konektor</h2>
  <label>Tip</label>
  <select id="kind"></select>
  <div id="fields"></div>
  <label>Naziv (za tebe)</label>
  <input id="cname" placeholder="npr. Uredski Gmail / Telegram recepcija">
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
<script>__SCRIPT__</script>
"""


def connectors_page() -> str:
    return page_shell("E-pošta i kanali", _BODY.replace("__SCRIPT__", _JS), active="postavke")
