"""HTML za /ui/dok-tipovi — registar vrsta dokumenata s poljima za ekstrakciju.
Reuse design-system shell iz templates_ui; svi podaci iz API-ja preko
textContent/createElement (XSS-safe); bez vanjskih resursa."""
from atlas.web.templates_ui import page_shell

_DOCTYPES_JS = """
function $(id){ return document.getElementById(id); }
var KIND = {text:'Tekst', date:'Datum'};

function fieldRow(f){
  f = f || {key:'', label:'', kind:'text', expiry:false};
  var row = document.createElement('div');
  row.className = 'field-row';
  row.style.cssText = 'display:flex;gap:.4rem;align-items:center;margin:.25rem 0';
  function inp(ph, val, w){ var i=document.createElement('input'); i.type='text';
    i.placeholder=ph; i.value=val||''; i.style.width=w; return i; }
  var key = inp('kljuc (npr. broj)', f.key, '11rem');
  var label = inp('Naziv', f.label, '12rem');
  var kind = document.createElement('select');
  Object.keys(KIND).forEach(function(k){ var o=document.createElement('option');
    o.value=k; o.textContent=KIND[k]; kind.appendChild(o); });
  kind.value = f.kind || 'text';
  var expWrap = document.createElement('label');
  expWrap.style.cssText = 'display:inline-flex;gap:.25rem;align-items:center;white-space:nowrap';
  var exp = document.createElement('input'); exp.type='checkbox'; exp.checked=!!f.expiry;
  expWrap.appendChild(exp); expWrap.appendChild(document.createTextNode('istek'));
  var del = document.createElement('button'); del.type='button';
  del.className='btn btn-ghost'; del.textContent='\\u00D7';
  del.addEventListener('click', function(){ row.remove(); });
  row.appendChild(key); row.appendChild(label); row.appendChild(kind);
  row.appendChild(expWrap); row.appendChild(del);
  row._get = function(){ return {key:key.value.trim(), label:label.value.trim(),
    kind:kind.value, expiry:exp.checked}; };
  return row;
}

function readFields(){
  return Array.prototype.map.call(document.querySelectorAll('#fields .field-row'),
    function(r){ return r._get(); }).filter(function(f){ return f.key; });
}

function fillForm(t){
  $('d-key').value = t.key; $('d-key').readOnly = true;
  $('d-label').value = t.label || '';
  $('d-active').checked = !!t.active; $('d-sort').value = t.sort;
  var box = $('fields'); box.textContent='';
  (t.fields||[]).forEach(function(f){ box.appendChild(fieldRow(f)); });
  $('form-title').textContent = 'Uredi: ' + (t.label || t.key);
}
function resetForm(){
  $('d-key').value=''; $('d-key').readOnly=false; $('d-label').value='';
  $('d-active').checked=true; $('d-sort').value=100;
  $('fields').textContent=''; $('form-title').textContent='Nova vrsta';
}

function renderTypes(rows){
  var body = $('types-body'); body.textContent='';
  rows.forEach(function(t){
    var tr = document.createElement('tr'); tr.style.cursor='pointer';
    tr.addEventListener('click', function(){ fillForm(t); });
    function td(text){ var d=document.createElement('td'); d.textContent=text; return d; }
    tr.appendChild(td(t.key));
    tr.appendChild(td(t.label||''));
    tr.appendChild(td((t.fields||[]).map(function(f){
      return f.label + (f.expiry ? ' \\u23F3' : ''); }).join(', ') || '\\u2014'));
    var st=document.createElement('td'); var chip=document.createElement('span');
    chip.className='chip '+(t.active?'ok':''); chip.textContent=t.active?'aktivna':'skrivena';
    st.appendChild(chip); tr.appendChild(st);
    body.appendChild(tr);
  });
}

async function loadTypes(){
  var res = await fetch('/doc-types', {credentials:'same-origin'});
  if(res.ok) renderTypes(await res.json());
}

async function saveType(){
  var payload = {
    key: $('d-key').value.trim(), label: $('d-label').value.trim(),
    fields: readFields(), active: $('d-active').checked?1:0,
    sort: Number($('d-sort').value)||100,
  };
  if(!payload.key){ alert('\\u0160ifra je obavezna.'); return; }
  var res = await fetch('/doc-types', {method:'POST', credentials:'same-origin',
    headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)});
  if(!res.ok){ var e = await res.json().catch(function(){return {};}); alert('Gre\\u0161ka: '+(e.detail||res.status)); return; }
  resetForm(); loadTypes();
}

$('d-save').addEventListener('click', saveType);
$('d-reset').addEventListener('click', resetForm);
$('d-addfield').addEventListener('click', function(){ $('fields').appendChild(fieldRow()); });
loadTypes();
"""


def doctypes_page() -> str:
    body = f"""<h1>Vrste dokumenata</h1>
<p class="meta">Registar vrsta dokumenata (osobna iskaznica, putovnica, ugovor…) s poljima
koja se automatski čitaju iz skeniranog dokumenta. Polje označeno „istek" (⏳) daje
datum za podsjetnik prije isteka. <a href="/ui/postavke">← natrag na Postavke</a></p>

<p><a class="btn btn-ghost" href="/doc-types/export" download>Izvoz JSON</a></p>

<table class="ledger" style="margin:1rem 0">
  <thead><tr><th>Šifra</th><th>Naziv</th><th>Polja</th><th>Status</th></tr></thead>
  <tbody id="types-body"><tr><td colspan="4">Učitavanje…</td></tr></tbody>
</table>

<div class="card" style="max-width:720px">
  <h2 id="form-title">Nova vrsta</h2>
  <form class="stack" onsubmit="return false">
    <label for="d-key">Šifra (npr. putovnica) — mala slova, bez razmaka</label>
    <input type="text" id="d-key" maxlength="40" placeholder="putovnica">
    <label for="d-label">Naziv</label>
    <input type="text" id="d-label" placeholder="Putovnica">
    <label>Polja za ekstrakciju</label>
    <div id="fields"></div>
    <button type="button" class="btn btn-ghost" id="d-addfield">+ dodaj polje</button>
    <label><input type="checkbox" id="d-active" checked> aktivna</label>
    <input type="hidden" id="d-sort" value="100">
    <div style="display:flex;gap:.5rem">
      <button type="button" class="btn" id="d-save">Spremi</button>
      <button type="button" class="btn btn-ghost" id="d-reset">Nova / očisti</button>
    </div>
  </form>
</div>
<script>{_DOCTYPES_JS}</script>"""
    return page_shell("Vrste dokumenata", body, active="postavke")
