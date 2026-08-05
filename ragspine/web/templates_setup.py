"""First-run wizard /ui/setup — 3 koraka: provjera računala → kreiraj operatera →
gotovo. Samostalna stranica (nema nav-a jer korisnik još ne postoji). XSS-safe
(createElement/textContent). Model i NAS se podešavaju u Postavkama nakon prijave."""

_JS = r"""
function $(id){ return document.getElementById(id); }
function show(step){
  ['s1','s2','s3'].forEach(function(s){ $(s).style.display = (s===step)?'block':'none'; });
}

async function checkSystem(){
  var box = $('sys'); box.textContent = 'Provjeravam…';
  var res = await fetch('/preflight', {credentials:'same-origin'});
  if(!res.ok){ box.textContent = 'Ne mogu dohvatiti stanje računala.'; return; }
  var d = await res.json();
  box.textContent = '';
  d.requirements.forEach(function(r){
    var line = document.createElement('div');
    var mark = {ok:'✓', warn:'⚠', fail:'✕'}[r.status] || '';
    var sp = document.createElement('span'); sp.className='mark '+r.status; sp.textContent = mark + ' ';
    line.appendChild(sp);
    line.appendChild(document.createTextNode(r.naziv + ' — ' + r.detalj));
    if(r.status !== 'ok'){ var f=document.createElement('div'); f.className='fix'; f.textContent = '→ ' + r.fix; line.appendChild(f); }
    box.appendChild(line);
  });
  $('sys-note').textContent = d.requirements_ok
    ? 'Sve obavezno zadovoljeno.'
    : 'Nešto obavezno nedostaje — možeš nastaviti, ali riješi prije produkcije.';
  $('to2').disabled = false;
}

async function createOwner(ev){
  ev.preventDefault();
  var u = $('u').value.trim(), p = $('p').value;
  if(!u || !p){ $('owner-err').textContent = 'Upiši ime i lozinku.'; return; }
  var b = $('create'); b.disabled = true;
  var res = await fetch('/setup/owner', {method:'POST', credentials:'same-origin',
    headers:{'Content-Type':'application/json'}, body: JSON.stringify({username:u, password:p})});
  var j = await res.json().catch(function(){ return {}; });
  if(res.ok){ show('s3'); }
  else if(res.status === 409){ window.location.href = '/login'; }  // već postoji → prijava
  else { b.disabled = false; $('owner-err').textContent = j.detail || 'Greška pri kreiranju.'; }
}

document.addEventListener('DOMContentLoaded', function(){
  show('s1');
  checkSystem();
  $('to2').addEventListener('click', function(){ show('s2'); });
  $('back1').addEventListener('click', function(){ show('s1'); });
  $('owner-form').addEventListener('submit', createOwner);
});
"""

_PAGE = """
<style>
  body{ font-family: system-ui, sans-serif; max-width: 640px; margin: 40px auto; padding: 0 16px; }
  .card{ border:1px solid #e5e7eb; border-radius:12px; padding:20px; margin:16px 0; }
  h1{ font-size:1.5rem; } h2{ font-size:1.1rem; margin-top:0; }
  .mark.ok{ color:#16a34a; } .mark.warn{ color:#d97706; } .mark.fail{ color:#dc2626; }
  .fix{ color:#6b7280; font-size:.85em; margin:2px 0 8px 18px; }
  .btn{ padding:8px 16px; border-radius:8px; border:1px solid #d1d5db; background:#f9fafb; cursor:pointer; }
  .btn.primary{ background:#2563eb; color:#fff; border-color:#2563eb; }
  input{ display:block; width:100%; padding:8px; margin:6px 0 12px; border:1px solid #d1d5db; border-radius:8px; box-sizing:border-box; }
  .step{ color:#6b7280; font-size:.85em; }
  .err{ color:#dc2626; }
</style>
<h1>Dobrodošli u RAGSPINE</h1>
<p class="step">Prvo postavljanje — 3 koraka</p>

<div class="card" id="s1">
  <h2>1 / 3 · Provjera računala</h2>
  <div id="sys"></div>
  <p id="sys-note" class="step"></p>
  <button class="btn primary" id="to2" disabled>Dalje →</button>
</div>

<div class="card" id="s2" style="display:none">
  <h2>2 / 3 · Operater (glavni korisnik)</h2>
  <p class="step">Prvi korisnik postaje vlasnik (owner) ureda.</p>
  <form id="owner-form">
    <label>Korisničko ime</label>
    <input id="u" autocomplete="username" autofocus>
    <label>Lozinka</label>
    <input id="p" type="password" autocomplete="new-password">
    <p id="owner-err" class="err"></p>
    <button type="button" class="btn" id="back1">← Natrag</button>
    <button type="submit" class="btn primary" id="create">Kreiraj operatera →</button>
  </form>
</div>

<div class="card" id="s3" style="display:none">
  <h2>3 / 3 · Spremno ✓</h2>
  <p>Operater je kreiran. Prijavi se pa u <b>Postavke → Računalo i modeli</b>
     odaberi lokalni model, a u <b>Mrežne mape</b> registriraj KLIJENTI mapu.</p>
  <a class="btn primary" href="/login">Idi na prijavu →</a>
</div>
<script>__SCRIPT__</script>
"""


def setup_page() -> str:
    return "<!doctype html><meta charset='utf-8'><title>RAGSPINE — postavljanje</title>" \
        + _PAGE.replace("__SCRIPT__", _JS)
