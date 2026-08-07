"""HTML za /ui/novi-klijent — DODAJ NOVOG KLIJENTA wizard (piece F) s AI
sidebarom: dok se tipka, sidebar provjerava podatke (pravila + quickref),
vadi citate iz propisa i nabraja obrasce institucija. XSS-safe
(createElement/textContent), bez vanjskih resursa."""
from atlas.web.templates_ui import page_shell

_WIZ_JS = """
function $(id){ return document.getElementById(id); }

var STEPS = ['s1','s2','s3'];
var cur = 0;

function showStep(i){
  cur = i;
  STEPS.forEach(function(s, j){ $(s).style.display = j === i ? 'block' : 'none'; });
  $('back').style.display = i > 0 ? 'inline-block' : 'none';
  $('next').textContent = i === STEPS.length - 1 ? 'Kreiraj klijenta' : 'Dalje \\u2192';
  $('step-label').textContent = 'Korak ' + (i+1) + ' / ' + STEPS.length;
}

function draft(){
  return {
    name: $('w-name').value.trim(), oib: $('w-oib').value.trim(),
    legal_form: $('w-legal').value, regime: $('w-regime').value,
    pdv_status: $('w-pdv').value, has_employees: $('w-emp').checked ? 1 : 0,
    pausal_eur: Number($('w-pausal').value) || 0,
  };
}

function syncRegime(){
  // poduzeće = dobit; obrt bira dohodak/paušal/dobit
  var legal = $('w-legal').value, reg = $('w-regime');
  if(legal === 'poduzece'){ reg.value = 'dobit'; reg.disabled = true; }
  else { reg.disabled = false; }
}

function ul(id, items, cls){
  var box = $(id); box.textContent = '';
  items.forEach(function(t){
    var li = document.createElement('li');
    if(cls){ var chip = document.createElement('span'); chip.className = 'chip ' + cls;
      chip.textContent = t; li.appendChild(chip); }
    else li.textContent = t;
    box.appendChild(li);
  });
  box.parentElement.style.display = items.length ? 'block' : 'none';
}

var assistTimer = null;
var assistSeq = 0;
function scheduleAssist(){
  if(assistTimer) clearTimeout(assistTimer);
  assistTimer = setTimeout(runAssist, 600);
}

async function runAssist(){
  var seq = ++assistSeq;  // spori stari odgovor ne smije pregaziti noviji
  var res = await fetch('/clients/assist', {method:'POST', credentials:'same-origin',
    headers:{'Content-Type':'application/json'}, body: JSON.stringify(draft())});
  if(!res.ok || seq !== assistSeq) return;
  var a = await res.json();
  ul('as-warn', a.warnings, 'bad');
  ul('as-sugg', a.suggestions);
  ul('as-forms', a.forms);
  var src = $('as-src'); src.textContent = '';
  a.sources.forEach(function(s){
    var li = document.createElement('li');
    var b = document.createElement('strong'); b.textContent = s.title; li.appendChild(b);
    li.appendChild(document.createTextNode(' \\u2014 ' + s.snippet));
    src.appendChild(li);
  });
  src.parentElement.style.display = a.sources.length ? 'block' : 'none';
  $('as-note').textContent = a.llm_note || '';
  $('as-note').style.display = a.llm_note ? 'block' : 'none';
}

async function loadDocTypes(){
  var res = await fetch('/doc-types', {credentials:'same-origin'});
  if(!res.ok) return;
  var box = $('w-doctypes'); box.textContent = '';
  (await res.json()).filter(function(t){ return t.active; }).forEach(function(t){
    var label = document.createElement('label');
    label.style.cssText = 'display:flex;gap:.4rem;align-items:center';
    var cb = document.createElement('input'); cb.type = 'checkbox'; cb.value = t.key;
    label.appendChild(cb);
    label.appendChild(document.createTextNode(t.label));
    box.appendChild(label);
  });
}

async function create(){
  var d = draft();
  d.email = $('w-email').value.trim(); d.phone = $('w-phone').value.trim();
  d.industry = $('w-industry').value.trim();
  d.pdv_freq = $('w-pdvfreq').value;
  d.doc_types = Array.prototype.map.call(
    document.querySelectorAll('#w-doctypes input:checked'),
    function(cb){ return cb.value; });
  var res = await fetch('/clients', {method:'POST', credentials:'same-origin',
    headers:{'Content-Type':'application/json'}, body: JSON.stringify(d)});
  if(!res.ok){
    var e = await res.json().catch(function(){ return {}; });
    $('w-error').textContent = 'Gre\\u0161ka: ' + (e.detail || res.status);
    $('w-error').style.display = 'inline-block';
    return;
  }
  var c = await res.json();
  window.location = '/ui/klijent/' + c.id;
}

$('next').addEventListener('click', function(){
  if(cur === 0 && !$('w-name').value.trim()){
    $('w-error').textContent = 'Naziv je obavezan.'; $('w-error').style.display='inline-block'; return;
  }
  $('w-error').style.display = 'none';
  if(cur < STEPS.length - 1) showStep(cur + 1); else create();
});
$('back').addEventListener('click', function(){ showStep(cur - 1); });
$('w-legal').addEventListener('change', function(){ syncRegime(); scheduleAssist(); });
['w-name','w-oib','w-regime','w-pdv','w-emp','w-pausal'].forEach(function(id){
  $(id).addEventListener('input', scheduleAssist);
  $(id).addEventListener('change', scheduleAssist);
});
loadDocTypes();
showStep(0);
scheduleAssist();
"""


def wizard_page() -> str:
    body = f"""<h1>Dodaj novog klijenta</h1>
<p class="meta"><span id="step-label"></span> — ATLAS uz rub provjerava podatke i
predlaže korake dok tipkaš. <a href="/ui/klijenti">← natrag na Klijente</a></p>

<div style="display:flex;gap:1.5rem;flex-wrap:wrap">
<div class="card" style="flex:2;min-width:320px">
  <form class="stack" onsubmit="return false">
    <div id="s1">
      <h2>1. Osnovno</h2>
      <label for="w-name">Naziv</label>
      <input type="text" id="w-name" placeholder="Pekara Mlinar d.o.o.">
      <label for="w-oib">OIB</label>
      <input type="text" id="w-oib" maxlength="11" placeholder="11 znamenki">
      <label for="w-legal">Pravni oblik</label>
      <select id="w-legal">
        <option value="">-</option>
        <option value="obrt">Obrt</option>
        <option value="poduzece">Poduzeće (d.o.o. / j.d.o.o.)</option>
      </select>
      <label for="w-email">Email</label>
      <input type="email" id="w-email">
      <label for="w-phone">Telefon</label>
      <input type="text" id="w-phone">
      <label for="w-industry">Djelatnost</label>
      <input type="text" id="w-industry">
    </div>
    <div id="s2" style="display:none">
      <h2>2. Porezni status</h2>
      <label for="w-regime">Obračun</label>
      <select id="w-regime">
        <option value="">-</option>
        <option value="dobit">Porez na dobit</option>
        <option value="dohodak">Dohodak (obrt, knjige)</option>
        <option value="pausal">Paušalni obrt</option>
      </select>
      <label for="w-pausal">Paušal (EUR/mj)</label>
      <input type="number" id="w-pausal" step="0.01" min="0">
      <label for="w-pdv">PDV status</label>
      <select id="w-pdv">
        <option value="">-</option>
        <option value="u sustavu PDV-a">U sustavu PDV-a</option>
        <option value="nije u sustavu PDV-a">Nije u sustavu PDV-a</option>
      </select>
      <label for="w-pdvfreq">PDV obračun</label>
      <select id="w-pdvfreq">
        <option value="monthly">Mjesečni</option>
        <option value="quarterly">Tromjesečni</option>
      </select>
      <label style="display:flex;gap:.4rem;align-items:center">
        <input type="checkbox" id="w-emp"> Ima zaposlene
      </label>
    </div>
    <div id="s3" style="display:none">
      <h2>3. Dokumenti koji se prate</h2>
      <p class="meta">Vrste dokumenata (skeniranje + automatsko čitanje + podsjetnik na
      istek). Uređuju se u Postavke → Vrste dokumenata.</p>
      <div id="w-doctypes" class="stack"></div>
    </div>
    <div style="display:flex;gap:.5rem;margin-top:1rem">
      <button type="button" class="btn btn-ghost" id="back" style="display:none">← Natrag</button>
      <button type="button" class="btn" id="next">Dalje →</button>
    </div>
    <span id="w-error" class="chip bad" style="display:none;margin-top:.5rem"></span>
  </form>
</div>

<div class="card" style="flex:1;min-width:260px" aria-live="polite">
  <h2>ATLAS provjerava</h2>
  <div style="display:none"><h3>Upozorenja</h3><ul id="as-warn" style="list-style:none;padding:0"></ul></div>
  <div style="display:none"><h3>Prijedlozi</h3><ul id="as-sugg" style="padding-left:1rem"></ul></div>
  <div style="display:none"><h3>Obrasci / koraci</h3><ul id="as-forms" style="padding-left:1rem"></ul></div>
  <div style="display:none"><h3>Iz propisa</h3><ul id="as-src" style="padding-left:1rem"></ul></div>
  <p id="as-note" class="meta" style="display:none"></p>
</div>
</div>
<script>{_WIZ_JS}</script>"""
    return page_shell("Novi klijent", body, active="klijenti")
