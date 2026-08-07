"""HTML za /ui/pracenje — praćenje propisa (piece G): izvori, vlastite ključne
riječi ureda, nadolazeće promjene, ručna provjera, Excel izvoz. XSS-safe
(createElement/textContent), bez vanjskih resursa."""
from atlas.web.templates_ui import page_shell

_PRACENJE_JS = """
function $(id){ return document.getElementById(id); }

function td(content){ var d=document.createElement('td');
  if(content instanceof Node) d.appendChild(content); else d.textContent=content;
  return d; }

async function loadKeywords(){
  var res = await fetch('/watchlist/keywords', {credentials:'same-origin'});
  if(!res.ok) return;
  var words = await res.json();
  var box = $('kw-box'); box.textContent='';
  words.forEach(function(w, i){
    var chip = document.createElement('span'); chip.className='chip ok';
    chip.style.cssText='margin:.15rem;cursor:pointer';
    chip.textContent = w + ' \\u00d7';
    chip.title = 'Ukloni';
    chip.addEventListener('click', function(){
      words.splice(i, 1); saveKeywords(words);
    });
    box.appendChild(chip);
  });
  if(!words.length){ box.textContent = 'Nema ključnih riječi — dodaj pojmove koje ured prati (npr. naziv djelatnosti, porez, propis).'; }
  box.dataset.words = JSON.stringify(words);
}

async function saveKeywords(words){
  var res = await fetch('/watchlist/keywords', {method:'POST', credentials:'same-origin',
    headers:{'Content-Type':'application/json'}, body: JSON.stringify({keywords: words})});
  if(res.ok) loadKeywords();
}

$('kw-add').addEventListener('click', function(){
  var w = $('kw-input').value.trim();
  if(!w) return;
  var words = JSON.parse($('kw-box').dataset.words || '[]');
  words.push(w); $('kw-input').value='';
  saveKeywords(words);
});

async function loadSources(){
  var res = await fetch('/watchlist/sources', {credentials:'same-origin'});
  if(!res.ok) return;
  var body = $('src-body'); body.textContent='';
  (await res.json()).forEach(function(s){
    var tr = document.createElement('tr');
    tr.appendChild(td(s.url));
    tr.appendChild(td(s.category || '\\u2014'));
    tr.appendChild(td(s.kind));
    var tog = document.createElement('button'); tog.type='button';
    tog.className = 'btn btn-ghost';
    tog.textContent = s.active ? 'Isključi' : 'Uključi';
    tog.addEventListener('click', function(){
      fetch('/watchlist/sources/'+s.id+'/toggle', {method:'POST', credentials:'same-origin'})
        .then(loadSources);
    });
    var st = document.createElement('span');
    st.className = 'chip ' + (s.active ? 'ok' : '');
    st.textContent = s.active ? 'aktivan' : 'isklju\\u010den';
    var cell = document.createElement('span');
    cell.appendChild(st); cell.appendChild(document.createTextNode(' ')); cell.appendChild(tog);
    tr.appendChild(td(cell));
    body.appendChild(tr);
  });
}

$('src-add').addEventListener('click', async function(){
  var url = $('s-url').value.trim();
  if(!url) return;
  var res = await fetch('/watchlist/sources', {method:'POST', credentials:'same-origin',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({url: url, category: $('s-cat').value.trim(), kind: $('s-kind').value})});
  if(!res.ok){ var e = await res.json().catch(function(){return {};}); alert('Gre\\u0161ka: '+(e.detail||res.status)); return; }
  $('s-url').value=''; $('s-cat').value='';
  loadSources();
});

async function loadUpcoming(){
  var res = await fetch('/watchlist/upcoming', {credentials:'same-origin'});
  if(!res.ok) return;
  var rows = await res.json();
  var body = $('up-body'); body.textContent='';
  if(!rows.length){
    var tr = document.createElement('tr');
    tr.appendChild(td('Nema najavljenih promjena.')); tr.appendChild(td('')); tr.appendChild(td(''));
    body.appendChild(tr);
  }
  rows.forEach(function(u){
    var tr = document.createElement('tr');
    tr.appendChild(td(u.effective_date || '?'));
    tr.appendChild(td(u.description || ''));
    tr.appendChild(td(u.url || ''));
    body.appendChild(tr);
  });
}

$('run').addEventListener('click', async function(){
  var b = $('run'); b.disabled = true; b.textContent = 'Provjeravam\\u2026';
  var res = await fetch('/watchlist/run', {method:'POST', credentials:'same-origin'});
  b.disabled = false; b.textContent = 'Provjeri sada';
  if(res.ok){
    var changes = await res.json();
    $('run-result').textContent = changes.length
      ? changes.length + ' promjena \\u2014 detalji u Obavijestima.'
      : 'Nema promjena od zadnje provjere.';
    loadUpcoming();
  }
});

loadKeywords(); loadSources(); loadUpcoming();
"""


def pracenje_page() -> str:
    body = f"""<h1>Praćenje propisa</h1>
<p class="meta">ATLAS prati izvore (NN, porezna, stranice institucija), javlja promjene i
pogotke tvojih ključnih riječi, i vodi nadolazeće promjene s datumom stupanja na snagu.</p>

<div class="card">
  <h2>Ključne riječi ureda</h2>
  <p class="meta">Kad se pojam pojavi u promjeni propisa, stiže posebna obavijest.</p>
  <div id="kw-box" style="margin:.5rem 0"></div>
  <div style="display:flex;gap:.5rem">
    <input type="text" id="kw-input" maxlength="60" placeholder="npr. paušalni obrt">
    <button type="button" class="btn" id="kw-add">Dodaj</button>
  </div>
</div>

<div class="card">
  <h2>Nadolazeće promjene</h2>
  <div style="display:flex;gap:.5rem;align-items:center;margin-bottom:.5rem">
    <button type="button" class="btn" id="run">Provjeri sada</button>
    <a class="btn btn-ghost" href="/watchlist/export.xlsx" download>Izvoz u Excel</a>
    <span id="run-result" class="meta"></span>
  </div>
  <table class="ledger">
    <thead><tr><th>Stupa na snagu</th><th>Opis</th><th>Izvor</th></tr></thead>
    <tbody id="up-body"></tbody>
  </table>
</div>

<div class="card">
  <h2>Izvori</h2>
  <table class="ledger">
    <thead><tr><th>URL</th><th>Kategorija</th><th>Vrsta</th><th>Status</th></tr></thead>
    <tbody id="src-body"></tbody>
  </table>
  <form class="stack" onsubmit="return false" style="max-width:640px;margin-top:.75rem">
    <label for="s-url">URL izvora</label>
    <input type="text" id="s-url" placeholder="https://www.porezna-uprava.hr/...">
    <label for="s-cat">Kategorija (opcionalno)</label>
    <input type="text" id="s-cat" placeholder="PDV, plaće, paušal…">
    <label for="s-kind">Vrsta</label>
    <select id="s-kind"><option value="page">Stranica (diff)</option><option value="rss">RSS</option></select>
    <button type="button" class="btn" id="src-add">Dodaj izvor</button>
  </form>
</div>
<script>{_PRACENJE_JS}</script>"""
    return page_shell("Praćenje", body, active="pracenje")
