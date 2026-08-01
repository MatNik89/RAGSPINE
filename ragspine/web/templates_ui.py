"""HTML za worker WebUI (/, /ui/chat, /ui/upute) — čisti f-string builder,
bez template engine ovisnosti. Isti stil kao templates_obveze.py."""
import html

_CSS = """
body{font-family:system-ui,sans-serif;max-width:900px;margin:0 auto;padding:0 1rem 2rem;color:#1a1a1a}
nav.topnav{display:flex;flex-wrap:wrap;align-items:center;gap:.25rem;margin:0 -1rem 1.5rem;padding:.75rem 1rem;
  background:#1e293b;color:#fff}
nav.topnav .brand{font-weight:700;margin-right:1rem}
nav.topnav a{color:#cbd5e1;text-decoration:none;padding:.4rem .6rem;border-radius:4px;font-size:.9rem}
nav.topnav a:hover{background:#334155;color:#fff}
nav.topnav a.active{background:#2563eb;color:#fff}
nav.topnav a.logout{margin-left:auto}
h1{font-size:1.4rem}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:1rem;margin-top:1.5rem}
.card{display:block;padding:1rem;border:1px solid #ddd;border-radius:8px;text-decoration:none;color:#1a1a1a}
.card:hover{border-color:#2563eb;background:#f0f5ff}
.card h2{margin:0 0 .3rem;font-size:1.1rem}
.card p{margin:0;font-size:.85rem;opacity:.8}
button{padding:.4rem .9rem;border:none;border-radius:4px;cursor:pointer;font-size:.9rem;background:#2563eb;color:#fff}
button:disabled{opacity:.6;cursor:default}
input,textarea,select{padding:.4rem .5rem;font-size:1rem;font-family:inherit}
table{width:100%;border-collapse:collapse;margin-top:.5rem}
th,td{padding:.5rem .6rem;text-align:left;border-bottom:1px solid #ddd;vertical-align:top}
.meta{font-size:.8rem;opacity:.7}
form.stack{display:flex;flex-direction:column;gap:.5rem;max-width:520px}
#chat-log{display:flex;flex-direction:column;gap:.6rem;min-height:200px;margin-bottom:1rem}
.msg{padding:.6rem .8rem;border-radius:8px;max-width:80%}
.msg.user{align-self:flex-end;background:#2563eb;color:#fff}
.msg.assistant{align-self:flex-start;background:#eef2f7}
.msg.error{align-self:flex-start;background:#fdecea;color:#7a1f14}
.msg.clarify{align-self:flex-start;background:#fff8e1;color:#7a5b14;border:1px solid #f0d98c}
.chip{display:inline-block;font-size:.75rem;background:#eafaf1;color:#14532d;padding:.1rem .5rem;
  border-radius:12px;margin-bottom:.3rem}
.sources{font-size:.8rem;opacity:.8;margin-top:.3rem}
.variants{display:flex;flex-wrap:wrap;gap:.4rem;margin-top:.5rem}
.variants button{background:#fff;color:#7a5b14;border:1px solid #f0d98c}
.chat-input{display:flex;gap:.5rem}
.chat-input input{flex:1}
"""

_NAV = [
    ("home", "/", "Početna"),
    ("chat", "/ui/chat", "Chat"),
    ("upute", "/ui/upute", "Upute"),
    ("obveze", "/obveze", "Obveze"),
    ("kalendar", "/kalendar", "Kalendar"),
    ("dashboard", "/dashboard", "Dashboard"),
]


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
<style>{_CSS}</style>
</head>
<body>
<nav class="topnav">
<span class="brand">RAGSPINE</span>
{nav_links}
<a href="/logout" class="logout">Odjava</a>
</nav>
<main>
{body_html}
</main>
</body>
</html>"""


def home_page() -> str:
    body = """<h1>Dobrodošli u RAGSPINE</h1>
<p>Asistent za knjigovodstvo — odaberite alat:</p>
<div class="cards">
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
  <button type="button" id="send">Pošalji</button>
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
    <button type="button" onclick="submitSop({sop_id})">Predaj</button>
    <button type="button" onclick="approveSop({sop_id})">Odobri</button>
  </td>
  <td>
    <input type="file" id="img-{sop_id}" accept="image/*">
    <button type="button" onclick="uploadImage({sop_id})">Učitaj sliku</button>
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
  <button type="submit">Spremi kao skicu</button>
</form>
<h2>Na čekanju pregleda</h2>
<table>
<thead><tr><th>Naslov</th><th>Kategorija</th><th>Akcije</th><th>Slika</th></tr></thead>
<tbody>
{rows_html}
</tbody>
</table>
<script>{_UPUTE_JS}</script>"""
    return page_shell("Upute", body, active="upute")
