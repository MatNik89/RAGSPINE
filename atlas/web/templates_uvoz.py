"""Ekran /ui/klijenti-uvoz — otkrivanje klijenata iz spojene KLIJENTI mape.
Odaberi mapu → učitaj kandidate → po retku uredi ime + akciju (uvezi/spoji/preskoči)
→ Uvezi. Sve API-driven, textContent (nikad innerHTML za API podatke)."""
from atlas.web.templates_ui import page_shell

_UVOZ_JS = """
function $(id){ return document.getElementById(id); }

async function loadFolders(){
  var res = await fetch('/folders', {credentials:'same-origin'});
  var sel = $('u-folder'); sel.textContent = '';
  if(!res.ok) return;
  (await res.json()).filter(function(f){ return f.role === 'klijenti'; }).forEach(function(f){
    var o = document.createElement('option'); o.value = f.id;
    o.textContent = (f.label || f.path); sel.appendChild(o);
  });
}

function actionSelect(match){
  var sel = document.createElement('select'); sel.className = 'u-action';
  [['import','Uvezi'], ['merge','Spoji s postojećim'], ['skip','Preskoči']].forEach(function(p){
    var o = document.createElement('option'); o.value = p[0]; o.textContent = p[1]; sel.appendChild(o);
  });
  sel.value = match ? 'merge' : 'import';
  return sel;
}

async function loadCandidates(){
  var fid = $('u-folder').value;
  if(!fid){ return; }
  var res = await fetch('/clients/discover?folder_id=' + encodeURIComponent(fid), {credentials:'same-origin'});
  var tb = $('u-list'); tb.textContent = '';
  if(!res.ok){ $('u-msg').textContent = 'Greška pri učitavanju.'; return; }
  var rows = await res.json();
  $('u-msg').textContent = rows.length + ' kandidata.';
  rows.forEach(function(r){
    var tr = document.createElement('tr'); tr.dataset.subdir = r.subdir; tr.dataset.match = r.match_id || '';
    var td0 = document.createElement('td'); td0.textContent = r.subdir;
    var td1 = document.createElement('td');
    var inp = document.createElement('input'); inp.type = 'text'; inp.className = 'u-name';
    inp.value = r.raw_name; td1.appendChild(inp);
    var td2 = document.createElement('td'); td2.textContent = r.guessed_type;
    var td3 = document.createElement('td'); td3.textContent = r.match_id ? 'postoji #' + r.match_id : '—';
    var td4 = document.createElement('td'); td4.appendChild(actionSelect(r.match_id));
    tr.appendChild(td0); tr.appendChild(td1); tr.appendChild(td2); tr.appendChild(td3); tr.appendChild(td4);
    tb.appendChild(tr);
  });
  $('u-commit').style.display = rows.length ? '' : 'none';
}

async function commit(){
  var fid = parseInt($('u-folder').value, 10);
  var items = Array.prototype.map.call(document.querySelectorAll('#u-list tr'), function(tr){
    var it = {subdir: tr.dataset.subdir,
              name: tr.querySelector('.u-name').value.trim(),
              action: tr.querySelector('.u-action').value};
    if(it.action === 'merge' && tr.dataset.match){ it.merge_id = parseInt(tr.dataset.match, 10); }
    return it;
  });
  var res = await fetch('/clients/discover/commit', {method:'POST', credentials:'same-origin',
    headers:{'Content-Type':'application/json'}, body: JSON.stringify({folder_id: fid, items: items})});
  var msg = $('u-msg');
  if(res.ok){ var d = await res.json();
    msg.textContent = 'Uvezeno: ' + d.created + ' novih, ' + d.merged + ' spojenih, ' + d.skipped + ' preskočeno.';
    loadCandidates(); }
  else { var e = await res.json().catch(function(){return{};}); msg.textContent = 'Greška: ' + (e.detail || res.status); }
}

$('u-load').addEventListener('click', loadCandidates);
$('u-commit').addEventListener('click', commit);
loadFolders();
"""


def uvoz_page() -> str:
    body = f"""<h1>Uvoz klijenata iz mape</h1>
<p class="meta">Odaberi spojenu KLIJENTI mapu, učitaj kandidate (podmape = klijenti),
uredi ime i akciju po retku, pa uvezi. Ništa se na disku ne mijenja.</p>
<div class="card" style="max-width:840px">
  <div style="display:flex;gap:.5rem;align-items:center">
    <select id="u-folder"></select>
    <button type="button" class="btn" id="u-load">Učitaj kandidate</button>
    <span id="u-msg" class="meta"></span>
  </div>
  <table style="margin-top:.75rem"><thead><tr>
    <th>Podmapa</th><th>Naziv (uredi)</th><th>Tip</th><th>Postoji?</th><th>Akcija</th>
  </tr></thead><tbody id="u-list"></tbody></table>
  <button type="button" class="btn" id="u-commit" style="display:none;margin-top:.75rem">Uvezi</button>
</div>
<script>{_UVOZ_JS}</script>"""
    return page_shell("Uvoz klijenata", body, active="klijenti")
