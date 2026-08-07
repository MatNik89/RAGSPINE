"""HTML za /ui/org, /ui/wiki, /ui/skills — SaaS spojno tkivo (Postavke).
Sve API-driven: fetch same-origin, textContent (nikad innerHTML za API podatke)."""
from atlas.web.templates_ui import page_shell

_ORG_JS = """
function $(id){ return document.getElementById(id); }
var ROLES = ['viewer','member','admin','owner'];
var MY_ROLE = 'member';

function roleSelect(current, uid){
  var sel = document.createElement('select');
  ROLES.forEach(function(r){
    var o = document.createElement('option');
    o.value = r; o.textContent = r; if (r === current) o.selected = true;
    sel.appendChild(o);
  });
  sel.addEventListener('change', function(){ setRole(uid, sel.value, sel, current); });
  return sel;
}

async function setRole(uid, role, sel, prev){
  var res = await fetch('/org/members/' + uid + '/role', {method:'POST',
    credentials:'same-origin', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({role: role})});
  var msg = $('o-msg');
  if (res.ok){ msg.textContent = 'Uloga spremljena.'; load(); }
  else { sel.value = prev; var e = await res.json().catch(function(){return{};});
         msg.textContent = 'Greška: ' + (e.detail || res.status); }
}

async function addMember(){
  var res = await fetch('/org/members', {method:'POST', credentials:'same-origin',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({username: $('o-user').value.trim(), role: $('o-role').value})});
  var msg = $('o-msg');
  if (res.ok){ $('o-user').value = ''; msg.textContent = 'Član dodan.'; load(); }
  else { var e = await res.json().catch(function(){return{};});
         msg.textContent = 'Greška: ' + (e.detail || res.status); }
}

async function load(){
  var res = await fetch('/org', {credentials:'same-origin'});
  if (!res.ok) return;
  var d = await res.json();
  MY_ROLE = d.role;
  $('o-name').textContent = (d.org && d.org.name) || '?';
  $('o-my-role').textContent = d.role;
  var isAdmin = (d.role === 'admin' || d.role === 'owner');
  $('o-add').style.display = isAdmin ? '' : 'none';
  var tb = $('o-members'); tb.textContent = '';
  d.members.forEach(function(m){
    var tr = document.createElement('tr');
    var td1 = document.createElement('td'); td1.textContent = m.username;
    var td2 = document.createElement('td');
    if (isAdmin){ td2.appendChild(roleSelect(m.role, m.user_id)); }
    else { td2.textContent = m.role; }
    tr.appendChild(td1); tr.appendChild(td2); tb.appendChild(tr);
  });
}

$('o-addbtn').addEventListener('click', addMember);
load();
"""


def org_page() -> str:
    body = f"""<h1>Organizacija</h1>
<p class="meta"><a href="/ui/postavke">← Postavke</a> · Članovi i uloge
(viewer &lt; member &lt; admin &lt; owner). Uloga vrijedi odmah — bez ponovne prijave.</p>
<div class="card" style="max-width:640px">
  <p><b id="o-name"></b> · moja uloga: <span id="o-my-role" class="meta"></span></p>
  <table><thead><tr><th>Korisnik</th><th>Uloga</th></tr></thead>
    <tbody id="o-members"></tbody></table>
  <div id="o-add" style="display:none;margin-top:.75rem">
    <div style="display:flex;gap:.5rem;align-items:center">
      <input type="text" id="o-user" placeholder="korisničko ime">
      <select id="o-role"><option>member</option><option>viewer</option>
        <option>admin</option><option>owner</option></select>
      <button type="button" class="btn" id="o-addbtn">Dodaj člana</button>
    </div>
    <p class="meta">Korisnik mora već imati račun (atlas adduser).</p>
  </div>
  <p id="o-msg" class="meta"></p>
</div>
<script>{_ORG_JS}</script>"""
    return page_shell("Organizacija", body, active="postavke")


_RADNICI_JS = """
function $(id){ return document.getElementById(id); }
var CLIENTS = [];

async function loadClients(){
  var res = await fetch('/clients', {credentials:'same-origin'});
  CLIENTS = res.ok ? await res.json() : [];
}

async function load(){
  await loadClients();
  var res = await fetch('/workers', {credentials:'same-origin'});
  if (!res.ok){ $('r-err').textContent = 'Samo administrator vidi ovu stranicu.'; return; }
  var workers = await res.json();
  var tb = $('r-list'); tb.textContent = '';
  workers.forEach(function(w){
    var tr = document.createElement('tr');
    var td1 = document.createElement('td'); td1.textContent = w.username + ' (' + w.role + ')';
    var td2 = document.createElement('td');
    td2.textContent = w.sees_all ? 'svi klijenti'
                    : (w.client_ids.length + ' odabrano');
    var td3 = document.createElement('td');
    var b = document.createElement('button'); b.className = 'btn btn-ghost'; b.textContent = 'Uredi';
    b.addEventListener('click', function(){ edit(w); });
    td3.appendChild(b);
    tr.appendChild(td1); tr.appendChild(td2); tr.appendChild(td3);
    tb.appendChild(tr);
  });
}

function edit(w){
  $('e-title').textContent = 'Vidljivost — ' + w.username;
  $('e-worker').value = w.user_id;
  var sel = new Set(w.client_ids);
  document.querySelector('input[name=e-mode][value=' + (w.sees_all ? 'all' : 'some') + ']').checked = true;
  var box = $('e-clients'); box.textContent = '';
  CLIENTS.forEach(function(c){
    var lab = document.createElement('label'); lab.style.display = 'block';
    var cb = document.createElement('input'); cb.type = 'checkbox'; cb.value = c.id;
    cb.className = 'e-client'; if (sel.has(c.id)) cb.checked = true;
    lab.appendChild(cb); lab.appendChild(document.createTextNode(' ' + c.name));
    box.appendChild(lab);
  });
  toggleBox();
  $('e-panel').style.display = '';
}

function toggleBox(){
  var some = document.querySelector('input[name=e-mode][value=some]').checked;
  $('e-clients').style.display = some ? '' : 'none';
}

async function save(){
  var some = document.querySelector('input[name=e-mode][value=some]').checked;
  var ids = Array.prototype.map.call(document.querySelectorAll('.e-client:checked'),
                                     function(cb){ return parseInt(cb.value, 10); });
  var res = await fetch('/workers/' + $('e-worker').value + '/visibility', {method:'POST',
    credentials:'same-origin', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({sees_all: !some, client_ids: ids})});
  var msg = $('e-msg');
  if (res.ok){ msg.textContent = 'Spremljeno.'; $('e-panel').style.display = 'none'; load(); }
  else { var e = await res.json().catch(function(){return{};});
         msg.textContent = 'Greška: ' + (e.detail || res.status); }
}

Array.prototype.forEach.call(document.getElementsByName('e-mode'), function(r){
  r.addEventListener('change', toggleBox);
});
$('e-save').addEventListener('click', save);
load();
"""


def radnici_page() -> str:
    body = f"""<h1>Radnici — vidljivost klijenata</h1>
<p class="meta"><a href="/ui/postavke">← Postavke</a> · Zadano svaki radnik vidi
sve klijente. Ovdje možeš pojedinom radniku ograničiti vidljivost na izabrane
klijente. Administrator i vlasnik uvijek vide sve.</p>
<p id="r-err" class="meta"></p>
<div class="card" style="max-width:640px">
  <table><thead><tr><th>Radnik</th><th>Vidi</th><th></th></tr></thead>
    <tbody id="r-list"></tbody></table>
</div>
<div class="card" id="e-panel" style="max-width:640px;display:none">
  <h2 id="e-title"></h2>
  <input type="hidden" id="e-worker">
  <label style="display:block"><input type="radio" name="e-mode" value="all" checked> Vidi sve klijente</label>
  <label style="display:block"><input type="radio" name="e-mode" value="some"> Samo izabrane klijente</label>
  <div id="e-clients" style="margin:.5rem 0;max-height:260px;overflow:auto;display:none"></div>
  <div style="display:flex;gap:.5rem;align-items:center">
    <button type="button" class="btn" id="e-save">Spremi</button>
    <span id="e-msg" class="meta"></span>
  </div>
</div>
<script>{_RADNICI_JS}</script>"""
    return page_shell("Radnici", body, active="postavke")


_WIKI_JS = """
function $(id){ return document.getElementById(id); }
var IS_ADMIN = false;

async function whoami(){
  var res = await fetch('/org', {credentials:'same-origin'});
  if (res.ok){ var d = await res.json(); IS_ADMIN = (d.role==='admin'||d.role==='owner'); }
}

function row(p){
  var tr = document.createElement('tr');
  var td1 = document.createElement('td');
  var a = document.createElement('a'); a.href = '#'; a.textContent = p.title;
  a.addEventListener('click', function(e){ e.preventDefault(); openPage(p.slug); });
  td1.appendChild(a);
  var td2 = document.createElement('td'); td2.textContent = p.type || '';
  var td3 = document.createElement('td'); td3.textContent = 'v' + p.version + (p.locked ? ' 🔒' : '');
  tr.appendChild(td1); tr.appendChild(td2); tr.appendChild(td3);
  return tr;
}

async function load(){
  var res = await fetch('/wiki', {credentials:'same-origin'});
  if (!res.ok) return;
  var tb = $('w-list'); tb.textContent = '';
  (await res.json()).forEach(function(p){ tb.appendChild(row(p)); });
}

async function openPage(slug){
  var res = await fetch('/wiki/' + encodeURIComponent(slug), {credentials:'same-origin'});
  if (!res.ok) return;
  var p = await res.json();
  $('w-title').textContent = p.title + ' (v' + p.version + ')';
  $('w-body').textContent = p.body;
  var lock = $('w-lock');
  lock.style.display = IS_ADMIN ? '' : 'none';
  lock.textContent = p.locked ? 'Otključaj (dozvoli LLM ažuriranje)' : 'Zaključaj (LLM ne smije gaziti)';
  lock.onclick = async function(){
    await fetch('/wiki/' + encodeURIComponent(slug) + '/lock', {method:'POST',
      credentials:'same-origin', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({locked: !p.locked})});
    openPage(slug); load();
  };
  $('w-page').style.display = '';
}

async function search(){
  var q = $('w-q').value.trim();
  if (!q){ load(); return; }
  var res = await fetch('/wiki/search?q=' + encodeURIComponent(q), {credentials:'same-origin'});
  if (!res.ok) return;
  var tb = $('w-list'); tb.textContent = '';
  (await res.json()).forEach(function(p){
    tb.appendChild(row({title: p.title, type: p.type, version: '?', locked: false, slug: p.slug}));
  });
}

$('w-q').addEventListener('keydown', function(e){ if (e.key === 'Enter'){ e.preventDefault(); search(); } });
$('w-search').addEventListener('click', search);
whoami(); load();
"""


def wiki_page() -> str:
    body = f"""<h1>Wiki</h1>
<p class="meta"><a href="/ui/postavke">← Postavke</a> · Interno znanje ureda koje LLM
održava iz dokumenata. Zaključana stranica se nikad ne gazi automatski.</p>
<div class="card" style="max-width:760px">
  <div style="display:flex;gap:.5rem">
    <input type="text" id="w-q" placeholder="pretraži wiki…" style="flex:1">
    <button type="button" class="btn" id="w-search">Traži</button>
  </div>
  <table style="margin-top:.75rem"><thead><tr><th>Stranica</th><th>Tip</th><th>Verzija</th></tr></thead>
    <tbody id="w-list"></tbody></table>
</div>
<div class="card" id="w-page" style="max-width:760px;display:none">
  <h2 id="w-title"></h2>
  <pre id="w-body" style="white-space:pre-wrap"></pre>
  <button type="button" class="btn btn-ghost" id="w-lock" style="display:none"></button>
</div>
<script>{_WIKI_JS}</script>"""
    return page_shell("Wiki", body, active="postavke")


_SKILLS_JS = """
function $(id){ return document.getElementById(id); }
var EDIT_ID = null;

function fillForm(s){
  EDIT_ID = s ? s.id : null;
  $('s-name').value = s ? s.name : '';
  $('s-trigger').value = s ? s.trigger : '';
  $('s-desc').value = s ? s.description : '';
  $('s-steps').value = s ? s.steps : '';
  $('s-valid').value = s ? s.validation : '';
  $('s-vis').value = s ? s.visibility : 'org';
  $('s-save').textContent = s ? 'Spremi izmjene (v' + s.version + ' → v' + (s.version + 1) + ')' : 'Novi skill';
}

async function save(){
  var payload = {name: $('s-name').value.trim(), trigger: $('s-trigger').value.trim(),
    description: $('s-desc').value.trim(), steps: $('s-steps').value,
    validation: $('s-valid').value, visibility: $('s-vis').value};
  var url = EDIT_ID ? '/skills/' + EDIT_ID : '/skills';
  var res = await fetch(url, {method:'POST', credentials:'same-origin',
    headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)});
  var msg = $('s-msg');
  if (res.ok){ msg.textContent = 'Spremljeno.'; fillForm(null); load(); }
  else { var e = await res.json().catch(function(){return{};});
         msg.textContent = 'Greška: ' + (e.detail || res.status); }
}

async function setStatus(id, status){
  var res = await fetch('/skills/' + id + '/status', {method:'POST',
    credentials:'same-origin', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({status: status})});
  if (!res.ok){ var e = await res.json().catch(function(){return{};});
                $('s-msg').textContent = 'Greška: ' + (e.detail || res.status); }
  load();
}

function statusBtn(s){
  var b = document.createElement('button');
  b.className = 'btn btn-ghost';
  if (s.status === 'active'){ b.textContent = 'Arhiviraj';
    b.addEventListener('click', function(){ setStatus(s.id, 'archived'); }); }
  else { b.textContent = 'Aktiviraj';
    b.addEventListener('click', function(){ setStatus(s.id, 'active'); }); }
  return b;
}

async function load(){
  var res = await fetch('/skills', {credentials:'same-origin'});
  if (!res.ok) return;
  var tb = $('s-list'); tb.textContent = '';
  (await res.json()).forEach(function(s){
    var tr = document.createElement('tr');
    var td1 = document.createElement('td');
    var a = document.createElement('a'); a.href = '#'; a.textContent = s.name;
    a.addEventListener('click', function(e){ e.preventDefault(); fillForm(s); });
    td1.appendChild(a);
    var td2 = document.createElement('td'); td2.textContent = s.trigger || '';
    var td3 = document.createElement('td'); td3.textContent = s.status + ' · v' + s.version;
    var td4 = document.createElement('td'); td4.appendChild(statusBtn(s));
    tr.appendChild(td1); tr.appendChild(td2); tr.appendChild(td3); tr.appendChild(td4);
    tb.appendChild(tr);
  });
}

$('s-save').addEventListener('click', save);
$('s-new').addEventListener('click', function(){ fillForm(null); });
load();
"""


def skills_page() -> str:
    body = f"""<h1>Skills</h1>
<p class="meta"><a href="/ui/postavke">← Postavke</a> · Interni postupci ureda
(okidač + koraci). Aktivan skill se automatski nudi chatu kad upit pogodi okidač.</p>
<div class="card" style="max-width:760px">
  <table><thead><tr><th>Skill</th><th>Okidač</th><th>Status</th><th></th></tr></thead>
    <tbody id="s-list"></tbody></table>
</div>
<div class="card" style="max-width:760px">
  <form class="stack" onsubmit="return false">
    <label for="s-name">Naziv</label>
    <input type="text" id="s-name" placeholder="npr. JOPPD predaja">
    <label for="s-trigger">Okidač <span class="meta">(riječi koje pale skill)</span></label>
    <input type="text" id="s-trigger" placeholder="npr. joppd, predaja obrasca">
    <label for="s-desc">Opis</label>
    <input type="text" id="s-desc">
    <label for="s-steps">Koraci</label>
    <textarea id="s-steps" rows="6" placeholder="1. …&#10;2. …"></textarea>
    <label for="s-valid">Validacija <span class="meta">(kako provjeriti da je dobro)</span></label>
    <textarea id="s-valid" rows="2"></textarea>
    <label for="s-vis">Vidljivost</label>
    <select id="s-vis"><option value="org">cijela organizacija</option>
      <option value="team">tim</option><option value="private">samo ja</option></select>
    <div style="display:flex;gap:.5rem;align-items:center">
      <button type="button" class="btn" id="s-save">Novi skill</button>
      <button type="button" class="btn btn-ghost" id="s-new">Očisti formu</button>
      <span id="s-msg" class="meta"></span>
    </div>
  </form>
</div>
<script>{_SKILLS_JS}</script>"""
    return page_shell("Skills", body, active="postavke")
