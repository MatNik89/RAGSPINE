"""HTML za /ui/arhitektura — D2: naučena struktura KLIJENTI mape + DOGOVORENI
template (uredske mape + po klijentu) + preview/apply. Ništa nije hardkodirano:
dok se dogovor ne spremi (ovdje ili u chatu), ne predlaže se ništa.
XSS-safe: createElement/textContent; bez vanjskih resursa."""
from atlas.web.templates_ui import page_shell

_ARH_JS = """
function $(id){ return document.getElementById(id); }

function li(text, chipText, chipCls, indent){
  var el = document.createElement('li');
  if(indent) el.style.marginLeft = '1.25rem';
  el.appendChild(document.createTextNode(text + ' '));
  if(chipText){
    var chip = document.createElement('span');
    chip.className = 'chip ' + (chipCls||'');
    chip.textContent = chipText;
    el.appendChild(chip);
  }
  return el;
}

function renderLearned(d){
  var box = $('learned'); box.textContent='';
  if(!d.root){
    box.appendChild(li("Nema mape klijenata — registriraj je u Mre\\u017enim mapama s ulogom 'klijenti' (npr. KLIJENTI na NAS-u).", '', ''));
    return;
  }
  box.appendChild(li('Korijen: ' + d.root + ' \\u2014 ' + d.n_clients + ' klijenata', '', ''));
  var keys = Object.keys(d.subdir_counts);
  if(!keys.length){ box.appendChild(li('Klijenti nemaju podmapa.', '', '', true)); return; }
  keys.forEach(function(k){
    box.appendChild(li(k, 'kod ' + d.subdir_counts[k] + ' klijenata', 'ok', true));
  });
}

function renderPreview(p){
  var mh = $('must-have'); mh.textContent='';
  if(!p.template.office.length && !p.template.client_subdirs.length){
    mh.appendChild(li('Dogovor jo\\u0161 nije spremljen \\u2014 popuni ispod ili se dogovori u chatu.', '', ''));
  }
  p.must_have.forEach(function(e){
    mh.appendChild(li(e.name, e.exists ? '\\u2713 postoji' : '\\u2717 fali', e.exists ? 'ok' : 'bad'));
  });
  var cl = $('clients'); cl.textContent='';
  p.clients.forEach(function(c){
    cl.appendChild(li(c.name, '', ''));
    c.subdirs.forEach(function(s){
      cl.appendChild(li(s.name, s.exists ? '\\u2713' : '\\u2717 fali', s.exists ? 'ok' : 'bad', true));
    });
  });
  $('t-office').value = p.template.office.join(', ');
  $('t-client').value = p.template.client_subdirs.join(', ');
  var btn = $('apply');
  btn.style.display = p.n_missing > 0 ? 'inline-block' : 'none';
  if(p.n_missing > 0) btn.textContent = 'Kreiraj mape koje nedostaju (' + p.n_missing + ')';
  $('all-ok').style.display = (p.n_missing === 0 && (p.template.office.length || p.template.client_subdirs.length)) ? 'block' : 'none';
}

async function load(){
  var r1 = await fetch('/folder-architecture/learned', {credentials:'same-origin'});
  if(r1.ok) renderLearned(await r1.json());
  var r2 = await fetch('/folder-architecture', {credentials:'same-origin'});
  if(r2.ok) renderPreview(await r2.json());
}

function csv(v){ return v.split(',').map(function(s){ return s.trim(); }).filter(Boolean); }

$('t-save').addEventListener('click', async function(){
  var res = await fetch('/folder-architecture/template', {method:'POST', credentials:'same-origin',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({office: csv($('t-office').value), client_subdirs: csv($('t-client').value)})});
  if(!res.ok){ var e = await res.json().catch(function(){return {};}); alert('Gre\\u0161ka: '+(e.detail||res.status)); return; }
  load();
});

$('apply').addEventListener('click', async function(){
  var btn = $('apply'); btn.disabled = true; btn.textContent = 'Kreiram\\u2026';
  await fetch('/folder-architecture/apply', {method:'POST', credentials:'same-origin'});
  btn.disabled = false;
  load();
});
load();
"""


def arhitektura_page() -> str:
    body = f"""<h1>Arhitektura mapa</h1>
<p class="meta">ATLAS prvo <strong>pročita kako su mape već organizirane</strong>, a strukturu
onda <strong>dogovorite</strong> — ovdje ili u chatu ("dogovor mape po klijentu: Ugovori, Izvodi…").
Ništa se ne briše i ne premješta; na potvrdu se samo dodaju mape koje nedostaju.
<a href="/ui/postavke">← natrag na Postavke</a></p>

<div class="card">
  <h2>Snimljeno stanje (KLIJENTI mapa)</h2>
  <ul id="learned" style="list-style:none;padding:0"></ul>
</div>

<div class="card">
  <h2>Dogovor</h2>
  <form class="stack" onsubmit="return false">
    <label for="t-office">Uredske mape (zarezom odvojene, uz korijen)</label>
    <input type="text" id="t-office" placeholder="PROPISI, SCANNER, ARHIVA">
    <label for="t-client">Mape po klijentu (zarezom odvojene)</label>
    <input type="text" id="t-client" placeholder="Ugovori, Izvodi, Porezna">
    <button type="button" class="btn" id="t-save">Spremi dogovor</button>
  </form>
</div>

<div class="card">
  <h2>Pregled</h2>
  <ul id="must-have" style="list-style:none;padding:0"></ul>
  <h2>Po klijentu</h2>
  <ul id="clients" style="list-style:none;padding:0"></ul>
  <p id="all-ok" class="meta" style="display:none">Sve dogovorene mape postoje. ✓</p>
  <button type="button" class="btn" id="apply" style="display:none">Kreiraj mape koje nedostaju</button>
</div>
<script>{_ARH_JS}</script>"""
    return page_shell("Arhitektura mapa", body, active="postavke")
