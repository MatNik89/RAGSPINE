"""HTML za /ui/arhitektura — prijedlog strukture mapa (preview + confirm).
Reuse design-system shell; svi podaci iz API-ja preko createElement/textContent
(XSS-safe); bez vanjskih resursa."""
from ragspine.web.templates_ui import page_shell

_ARH_JS = """
function $(id){ return document.getElementById(id); }

function row(name, exists, indent){
  var li = document.createElement('li');
  if(indent) li.style.marginLeft = '1.25rem';
  var chip = document.createElement('span');
  chip.className = 'chip ' + (exists ? 'ok' : 'bad');
  chip.textContent = exists ? '\\u2713 postoji' : '\\u2717 fali';
  li.appendChild(document.createTextNode(name + ' '));
  li.appendChild(chip);
  return li;
}

function render(p){
  $('root-path').textContent = p.root;
  var mh = $('must-have'); mh.textContent='';
  p.must_have.forEach(function(e){ mh.appendChild(row(e.name, e.exists)); });
  var cl = $('clients'); cl.textContent='';
  if(!p.clients.length){
    var li = document.createElement('li'); li.className='meta';
    li.textContent = 'Nema klijenata s dodijeljenom mapom.'; cl.appendChild(li);
  }
  p.clients.forEach(function(c){
    cl.appendChild(row(c.name, c.folder_exists));
    c.subdirs.forEach(function(s){ cl.appendChild(row(s.name, s.exists, true)); });
  });
  var btn = $('apply');
  if(p.n_missing > 0){
    btn.style.display = 'inline-block';
    btn.textContent = 'Kreiraj mape koje nedostaju (' + p.n_missing + ')';
  } else {
    btn.style.display = 'none';
    $('all-ok').style.display = 'block';
  }
}

async function load(){
  var res = await fetch('/folder-architecture', {credentials:'same-origin'});
  if(res.ok) render(await res.json());
}

$('apply').addEventListener('click', async function(){
  var btn = $('apply'); btn.disabled = true; btn.textContent = 'Kreiram\\u2026';
  var res = await fetch('/folder-architecture/apply', {method:'POST', credentials:'same-origin'});
  btn.disabled = false;
  if(res.ok){ $('all-ok').style.display='none'; load(); }
});
load();
"""


def arhitektura_page() -> str:
    body = f"""<h1>Arhitektura mapa</h1>
<p class="meta">RAGSPINE predlaže urednu strukturu: obavezne mape ureda + standardne
podmape po klijentu. Ništa se ne briše i ne premješta — na potvrdu se samo
<strong>dodaju mape koje nedostaju</strong>.
<a href="/ui/postavke">← natrag na Postavke</a></p>

<div class="card">
  <h2>Korijen</h2>
  <p class="meta" id="root-path">Učitavanje…</p>
  <h2>Obavezne mape ureda</h2>
  <ul id="must-have" style="list-style:none;padding:0"></ul>
  <h2>Po klijentu</h2>
  <ul id="clients" style="list-style:none;padding:0"></ul>
  <p id="all-ok" class="meta" style="display:none">Sve mape postoje. ✓</p>
  <button type="button" class="btn" id="apply" style="display:none">Kreiraj mape koje nedostaju</button>
</div>
<script>{_ARH_JS}</script>"""
    return page_shell("Arhitektura mapa", body, active="postavke")
