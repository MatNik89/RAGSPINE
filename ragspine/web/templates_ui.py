"""HTML za worker WebUI (/, /ui/chat, /ui/upute) — čisti f-string builder,
bez template engine ovisnosti. Design-system shell (vidi .sdd/ui-DESIGN.md):
IBM Plex Sans/Mono self-hosted, dark-default theme-aware paleta, komponente
(.nav/.card/.tile/.ledger/.oblig-row/.btn/.chip) koje kasniji ekrani (U3-U5)
nasljeđuju preko iste CSS_TOKENS."""
import html

_FONT_FACES = "".join(
    f"""@font-face{{font-family:'IBM Plex Sans';src:url('/static/fonts/PlexSans-{w}.woff2') format('woff2');
  font-weight:{w};font-style:normal;font-display:swap;}}\n"""
    for w in (400, 500, 600, 700)
) + "".join(
    f"""@font-face{{font-family:'IBM Plex Mono';src:url('/static/fonts/PlexMono-{w}.woff2') format('woff2');
  font-weight:{w};font-style:normal;font-display:swap;}}\n"""
    for w in (400, 500)
)

CSS_TOKENS = _FONT_FACES + """
:root{
  --bg:#020617; --surface:#0E1223; --surface-2:#0F172A; --border:#334155; --text:#F8FAFC; --muted:#94A3B8;
  --accent:#6366F1; --accent-fg:#FFFFFF; --ok:#22C55E; --warn:#F59E0B; --bad:#EF4444;
  --font-sans:'IBM Plex Sans',system-ui,sans-serif; --font-mono:'IBM Plex Mono',ui-monospace,monospace;
}
[data-theme="light"]{
  --bg:#F8FAFC; --surface:#FFFFFF; --surface-2:#F1F5F9; --border:#E2E8F0; --text:#0F172A; --muted:#64748B;
}
*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:var(--font-sans);min-height:100vh}
a{color:var(--accent)}
h1{font-size:1.4rem;margin:0 0 .3rem}
h2{font-size:1.05rem;margin:1.5rem 0 .5rem}
.container{max-width:1200px;margin:0 auto;padding:1.5rem 1.25rem 3rem}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}

/* nav */
.nav{display:flex;align-items:center;gap:.25rem;flex-wrap:wrap;padding:.75rem 1.25rem;
  background:var(--surface);border-bottom:1px solid var(--border);position:sticky;top:0;z-index:10}
.nav .brand{font-weight:700;letter-spacing:.02em;margin-right:1rem;color:var(--text)}
.nav a{color:var(--muted);text-decoration:none;padding:.4rem .7rem;border-radius:6px;font-size:.9rem;
  transition:color .12s,background-color .12s}
.nav a:hover{color:var(--text);background:var(--surface-2)}
.nav a.active{color:var(--text);box-shadow:inset 0 -2px 0 var(--accent)}
.nav .spacer{flex:1}
.nav .theme-toggle{background:none;border:1px solid var(--border);color:var(--text);border-radius:6px;
  padding:.35rem .6rem;cursor:pointer;font-size:1rem;line-height:1;margin-right:.5rem}
.nav .theme-toggle:hover{background:var(--surface-2)}
.nav a.logout{color:var(--muted)}

/* grid + card */
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:1rem;margin-top:1rem}
.card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:1.1rem 1.25rem;
  text-decoration:none;color:var(--text);display:block;box-shadow:0 1px 2px rgba(0,0,0,.15);
  opacity:0;animation:card-in .3s ease forwards;transition:transform .12s,border-color .12s}
.card:hover{border-color:var(--accent);transform:translateY(-1px)}
.card h2{margin:0 0 .3rem;font-size:1.05rem;font-weight:600}
.card p{margin:0;color:var(--muted);font-size:.85rem}
.grid .card:nth-child(1){animation-delay:0ms}
.grid .card:nth-child(2){animation-delay:60ms}
.grid .card:nth-child(3){animation-delay:120ms}
.grid .card:nth-child(4){animation-delay:180ms}
.grid .card:nth-child(5){animation-delay:240ms}
.grid .card:nth-child(6){animation-delay:300ms}
@keyframes card-in{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:translateY(0)}}

/* stat tile */
.tile{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:1.1rem 1.25rem}
.tile-num{font-family:var(--font-mono);font-variant-numeric:tabular-nums;font-size:1.8rem;font-weight:600;
  color:var(--text);display:block;line-height:1.2}
.tile-label{color:var(--muted);font-size:.8rem;margin-top:.25rem;display:block}

/* ledger table */
table.ledger{width:100%;border-collapse:collapse}
table.ledger th{text-align:left;font-size:.72rem;text-transform:uppercase;letter-spacing:.03em;color:var(--muted);
  padding:.55rem .75rem;border-bottom:1px solid var(--border);position:sticky;top:0;background:var(--surface)}
table.ledger td{padding:.55rem .75rem;border-bottom:1px solid var(--border);vertical-align:top}
table.ledger th.num,table.ledger td.num{font-family:var(--font-mono);font-variant-numeric:tabular-nums;text-align:right}
table.ledger tbody tr:nth-child(even){background:var(--surface-2)}

/* obligation/deadline row — 3px left urgency bar via --state */
.oblig-row{--state:var(--muted);display:flex;align-items:center;gap:.75rem;padding:.6rem .9rem;
  border-left:3px solid var(--state);background:var(--surface);border-radius:6px;margin-bottom:.4rem}
.oblig-row.ok{--state:var(--ok)}
.oblig-row.warn{--state:var(--warn)}
.oblig-row.bad{--state:var(--bad)}
.oblig-row .due{font-family:var(--font-mono);font-variant-numeric:tabular-nums;margin-left:auto;color:var(--muted)}

/* buttons */
.btn{display:inline-block;background:var(--accent);color:var(--accent-fg);border:none;border-radius:6px;
  padding:.5rem 1rem;font-size:.9rem;cursor:pointer;text-decoration:none;font-family:var(--font-sans);
  transition:opacity .12s}
.btn:hover{opacity:.9}
.btn:disabled{opacity:.5;cursor:default}
.btn-ghost{background:transparent;border:1px solid var(--border);color:var(--text)}
.btn-danger{background:var(--bad);color:#fff}

/* chips */
.chip{display:inline-block;font-size:.75rem;padding:.15rem .55rem;border-radius:999px;
  background:var(--surface-2);color:var(--muted)}
.chip.ok{background:rgba(34,197,94,.15);color:var(--ok)}
.chip.warn{background:rgba(245,158,11,.15);color:var(--warn)}
.chip.bad{background:rgba(239,68,68,.15);color:var(--bad)}

/* forms */
input,textarea,select{background:var(--surface-2);border:1px solid var(--border);color:var(--text);
  border-radius:6px;padding:.5rem .6rem;font-size:.9rem;font-family:inherit}
input:focus,textarea:focus,select:focus{outline:2px solid var(--accent);outline-offset:1px;border-color:var(--accent)}
label{font-size:.85rem;color:var(--muted)}
form.stack{display:flex;flex-direction:column;gap:.5rem;max-width:520px}

/* chat */
#chat-log{display:flex;flex-direction:column;gap:.6rem;min-height:200px;margin-bottom:1rem}
.msg{padding:.6rem .8rem;border-radius:8px;max-width:80%;font-size:.92rem}
.msg.user{align-self:flex-end;background:var(--accent);color:var(--accent-fg)}
.msg.assistant{align-self:flex-start;background:var(--surface-2)}
.msg.error{align-self:flex-start;background:rgba(239,68,68,.15);color:var(--bad)}
.msg.clarify{align-self:flex-start;background:rgba(245,158,11,.15);color:var(--warn);border:1px solid var(--border)}
.sources{font-size:.8rem;color:var(--muted);margin-top:.3rem}
.variants{display:flex;flex-wrap:wrap;gap:.4rem;margin-top:.5rem}
.variants button{background:var(--surface);color:var(--text);border:1px solid var(--border);border-radius:6px;
  padding:.3rem .6rem;font-size:.8rem;cursor:pointer}
.chat-input{display:flex;gap:.5rem}
.chat-input input{flex:1}
.meta{font-size:.8rem;color:var(--muted)}

@media (max-width:640px){
  .grid{grid-template-columns:1fr}
  .container{padding:1rem .75rem 2rem}
}
@media (prefers-reduced-motion: reduce){
  *{animation:none!important;transition:none!important}
}
"""

_NAV = [
    ("home", "/", "Nadzorna ploča"),
    ("chat", "/ui/chat", "Chat"),
    ("klijenti", "/ui/klijenti", "Klijenti"),
    ("upute", "/ui/upute", "Upute"),
    ("obveze", "/obveze", "Obveze"),
    ("rokovi", "/ui/rokovi", "Rokovi"),
]

# Inline, CSP-safe (no external script). Runs as the first thing inside
# <body> — before nav/main are parsed — so the theme is set before paint
# (no flash): localStorage wins, else prefers-color-scheme, else dark default.
_THEME_INIT_JS = """
(function(){
  var saved = null;
  try { saved = localStorage.getItem('ragspine-theme'); } catch (e) {}
  var theme = saved || (window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark');
  document.body.setAttribute('data-theme', theme);
})();
"""

_THEME_TOGGLE_JS = """
function toggleTheme() {
  var body = document.body;
  var next = body.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
  body.setAttribute('data-theme', next);
  try { localStorage.setItem('ragspine-theme', next); } catch (e) {}
  var btn = document.getElementById('theme-toggle');
  if (btn) btn.textContent = next === 'light' ? '\\u2600' : '\\u263E';
}
"""


def page_shell(title: str, body_html: str, active: str = "") -> str:
    title_e = html.escape(title)
    links = []
    for key, href, label in _NAV:
        cls = ' class="active"' if key == active else ""
        links.append(f'<a href="{html.escape(href)}"{cls}>{html.escape(label)}</a>')
    nav_links = "".join(links)
    return f"""<!DOCTYPE html>
<html lang="hr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title_e} — RAGSPINE</title>
<style>{CSS_TOKENS}</style>
</head>
<body>
<script>{_THEME_INIT_JS}</script>
<nav class="nav" aria-label="Glavna navigacija">
<span class="brand">RAGSPINE</span>
{nav_links}
<span class="spacer"></span>
<button type="button" id="theme-toggle" class="theme-toggle" aria-label="Promijeni temu"
  onclick="toggleTheme()">&#9788;</button>
<a href="/logout" class="logout">Odjava</a>
</nav>
<main class="container">
{body_html}
</main>
<script>{_THEME_TOGGLE_JS}</script>
</body>
</html>"""


def home_page() -> str:
    body = """<h1>Dobrodošli u RAGSPINE</h1>
<p>Asistent za knjigovodstvo — odaberite alat:</p>
<div class="grid">
  <a class="card" href="/ui/chat"><h2>Chat</h2><p>Postavite pitanje asistentu.</p></a>
  <a class="card" href="/ui/upute"><h2>Upute</h2><p>Interne procedure (SOP) — kreiranje i pregled.</p></a>
  <a class="card" href="/obveze"><h2>Obveze</h2><p>PDV, doprinosi i ostale periodičke obveze.</p></a>
  <a class="card" href="/kalendar"><h2>Rokovi</h2><p>Nadolazeći rokovi i istekle stavke.</p></a>
</div>"""
    return page_shell("Početna", body, active="home")


_CHAT_JS = """
const log = document.getElementById('chat-log');
const q = document.getElementById('q');
const sendBtn = document.getElementById('send');

function addMsg(cls, text) {
  const div = document.createElement('div');
  div.className = 'msg ' + cls;
  div.textContent = text;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
  return div;
}

function addAssistant(data) {
  const cls = data.clarify ? 'assistant clarify' : 'assistant';
  const div = addMsg(cls, data.answer || '');
  if (data.client && data.client.name) {
    const chip = document.createElement('div');
    chip.className = 'chip';
    chip.textContent = 'Klijent: ' + data.client.name;
    div.insertBefore(chip, div.firstChild);
  }
  if (Array.isArray(data.sources) && data.sources.length) {
    const src = document.createElement('div');
    src.className = 'sources';
    src.textContent = 'Izvori: ' + data.sources.map(function (s) {
      return '[' + s.n + '] ' + s.title;
    }).join(', ');
    div.appendChild(src);
  }
  if (data.clarify && Array.isArray(data.variants) && data.variants.length) {
    const wrap = document.createElement('div');
    wrap.className = 'variants';
    data.variants.forEach(function (v) {
      const b = document.createElement('button');
      b.type = 'button';
      b.textContent = v.title || v.category || '?';
      b.addEventListener('click', function () { q.value = b.textContent; q.focus(); });
      wrap.appendChild(b);
    });
    div.appendChild(wrap);
  }
}

async function send() {
  const text = q.value.trim();
  if (!text) return;
  addMsg('user', text);
  q.value = '';
  sendBtn.disabled = true;
  try {
    const res = await fetch('/chat', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ q: text, fresh: false }),
    });
    if (!res.ok) {
      addMsg('error', 'Greška: ' + res.status);
      return;
    }
    const data = await res.json();
    addAssistant(data);
  } catch (err) {
    addMsg('error', 'Greška u komunikaciji sa serverom.');
  } finally {
    sendBtn.disabled = false;
    q.focus();
  }
}

sendBtn.addEventListener('click', send);
q.addEventListener('keydown', function (e) {
  if (e.key === 'Enter') { e.preventDefault(); send(); }
});
"""


def chat_page() -> str:
    body = f"""<h1>Chat</h1>
<div id="chat-log"></div>
<div class="chat-input">
  <input type="text" id="q" placeholder="Postavite pitanje..." autofocus>
  <button type="button" class="btn" id="send">Pošalji</button>
</div>
<script>{_CHAT_JS}</script>"""
    return page_shell("Chat", body, active="chat")


def _pending_rows_html(pending_rows: list[dict]) -> str:
    if not pending_rows:
        return '<tr><td colspan="4">Nema SOP-ova na čekanju.</td></tr>'
    rows = []
    for r in pending_rows:
        sop_id = int(r["id"])
        title = html.escape(str(r["title"]))
        category = html.escape(str(r.get("category") or ""))
        author = html.escape(str(r.get("author") or ""))
        rows.append(f"""<tr>
  <td>{title}<div class="meta">{author}</div></td>
  <td>{category}</td>
  <td>
    <button type="button" class="btn" onclick="submitSop({sop_id})">Predaj</button>
    <button type="button" class="btn btn-ghost" onclick="approveSop({sop_id})">Odobri</button>
  </td>
  <td>
    <input type="file" id="img-{sop_id}" accept="image/*">
    <button type="button" class="btn btn-ghost" onclick="uploadImage({sop_id})">Učitaj sliku</button>
  </td>
</tr>""")
    return "\n".join(rows)


_UPUTE_JS = """
async function post(url) {
  const res = await fetch(url, { method: 'POST', credentials: 'same-origin' });
  if (!res.ok) { alert('Greška: ' + res.status); return null; }
  return res.json();
}

async function submitSop(id) {
  await post('/sop/' + id + '/submit');
  location.reload();
}

async function approveSop(id) {
  await post('/sop/' + id + '/approve');
  location.reload();
}

function readAsBase64(file) {
  return new Promise(function (resolve, reject) {
    const reader = new FileReader();
    reader.onload = function () {
      const result = reader.result;
      resolve(result.substring(result.indexOf(',') + 1));
    };
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

async function uploadImage(id) {
  const input = document.getElementById('img-' + id);
  const file = input.files[0];
  if (!file) return;
  const data_base64 = await readAsBase64(file);
  const res = await fetch('/sop/' + id + '/image', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ filename: file.name, data_base64: data_base64, caption: '' }),
  });
  if (!res.ok) { alert('Greška: ' + res.status); return; }
  alert('Slika učitana.');
}

document.getElementById('sop-form').addEventListener('submit', async function (e) {
  e.preventDefault();
  const title = document.getElementById('sop-title').value.trim();
  const category = document.getElementById('sop-category').value.trim();
  const content = document.getElementById('sop-content').value.trim();
  const clientRaw = document.getElementById('sop-client').value.trim();
  if (!title || !content) return;
  const body = { title: title, category: category, content: content };
  if (clientRaw) body.client_id = parseInt(clientRaw, 10);
  const res = await fetch('/sop', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) { alert('Greška: ' + res.status); return; }
  location.reload();
});
"""


def upute_page(pending_rows: list[dict]) -> str:
    rows_html = _pending_rows_html(pending_rows)
    body = f"""<h1>Upute (SOP)</h1>
<h2>Nova uputa</h2>
<form id="sop-form" class="stack">
  <input type="text" id="sop-title" placeholder="Naslov" required>
  <input type="text" id="sop-category" placeholder="Kategorija">
  <input type="number" id="sop-client" placeholder="ID klijenta (opcionalno)">
  <textarea id="sop-content" rows="8" placeholder="Sadržaj" required></textarea>
  <button type="submit" class="btn">Spremi kao skicu</button>
</form>
<h2>Na čekanju pregleda</h2>
<table class="ledger">
<thead><tr><th>Naslov</th><th>Kategorija</th><th>Akcije</th><th>Slika</th></tr></thead>
<tbody>
{rows_html}
</tbody>
</table>
<script>{_UPUTE_JS}</script>"""
    return page_shell("Upute", body, active="upute")
