"""HTML za worker WebUI (/, /ui/chat, /ui/upute) — čisti f-string builder,
bez template engine ovisnosti. Design-system shell (vidi .sdd/ui-DESIGN.md):
IBM Plex Sans/Mono self-hosted, dark-default theme-aware paleta, komponente
(.nav/.card/.tile/.ledger/.oblig-row/.btn/.chip) koje kasniji ekrani (U3-U5)
nasljeđuju preko iste CSS_TOKENS."""
import html
import json


def script_json(value) -> str:
    """json.dumps() for embedding a value into an inline <script> tag.
    json.dumps does NOT escape '</script>' or the JS-illegal line separators
    U+2028/U+2029 — an attacker-controlled string containing
    '</script><script>...' would otherwise close the tag early and execute
    as raw HTML/script content (a reflected-XSS breakout independent of any
    server-side value validation). All three are valid inside a JS string
    once \\u-escaped, so this is purely a defense-in-depth belt on top of
    input validation, never a substitute for it."""
    return (json.dumps(value)
            .replace("<", "\\u003c")
            .replace(" ", "\\u2028")
            .replace(" ", "\\u2029"))


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
/* Warm-paper "knjigovodstveni rokovnik" system (light default, warm-dark
   override). Colour is status-only: oxblood accent = attention, green = done,
   amber = this week, oxblood = overdue. See mockup C / .sdd/ui-DESIGN.md. */
:root{
  --bg:#EFEBE0; --surface:#F7F5EF; --surface-2:#FBFAF5; --border:#DBD5C7; --border-2:#C7BFAD;
  --text:#16130E; --muted:#7A7266;
  --accent:#9B2C2C; --accent-ink:#7A1F1F; --accent-fg:#FFFFFF;
  --ok:#2F7D4F; --okbg:#E4EEE3; --warn:#B45309; --warnbg:#F3E9D6; --bad:#9B2C2C; --badbg:#F3E3DE;
  --font-sans:'IBM Plex Sans',system-ui,sans-serif; --font-mono:'IBM Plex Mono',ui-monospace,monospace;
}
[data-theme="dark"]{
  --bg:#17140F; --surface:#211D16; --surface-2:#2A251C; --border:#3D3629; --border-2:#4A4232;
  --text:#F5F1E8; --muted:#A69E8C;
  --accent:#C25B4E; --accent-ink:#D9776B; --accent-fg:#1A0E0C;
  --ok:#5FBF88; --okbg:#20302A; --warn:#E0A44E; --warnbg:#332818; --bad:#D9776B; --badbg:#33201D;
}
*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:var(--font-sans);min-height:100vh}
a{color:var(--accent)}
h1{font-size:1.4rem;margin:0 0 .3rem}
h2{font-size:1.05rem;margin:1.5rem 0 .5rem}
.container{max-width:1200px;margin:0 auto;padding:1.5rem 1.25rem 3rem}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}

/* layout + lijevi sidebar */
.layout{display:grid;grid-template-columns:220px 1fr;min-height:100vh}
.sidebar{display:flex;flex-direction:column;gap:.35rem;padding:1rem .75rem;
  background:var(--surface);border-right:1px solid var(--border);position:sticky;top:0;height:100vh;z-index:10}
.sidebar .brand{font-weight:700;letter-spacing:.02em;color:var(--text);padding:.3rem .6rem .6rem}
.sidebar nav{display:flex;flex-direction:column;gap:.1rem}
.sidebar a{color:var(--muted);text-decoration:none;padding:.45rem .6rem;border-radius:6px;font-size:.9rem;
  transition:color .12s,background-color .12s}
.sidebar a:hover{color:var(--text);background:var(--surface-2)}
.sidebar a.active{color:var(--text);background:var(--surface-2);font-weight:600;box-shadow:inset 2px 0 0 var(--accent)}
.sidebar .spacer{flex:1}
.sidebar .theme-toggle{background:none;border:1px solid var(--border);color:var(--text);border-radius:6px;
  padding:.35rem .6rem;cursor:pointer;font-size:1rem;line-height:1;align-self:flex-start}
.sidebar .theme-toggle:hover{background:var(--surface-2)}
.sidebar a.logout{color:var(--muted)}
@media(max-width:640px){.layout{grid-template-columns:1fr}
  .sidebar{position:static;height:auto;flex-direction:row;flex-wrap:wrap;align-items:center;
    border-right:0;border-bottom:1px solid var(--border)}
  .sidebar nav{flex-direction:row;flex-wrap:wrap}.sidebar .spacer{flex:0}}

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
.tile-num.bad{color:var(--bad)}

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
.chip{display:inline-block;font-size:.75rem;padding:.15rem .55rem;border-radius:6px;
  font-family:var(--font-mono);font-variant-numeric:tabular-nums;
  background:var(--surface-2);color:var(--muted);border:1px solid var(--border)}
.chip.ok{background:var(--okbg);color:var(--ok);border-color:transparent}
.chip.warn{background:var(--warnbg);color:var(--warn);border-color:transparent}
.chip.bad{background:var(--badbg);color:var(--bad);border-color:transparent}

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

/* ---- dashboard: calendar hero + rail + board ---- */
.dash-head{margin:.5rem 0 1.25rem}
.eyebrow{font-size:.72rem;font-weight:600;letter-spacing:.14em;text-transform:uppercase;color:var(--accent)}
.dash-head h1{font-size:2rem;letter-spacing:-.02em;margin:.15rem 0 .1rem}
.dash-head .sub{color:var(--muted);font-size:.92rem}
.dash-wrap{display:grid;grid-template-columns:1fr 320px;gap:1.25rem;align-items:start}
.panel{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:1rem 1.15rem;
  box-shadow:0 1px 2px rgba(22,19,14,.05)}
.panel-head{display:flex;align-items:baseline;justify-content:space-between;margin-bottom:.85rem}
.panel-title{font-size:.74rem;font-weight:600;letter-spacing:.12em;text-transform:uppercase;color:var(--muted)}
.panel-count{font-family:var(--font-mono);font-variant-numeric:tabular-nums;color:var(--muted);font-size:.85rem}
.panel-count.bad{color:var(--bad)}

/* month calendar */
.cal-head{display:flex;align-items:baseline;justify-content:space-between;margin-bottom:.9rem}
.cal-month{font-size:1.25rem;font-weight:700;letter-spacing:-.01em}
.cal-legend{display:flex;gap:.9rem;font-size:.72rem;color:var(--muted)}
.cal-legend i{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:.3rem;vertical-align:1px}
.dow{display:grid;grid-template-columns:repeat(7,1fr);gap:6px;margin-bottom:6px}
.dow span{font-size:.66rem;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);padding-left:2px}
.cal-grid{display:grid;grid-template-columns:repeat(7,1fr);gap:6px}
.cal-d{min-height:66px;border:1px solid var(--border);border-radius:8px;padding:5px 6px;background:var(--surface-2);position:relative}
.cal-d.empty{background:transparent;border:none}
.cal-dn{font-family:var(--font-mono);font-variant-numeric:tabular-nums;font-size:.78rem;color:var(--muted);font-weight:500}
.cal-d.today{border:2px solid var(--accent);background:var(--okbg)}
.cal-d.today .cal-dn{color:var(--accent-ink);font-weight:700}
.cal-d.today:after{content:'DANAS';position:absolute;top:6px;right:7px;font-family:var(--font-mono);
  font-size:.56rem;letter-spacing:.08em;color:var(--accent);font-weight:700}
.cal-ev{display:block;margin-top:4px;font-size:.68rem;font-weight:600;padding:1px 5px;border-radius:4px;
  font-family:var(--font-mono);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.cal-ev.bad{background:var(--badbg);color:var(--bad)}
.cal-ev.warn{background:var(--warnbg);color:var(--warn)}
.cal-ev.ok{background:var(--okbg);color:var(--ok)}

/* right rail */
.rail{display:flex;flex-direction:column;gap:1rem}
.trow{display:flex;align-items:center;justify-content:space-between;gap:.5rem;padding:.6rem 0;border-top:1px solid var(--border)}
.trow:first-of-type{border-top:none}
.trow .tt{font-weight:600;font-size:.9rem}
.trow .ts{font-size:.78rem;color:var(--muted);margin-top:1px}
.kpis{display:grid;grid-template-columns:1fr 1fr;gap:.6rem}
.kpi{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:.7rem .85rem}
.kpi .kn{font-family:var(--font-mono);font-variant-numeric:tabular-nums;font-size:1.6rem;font-weight:600;line-height:1;letter-spacing:-.02em}
.kpi .kn.bad{color:var(--bad)}
.kpi .kl{font-size:.68rem;color:var(--muted);letter-spacing:.04em;text-transform:uppercase;margin-top:.35rem}

/* board (grouped-by-client + notifications) */
.dash-board{display:grid;grid-template-columns:1fr 1fr;gap:1.25rem;margin-top:1.25rem}
.urow{display:flex;align-items:center;justify-content:space-between;gap:.5rem;padding:.65rem 0;border-top:1px solid var(--border)}
.urow:first-of-type{border-top:none}
.urow .uname{font-weight:600;font-size:.9rem;color:var(--text);text-decoration:none}
.urow .uname:hover{color:var(--accent)}
.urow .ukinds{display:flex;gap:.35rem;flex-wrap:wrap;justify-content:flex-end}
.nrow{display:flex;gap:.6rem;align-items:center;padding:.6rem 0;border-top:1px solid var(--border)}
.nrow:first-of-type{border-top:none}
.nrow .ntext{font-size:.9rem}

/* ---- obveze: type tabs + month nav + checkbox board ---- */
.obveze-tabs{display:flex;gap:.5rem;margin:1rem 0 .25rem;flex-wrap:wrap}
.obveze-tab{padding:.5rem 1.2rem;border:1px solid var(--border);border-radius:8px;background:var(--surface);
  color:var(--muted);text-decoration:none;font-weight:600;font-size:.95rem;transition:color .12s,background-color .12s}
.obveze-tab:hover{color:var(--text)}
.obveze-tab.active{background:var(--accent);color:var(--accent-fg);border-color:transparent}
.month-nav{display:flex;align-items:center;gap:.4rem;margin:.75rem 0 1.5rem}
.month-nav a.step{display:inline-flex;align-items:center;justify-content:center;width:2rem;height:2rem;
  border:1px solid var(--border);border-radius:8px;color:var(--text);text-decoration:none;background:var(--surface)}
.month-nav a.step:hover{border-color:var(--accent);color:var(--accent)}
.month-nav .month-label{font-weight:600;min-width:9.5rem;text-align:center;font-size:1rem}
.oblig-section{margin-bottom:1.75rem}
.oblig-section h2{display:flex;align-items:center;gap:.5rem;font-size:1rem;margin:0 0 .75rem}
.oblig-section .sec-count{font-family:var(--font-mono);font-variant-numeric:tabular-nums;color:var(--muted);font-weight:500}
.oblig-check{display:flex;align-items:center;gap:.6rem;cursor:pointer;margin:0}
.oblig-check input{width:1.15rem;height:1.15rem;accent-color:var(--accent);cursor:pointer;flex:none}
.oblig-row .oname{color:var(--text);font-weight:500}
.oblig-row .ostatus{margin-left:auto}
.obveze-tab.tab-add{border-style:dashed;font-weight:500;color:var(--muted)}
.obveze-tab.tab-add:hover{color:var(--accent);border-color:var(--accent)}
@media (max-width:640px){
  .grid{grid-template-columns:1fr}
  .container{padding:1rem .75rem 2rem}
  .cal-d{min-height:54px}
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
    ("obavijesti", "/ui/obavijesti", "Obavijesti"),
    ("pracenje", "/ui/pracenje", "Praćenje"),
    ("dokumenti", "/ui/dokumenti", "Dokumenti"),
    ("postavke", "/ui/postavke", "Postavke"),
]

# Unseen-notifications badge next to the "Obavijesti" nav link. Best-effort:
# a failed fetch just leaves the badge hidden, never breaks the page it rides on.
_NAV_BADGE_JS = """
(function(){
  var badge = document.getElementById('nav-unseen');
  if (!badge) return;
  fetch('/notifications.json', { credentials: 'same-origin' })
    .then(function (r) { return r.ok ? r.json() : []; })
    .then(function (rows) {
      var n = rows.filter(function (r) { return !r.seen; }).length;
      if (n > 0) { badge.textContent = String(n); badge.style.display = 'inline-block'; }
    })
    .catch(function () {});
})();
"""

# Inline, CSP-safe (no external script). Runs as the first thing inside
# <body> — before nav/main are parsed — so the theme is set before paint
# (no flash): localStorage wins, else prefers-color-scheme, else dark default.
_THEME_INIT_JS = """
(function(){
  var saved = null;
  try { saved = localStorage.getItem('ragspine-theme'); } catch (e) {}
  var theme = saved || (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
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
  if (btn) btn.textContent = next === 'light' ? '\\u263E' : '\\u2600';
}
(function(){
  var b = document.getElementById('theme-toggle');
  if (b) b.textContent = document.body.getAttribute('data-theme') === 'light' ? '\\u263E' : '\\u2600';
})();
"""


def page_shell(title: str, body_html: str, active: str = "") -> str:
    title_e = html.escape(title)
    links = []
    for key, href, label in _NAV:
        cls = ' class="active" aria-current="page"' if key == active else ""
        badge = (' <span id="nav-unseen" class="chip bad" style="display:none"></span>'
                  if key == "obavijesti" else "")
        links.append(f'<a href="{html.escape(href)}"{cls}>{html.escape(label)}{badge}</a>')
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
<div class="layout">
<aside class="sidebar" aria-label="Glavna navigacija">
<span class="brand">RAGSPINE</span>
<nav>{nav_links}</nav>
<span class="spacer"></span>
<button type="button" id="theme-toggle" class="theme-toggle" aria-label="Promijeni temu"
  onclick="toggleTheme()">&#9790;</button>
<a href="/logout" class="logout">Odjava</a>
</aside>
<main class="container">
{body_html}
</main>
</div>
<script>{_THEME_TOGGLE_JS}</script>
<script>{_NAV_BADGE_JS}</script>
</body>
</html>"""


_DASHBOARD_JS = """
function $(id) { return document.getElementById(id); }
function setNum(id, value) { $(id).textContent = String(value); }
function emptyMsg(container, text) {
  const p = document.createElement('p'); p.className = 'meta'; p.textContent = text;
  container.appendChild(p);
}

var MJ_NOM = ['Siječanj','Veljača','Ožujak','Travanj','Svibanj','Lipanj','Srpanj',
  'Kolovoz','Rujan','Listopad','Studeni','Prosinac'];
var MJ_GEN = ['siječnja','veljače','ožujka','travnja','svibnja','lipnja','srpnja',
  'kolovoza','rujna','listopada','studenoga','prosinca'];
var DANI = ['Nedjelja','Ponedjeljak','Utorak','Srijeda','Četvrtak','Petak','Subota'];

function dd_mm(iso) { return iso ? (iso.slice(8,10) + '.' + iso.slice(5,7) + '.') : ''; }

function dueChip(row) {
  const chip = document.createElement('span');
  chip.className = 'chip ' + (row.state || '');
  const d = row.days_left;
  chip.textContent = typeof d !== 'number' ? '' :
    d < 0 ? ('kasni ' + Math.abs(d) + ' d.') : d === 0 ? 'danas' : ('za ' + d + ' d.');
  return chip;
}

function renderCalendar(cal) {
  $('cal-month').textContent = MJ_NOM[cal.month - 1] + ' ' + cal.year + '.';
  const grid = $('cal-grid'); grid.textContent = '';
  const byDay = {};
  (cal.events || []).forEach(function (e) { (byDay[e.day] = byDay[e.day] || []).push(e); });
  // Monday-first offset; JS getDay() has Sunday=0
  const firstDow = (new Date(cal.year, cal.month - 1, 1).getDay() + 6) % 7;
  const days = new Date(cal.year, cal.month, 0).getDate();
  for (let i = 0; i < firstDow; i++) {
    const e = document.createElement('div'); e.className = 'cal-d empty'; grid.appendChild(e);
  }
  for (let d = 1; d <= days; d++) {
    const cell = document.createElement('div');
    cell.className = 'cal-d' + (d === cal.today ? ' today' : '');
    const dn = document.createElement('span'); dn.className = 'cal-dn'; dn.textContent = String(d);
    cell.appendChild(dn);
    (byDay[d] || []).forEach(function (e) {
      const ev = document.createElement('span');
      ev.className = 'cal-ev ' + (e.state || '');
      ev.textContent = e.label; ev.title = e.label;
      cell.appendChild(ev);
    });
    grid.appendChild(cell);
  }
}

function renderToday(rows) {
  const box = $('today-list'); box.textContent = '';
  const urgent = rows.filter(function (r) { return r.state === 'bad' || r.state === 'warn'; }).slice(0, 4);
  $('today-count').textContent = String(urgent.length);
  if (!urgent.length) { emptyMsg(box, 'Nema hitnih rokova danas.'); return; }
  urgent.forEach(function (r) {
    const row = document.createElement('div'); row.className = 'trow ' + (r.state || '');
    const left = document.createElement('div');
    const tt = document.createElement('div'); tt.className = 'tt'; tt.textContent = r.kind;
    const ts = document.createElement('div'); ts.className = 'ts'; ts.textContent = 'rok ' + dd_mm(r.due);
    left.appendChild(tt); left.appendChild(ts);
    row.appendChild(left); row.appendChild(dueChip(r));
    box.appendChild(row);
  });
}

function renderUnsentByClient(groups, total) {
  const box = $('unsent-list'); box.textContent = '';
  const n = typeof total === 'number' ? total : groups.length;
  $('unsent-count').textContent = n + (n === 1 ? ' klijent' : ' klijenta');
  if (!groups.length) { emptyMsg(box, 'Sve obveze poslane \\uD83C\\uDF89'); return; }
  groups.forEach(function (g) {
    const row = document.createElement('div'); row.className = 'urow';
    const a = document.createElement('a'); a.className = 'uname';
    a.href = '/ui/klijent/' + g.client_id; a.textContent = g.client;
    row.appendChild(a);
    const kinds = document.createElement('span'); kinds.className = 'ukinds';
    (g.kinds || []).forEach(function (k) {
      const chip = document.createElement('span');
      chip.className = 'chip ' + (k.state || 'warn'); chip.textContent = k.kind;
      kinds.appendChild(chip);
    });
    row.appendChild(kinds);
    box.appendChild(row);
  });
}

function renderNotifications(rows) {
  const box = $('notifications-list'); box.textContent = '';
  $('notif-count').textContent = String(rows.length);
  if (!rows.length) { emptyMsg(box, 'Nema novih obavijesti.'); return; }
  rows.forEach(function (r) {
    const row = document.createElement('div'); row.className = 'nrow';
    const chip = document.createElement('span'); chip.className = 'chip'; chip.textContent = r.kind;
    const body = document.createElement('span'); body.className = 'ntext'; body.textContent = r.body;
    row.appendChild(chip); row.appendChild(body);
    box.appendChild(row);
  });
}

function renderOrientation(orientation) {
  var box = $('orientation-list'); if (!box) return; box.textContent = '';
  var folders = (orientation && orientation.folders) || [];
  if (!folders.length) { emptyMsg(box, 'Spoji mapu u Postavke → Mrežne mape.'); return; }
  folders.forEach(function (f) {
    var row = document.createElement('div'); row.className = 'nrow';
    var chip = document.createElement('span'); chip.className = 'chip'; chip.textContent = f.role;
    var txt = document.createElement('span'); txt.className = 'ntext';
    var s = f.scan || {};
    txt.textContent = f.label + (s.n_subdirs != null
      ? ' — ' + s.n_subdirs + ' podmapa, ' + (s.n_docs || 0) + ' dok., ' + (s.n_pdf_no_text || 0) + ' bez OCR'
      : ' — nije još skenirano');
    row.appendChild(chip); row.appendChild(txt);
    var scanBtn = document.createElement('button'); scanBtn.className = 'btn btn-ghost';
    scanBtn.textContent = 'Skeniraj sad';
    scanBtn.addEventListener('click', function () {
      fetch('/folders/' + f.id + '/scan', {method:'POST', credentials:'same-origin'})
        .then(function(){ loadDashboard(); });
    });
    row.appendChild(scanBtn);
    if ((f.scan || {}).n_pdf_no_text > 0) {
      var ocrBtn = document.createElement('button'); ocrBtn.className = 'btn';
      ocrBtn.textContent = 'OCR-aj mapu (' + f.scan.n_pdf_no_text + ')';
      ocrBtn.addEventListener('click', function () {
        ocrBtn.disabled = true; ocrBtn.textContent = 'OCR u tijeku…';
        fetch('/folders/' + f.id + '/ocr', {method:'POST', credentials:'same-origin'})
          .then(function(){ loadDashboard(); });
      });
      row.appendChild(ocrBtn);
    }
    box.appendChild(row);
  });
}

async function loadDashboard() {
  try {
    const res = await fetch('/dashboard.json', { credentials: 'same-origin' });
    if (!res.ok) throw new Error('status ' + res.status);
    const data = await res.json();
    renderOrientation(data.orientation);
    const cal = data.calendar || { year: 2026, month: 1, today: 1, events: [] };

    const dt = new Date(cal.year, cal.month - 1, cal.today);
    $('dash-date').textContent = DANI[dt.getDay()] + ' · ' + cal.today + '. ' +
      MJ_GEN[cal.month - 1] + ' ' + cal.year + '.';
    var unsentTotal = typeof data.unsent_total === 'number' ? data.unsent_total : data.unsent_obligations.length;
    var expiringTotal = typeof data.expiring_total === 'number' ? data.expiring_total : data.expiring.length;
    $('dash-sub').textContent = data.stats.deadlines_this_week + ' roka ovaj tjedan · ' +
      unsentTotal + ' neposlanih obveza · ' + expiringTotal + ' dok. uskoro isteče';

    setNum('stat-clients', data.stats.active_clients);
    setNum('stat-unsent', unsentTotal);
    setNum('stat-deadlines', data.stats.deadlines_this_week);
    setNum('stat-notifications', data.stats.unseen_notifications);
    $('stat-unsent').classList.toggle('bad', unsentTotal > 0);

    renderCalendar(cal);
    renderToday(data.deadlines);
    renderUnsentByClient(data.unsent_by_client || [], data.unsent_clients_total);
    renderNotifications(data.notifications);
  } catch (err) {
    const banner = $('dashboard-error');
    banner.textContent = 'Greška pri učitavanju nadzorne ploče. Osvježite stranicu.';
    banner.style.display = 'block';
  }
}

loadDashboard();
"""


def dashboard_page() -> str:
    body = f"""<div class="dash-head">
  <div class="eyebrow" id="dash-date">—</div>
  <h1>Ured danas</h1>
  <div class="sub" id="dash-sub">Učitavanje…</div>
</div>
<div id="dashboard-error" class="chip bad" style="display:none;margin-bottom:1rem"></div>
<div class="panel" style="margin-bottom:1rem">
  <div class="panel-head"><span class="panel-title">Orijentacija — spojene mape</span></div>
  <div id="orientation-list"><p class="meta">Učitavanje…</p></div>
</div>
<div class="dash-wrap">
  <div class="panel">
    <div class="cal-head">
      <div class="cal-month" id="cal-month">—</div>
      <div class="cal-legend">
        <span><i style="background:var(--bad)"></i>kasni</span>
        <span><i style="background:var(--warn)"></i>ovaj tjedan</span>
        <span><i style="background:var(--ok)"></i>predstoji</span>
      </div>
    </div>
    <div class="dow"><span>Pon</span><span>Uto</span><span>Sri</span><span>Čet</span><span>Pet</span><span>Sub</span><span>Ned</span></div>
    <div class="cal-grid" id="cal-grid"><p class="meta">Učitavanje…</p></div>
  </div>
  <div class="rail">
    <div class="panel">
      <div class="panel-head"><span class="panel-title">Što danas moram</span><span class="panel-count bad" id="today-count"></span></div>
      <div id="today-list"><p class="meta">Učitavanje…</p></div>
    </div>
    <div class="kpis">
      <div class="kpi"><div class="kn" id="stat-clients">–</div><div class="kl">Aktivni klijenti</div></div>
      <div class="kpi"><div class="kn" id="stat-unsent">–</div><div class="kl">Neposlane obveze</div></div>
      <div class="kpi"><div class="kn" id="stat-deadlines">–</div><div class="kl">Rokovi ovaj tjedan</div></div>
      <div class="kpi"><div class="kn" id="stat-notifications">–</div><div class="kl">Nove obavijesti</div></div>
    </div>
  </div>
</div>
<div class="dash-board">
  <div class="panel">
    <div class="panel-head"><span class="panel-title">Neposlane obveze · po klijentu</span><span class="panel-count" id="unsent-count"></span></div>
    <div id="unsent-list"><p class="meta">Učitavanje…</p></div>
  </div>
  <div class="panel">
    <div class="panel-head"><span class="panel-title">Nove obavijesti</span><span class="panel-count" id="notif-count"></span></div>
    <div id="notifications-list"><p class="meta">Učitavanje…</p></div>
  </div>
</div>
<script>{_DASHBOARD_JS}</script>"""
    return page_shell("Nadzorna ploča", body, active="home")


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
    const chip = document.createElement('a');
    chip.className = 'chip';
    chip.href = '/ui/klijent/' + data.client.id;
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


def postavke_page() -> str:
    cards = [
        ("Računalo i modeli", "/ui/racunalo", "Stanje računala, preduvjeti za pokretanje, i koji lokalni modeli stanu (po kvantizaciji/kompresiji)."),
        ("Sigurnosne kopije", "/ui/backup", "Napravi i preuzmi kopiju baze; automatski dnevni backup; upute za vraćanje."),
        ("Model (LLM)", "/ui/model", "Odaberi preko čega RAGSPINE radi (Claude / ChatGPT / lokalni). Mozak vs gorivo."),
        ("Mrežne mape", "/ui/mape", "Poveži NAS / Windows mape i dodijeli im uloge (propisi, klijenti…)."),
        ("Arhitektura mapa", "/ui/arhitektura", "Predložena struktura (obavezne + po klijentu) — pregled pa kreiranje."),
        ("Uređaji", "/ui/uredjaji", "Skeneri i pisači u mreži — pronađi, dodaj, biraj pri skeniranju/printanju."),
        ("Vrste obveza", "/ui/obveze-tipovi", "Dodaj i uredi vrste obveza (PDV, JOPPD, najam…)."),
        ("Vrste dokumenata", "/ui/dok-tipovi", "Dokumenti s poljima za automatsko čitanje (osobna, putovnica…) i istekom."),
        ("Organizacija", "/ui/org", "Članovi i uloge (viewer/member/admin/owner)."),
        ("Radnici", "/ui/radnici", "Ograniči kojem radniku su vidljivi koji klijenti."),
        ("Wiki", "/ui/wiki", "Interno znanje ureda koje LLM održava; zaključavanje stranica."),
        ("Skills", "/ui/skills", "Interni postupci s okidačima — chat ih automatski nudi."),
    ]
    tiles = "".join(
        f'<a class="card" href="{html.escape(href)}"><h2>{html.escape(t)}</h2>'
        f'<p>{html.escape(d)}</p></a>'
        for t, href, d in cards
    )
    body = f"""<h1>Postavke</h1>
<p class="meta">Konfiguracija ureda — odvojeno od svakodnevnog rada.</p>
<div class="grid">{tiles}</div>"""
    return page_shell("Postavke", body, active="postavke")


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


_KLIJENTI_JS = """
function $(id) { return document.getElementById(id); }

let ALL_CLIENTS = [];

function renderRows(rows) {
  const tbody = $('clients-tbody');
  tbody.textContent = '';
  if (!rows.length) {
    const tr = document.createElement('tr');
    const td = document.createElement('td');
    td.colSpan = 4;
    td.className = 'meta';
    td.textContent = ALL_CLIENTS.length
      ? 'Nema klijenata koji odgovaraju pretrazi.'
      : 'Još nema klijenata. Dodaj prvog.';
    tr.appendChild(td);
    tbody.appendChild(tr);
    return;
  }
  rows.forEach(function (r) {
    const tr = document.createElement('tr');

    const tdName = document.createElement('td');
    const a = document.createElement('a');
    a.href = '/ui/klijent/' + r.id;
    a.textContent = r.name;
    tdName.appendChild(a);
    tr.appendChild(tdName);

    const tdOib = document.createElement('td');
    tdOib.style.fontFamily = 'var(--font-mono)';
    tdOib.textContent = r.oib || '–';
    tr.appendChild(tdOib);

    const tdPdv = document.createElement('td');
    const chip = document.createElement('span');
    chip.className = 'chip';
    chip.textContent = r.pdv_status || '–';
    tdPdv.appendChild(chip);
    tr.appendChild(tdPdv);

    const tdReg = document.createElement('td');
    tdReg.textContent = REGIME_LABEL[r.regime || ''] || '–';
    tr.appendChild(tdReg);

    const tdInd = document.createElement('td');
    tdInd.textContent = r.industry || '–';
    tr.appendChild(tdInd);

    tbody.appendChild(tr);
  });
}

var REGIME_LABEL = { '': '–', dobit: 'Dobit', dohodak: 'Dohodak', pausal: 'Paušal' };

async function loadClients() {
  try {
    const res = await fetch('/clients', { credentials: 'same-origin' });
    if (!res.ok) throw new Error('status ' + res.status);
    ALL_CLIENTS = await res.json();
    renderRows(ALL_CLIENTS);
  } catch (err) {
    const tbody = $('clients-tbody');
    tbody.textContent = '';
    const tr = document.createElement('tr');
    const td = document.createElement('td');
    td.colSpan = 5;
    td.textContent = 'Greška pri učitavanju klijenata. Osvježite stranicu.';
    tr.appendChild(td);
    tbody.appendChild(tr);
  }
}

$('search').addEventListener('input', function () {
  const term = $('search').value.trim().toLowerCase();
  if (!term) { renderRows(ALL_CLIENTS); return; }
  renderRows(ALL_CLIENTS.filter(function (r) {
    return (r.name || '').toLowerCase().includes(term) || (r.oib || '').includes(term);
  }));
});

$('toggle-add').addEventListener('click', function () {
  const f = $('add-form');
  f.style.display = f.style.display === 'none' ? 'flex' : 'none';
});

$('add-form').addEventListener('submit', async function (e) {
  e.preventDefault();
  $('add-error').style.display = 'none';
  const name = $('f-name').value.trim();
  if (!name) return;
  const body = {
    name: name,
    oib: $('f-oib').value.trim(),
    email: $('f-email').value.trim(),
    phone: $('f-phone').value.trim(),
    industry: $('f-industry').value.trim(),
    pdv_status: $('f-pdv').value,
    regime: $('f-regime').value,
    pausal_eur: parseFloat($('f-pausal').value) || 0,
  };
  try {
    const res = await fetch('/clients', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      let detail = res.status;
      try { detail = (await res.json()).detail || detail; } catch (e2) {}
      $('add-error').textContent = 'Greška: ' + detail;
      $('add-error').style.display = 'block';
      return;
    }
    const data = await res.json();
    location.href = '/ui/klijent/' + data.id;
  } catch (err) {
    $('add-error').textContent = 'Greška u komunikaciji sa serverom.';
    $('add-error').style.display = 'block';
  }
});

loadClients();
"""


def klijenti_page() -> str:
    body = f"""<h1>Klijenti</h1>
<p class="meta">Svi klijenti ureda — otvori karton za pun pregled.</p>
<div style="display:flex;gap:.5rem;align-items:center;margin:1rem 0;flex-wrap:wrap">
  <input type="text" id="search" placeholder="Pretraži po imenu ili OIB-u...">
  <a class="btn" href="/ui/novi-klijent">Dodaj novog klijenta</a><button type="button" class="btn btn-ghost" id="toggle-add">Brzi unos</button>
</div>
<form id="add-form" class="stack" style="display:none;margin-bottom:1.5rem">
  <label for="f-name">Naziv</label>
  <input type="text" id="f-name" required>
  <label for="f-oib">OIB</label>
  <input type="text" id="f-oib" placeholder="11 znamenki">
  <label for="f-email">Email</label>
  <input type="email" id="f-email">
  <label for="f-phone">Telefon</label>
  <input type="text" id="f-phone">
  <label for="f-industry">Djelatnost</label>
  <input type="text" id="f-industry">
  <label for="f-pdv">PDV status</label>
  <select id="f-pdv">
    <option value="">-</option>
    <option value="u sustavu PDV-a">U sustavu PDV-a</option>
    <option value="nije u sustavu PDV-a">Nije u sustavu PDV-a</option>
  </select>
  <label for="f-regime">Obračun (porezni sustav)</label>
  <select id="f-regime">
    <option value="">-</option>
    <option value="dobit">Porez na dobit (d.o.o. / j.d.o.o. / obrt na dobiti)</option>
    <option value="dohodak">Dohodak (obrt, knjige)</option>
    <option value="pausal">Paušalni obrt</option>
  </select>
  <label for="f-pausal">Paušal (EUR/mj)</label>
  <input type="number" id="f-pausal" step="0.01" min="0">
  <button type="submit" class="btn">Spremi klijenta</button>
</form>
<div id="add-error" class="chip bad" style="display:none"></div>
<table class="ledger">
<thead><tr><th>Naziv</th><th>OIB</th><th>PDV status</th><th>Obračun</th><th>Djelatnost</th></tr></thead>
<tbody id="clients-tbody"><tr><td colspan="5" class="meta">Učitavanje…</td></tr></tbody>
</table>
<script>{_KLIJENTI_JS}</script>"""
    return page_shell("Klijenti", body, active="klijenti")


_KARTON_JS = """
function $(id) { return document.getElementById(id); }

function emptyMsg(container, text) {
  container.textContent = '';
  const p = document.createElement('p');
  p.className = 'meta';
  p.textContent = text;
  container.appendChild(p);
}

function euro(n) {
  const v = typeof n === 'number' ? n : parseFloat(n) || 0;
  return v.toFixed(2) + ' €';
}

function renderMissing(container, missing) {
  container.textContent = '';
  missing.forEach(function (m) {
    const chip = document.createElement('span');
    chip.className = 'chip warn';
    chip.style.marginRight = '.3rem';
    chip.textContent = m;
    container.appendChild(chip);
  });
}

function renderNotes(container, rows) {
  container.textContent = '';
  if (!rows.length) { emptyMsg(container, 'Još nema bilješki. Dodaj prvu.'); return; }
  rows.forEach(function (n) {
    const row = document.createElement('div');
    row.className = 'oblig-row';
    const body = document.createElement('span');
    body.textContent = n.body;
    row.appendChild(body);
    const meta = document.createElement('span');
    meta.className = 'due';
    meta.textContent = (n.author || '') + ' · ' + (n.created_at || '');
    row.appendChild(meta);
    container.appendChild(row);
  });
}

function renderSops(container, rows) {
  container.textContent = '';
  if (!rows.length) { emptyMsg(container, 'Nema odobrenih uputa za ovog klijenta.'); return; }
  rows.forEach(function (s) {
    const row = document.createElement('div');
    row.className = 'oblig-row';
    const a = document.createElement('a');
    a.href = '/ui/upute';
    a.textContent = s.title;
    row.appendChild(a);
    container.appendChild(row);
  });
}

function renderObligations(container, rows) {
  container.textContent = '';
  if (!rows.length) { emptyMsg(container, 'Nema obveza za ovaj period.'); return; }
  rows.forEach(function (o) {
    const row = document.createElement('div');
    row.className = 'oblig-row ' + (o.sent ? 'ok' : 'bad');
    const kind = document.createElement('span');
    kind.textContent = o.kind;
    row.appendChild(kind);
    const chip = document.createElement('span');
    chip.className = 'chip ' + (o.sent ? 'ok' : 'bad');
    chip.textContent = o.sent ? 'predano' : 'nije poslano';
    row.appendChild(chip);
    container.appendChild(row);
  });
}

function renderExpiry(container, rows) {
  container.textContent = '';
  if (!rows.length) { emptyMsg(container, 'Nema praćenih isteka dokumenata.'); return; }
  rows.forEach(function (e) {
    const row = document.createElement('div');
    row.className = 'oblig-row ' + (e.state || '');
    const label = document.createElement('span');
    label.textContent = e.label + ' (' + e.kind + ')';
    row.appendChild(label);
    const due = document.createElement('span');
    due.className = 'due';
    due.textContent = e.expires;
    row.appendChild(due);
    container.appendChild(row);
  });
}

function renderEracuni(container, data) {
  container.textContent = '';
  const summary = document.createElement('p');
  summary.className = 'meta';
  summary.textContent = data.count + ' e-računa ukupno.';
  container.appendChild(summary);
  if (!data.recent.length) { emptyMsg(container, 'Nema e-računa.'); return; }
  data.recent.forEach(function (r) {
    const row = document.createElement('div');
    row.className = 'oblig-row';
    const desc = document.createElement('span');
    desc.textContent = (r.issued || '') + ' — ' + euro(r.total || 0);
    row.appendChild(desc);
    container.appendChild(row);
  });
}

function renderDocuments(container, rows) {
  container.textContent = '';
  if (!rows.length) { emptyMsg(container, 'Još nema dokumenata. Učitaj prvi.'); return; }
  rows.forEach(function (d) {
    const row = document.createElement('div');
    row.className = 'oblig-row';
    const name = document.createElement('span');
    name.textContent = d.filename;
    row.appendChild(name);
    const chip = document.createElement('span');
    chip.className = 'chip ' + (d.ingested ? 'ok' : '');
    chip.textContent = d.ingested ? 'obrađeno' : 'nije obrađeno';
    row.appendChild(chip);
    container.appendChild(row);
  });
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

async function loadKarton() {
  try {
    const res = await fetch(KARTON_URL, { credentials: 'same-origin' });
    if (!res.ok) throw new Error('status ' + res.status);
    const data = await res.json();

    $('k-name').textContent = data.client.name;
    $('k-oib').textContent = data.client.oib || '–';
    $('k-pdv').textContent = data.client.pdv_status || '–';
    $('k-industry').textContent = data.client.industry || '–';
    $('k-owner').textContent = data.client.owner || '–';
    $('k-pausal').textContent = euro(data.client.pausal_eur || 0);

    $('k-score').textContent = (data.checklist.score || 0) + '%';
    renderMissing($('k-missing'), data.checklist.missing || []);

    renderNotes($('k-notes'), data.notes || []);
    renderSops($('k-sops'), data.sops || []);
    renderObligations($('k-obligations'), data.obligations || []);
    renderExpiry($('k-expiry'), data.expiry || []);

    $('k-cjenik-ukupno').textContent = euro(data.cjenik.ukupno || 0);
    $('k-cjenik-preporuka').textContent =
      (data.cjenik.usporedba && data.cjenik.usporedba.preporuka) || '';

    renderEracuni($('k-eracuni'), data.eracuni || { count: 0, recent: [] });
    renderDocuments($('k-documents'), data.documents || []);
  } catch (err) {
    const banner = $('karton-error');
    banner.textContent = 'Greška pri učitavanju kartona. Osvježite stranicu.';
    banner.style.display = 'block';
  }
}

$('note-form').addEventListener('submit', async function (e) {
  e.preventDefault();
  const body = $('note-body').value.trim();
  if (!body) return;
  const res = await fetch('/notes', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ client_id: CLIENT_ID, body: body }),
  });
  if (!res.ok) { alert('Greška: ' + res.status); return; }
  $('note-body').value = '';
  loadKarton();
});

$('doc-upload').addEventListener('click', async function () {
  const input = $('doc-file');
  const file = input.files[0];
  if (!file) return;
  const data_base64 = await readAsBase64(file);
  const res = await fetch('/clients/' + CLIENT_ID + '/document', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ filename: file.name, data_base64: data_base64 }),
  });
  if (!res.ok) { alert('Greška: ' + res.status); return; }
  input.value = '';
  loadKarton();
});

loadKarton();
"""


def klijent_page(client_id: int) -> str:
    body = f"""<div id="karton-error" class="chip bad" style="display:none;margin-bottom:1rem"></div>
<div class="card">
  <h1 id="k-name">Učitavanje…</h1>
  <div class="meta" style="display:flex;gap:.75rem;flex-wrap:wrap;margin-top:.3rem">
    <span id="k-oib" style="font-family:var(--font-mono)"></span>
    <span id="k-pdv" class="chip"></span>
    <span id="k-industry"></span>
    <span id="k-owner"></span>
    <span id="k-pausal" style="font-family:var(--font-mono)"></span>
  </div>
</div>
<div class="grid">
  <div class="card">
    <h2>Kompletnost</h2>
    <span class="tile-num" id="k-score">–</span>
    <div id="k-missing" style="margin-top:.5rem"></div>
  </div>
  <div class="card">
    <h2>Bilješke</h2>
    <div id="k-notes"><p class="meta">Učitavanje…</p></div>
    <form id="note-form" class="stack" style="margin-top:.75rem">
      <input type="text" id="note-body" placeholder="Nova bilješka...">
      <button type="submit" class="btn">Dodaj bilješku</button>
    </form>
  </div>
  <div class="card">
    <h2>Upute (SOP-ovi)</h2>
    <div id="k-sops"><p class="meta">Učitavanje…</p></div>
  </div>
  <div class="card">
    <h2>Obveze</h2>
    <div id="k-obligations"><p class="meta">Učitavanje…</p></div>
  </div>
  <div class="card">
    <h2>Obveze — postavke</h2>
    <label for="oc-regime" style="display:block">Porezni sustav</label>
    <select id="oc-regime">
      <option value="">— (nepoznato)</option>
      <option value="dobit">Porez na dobit (d.o.o.)</option>
      <option value="dohodak">Dohodaš (obrt, knjige) → DOH</option>
      <option value="pausal">Paušalni obrt → PO-SD</option>
    </select>
    <label style="display:flex;gap:.5rem;align-items:center;margin-top:.5rem"><input type="checkbox" id="oc-employees"> Ima zaposlene (za JOPPD)</label>
    <label for="oc-pdv-freq" style="margin-top:.5rem;display:block">PDV frekvencija</label>
    <select id="oc-pdv-freq"><option value="monthly">Mjesečno</option><option value="quarterly">Tromjesečno</option></select>
    <div id="oc-manual" style="margin-top:.5rem"></div>
    <button type="button" class="btn" id="oc-save" style="margin-top:.75rem">Spremi postavke</button>
    <span id="oc-msg" class="meta" style="margin-left:.5rem"></span>
  </div>
  <div class="card">
    <h2>Istek dokumenata</h2>
    <div id="k-expiry"><p class="meta">Učitavanje…</p></div>
  </div>
  <div class="card">
    <h2>Cjenik</h2>
    <span class="tile-num" id="k-cjenik-ukupno">–</span>
    <p class="meta" id="k-cjenik-preporuka"></p>
  </div>
  <div class="card">
    <h2>E-računi</h2>
    <div id="k-eracuni"><p class="meta">Učitavanje…</p></div>
  </div>
  <div class="card">
    <h2>Dokumenti</h2>
    <div id="k-documents"><p class="meta">Učitavanje…</p></div>
    <div style="margin-top:.75rem;display:flex;gap:.5rem;align-items:center">
      <input type="file" id="doc-file">
      <button type="button" class="btn" id="doc-upload">Učitaj dokument</button>
    </div>
  </div>
</div>
<script>
const CLIENT_ID = {int(client_id)};
const KARTON_URL = '/clients/{int(client_id)}/karton.json';
</script>
<script>{_KARTON_JS}</script>
<script>{_OBVEZE_SETTINGS_JS}</script>"""
    return page_shell("Klijent", body, active="klijenti")


_OBVEZE_SETTINGS_JS = """
(function(){
  function $(id){ return document.getElementById(id); }
  var URL = '/clients/' + CLIENT_ID + '/obveze-postavke';
  var manual = [];
  async function load(){
    var res = await fetch(URL, {credentials:'same-origin'});
    if(!res.ok) return;
    var d = await res.json();
    $('oc-employees').checked = !!d.has_employees;
    $('oc-pdv-freq').value = d.pdv_freq || 'monthly';
    $('oc-regime').value = d.regime || '';
    manual = d.available_manual || [];
    var box = $('oc-manual'); box.textContent = '';
    if(!manual.length){ return; }
    var lab = document.createElement('div'); lab.className='meta'; lab.textContent='Ručne obveze:'; box.appendChild(lab);
    var have = d.manual_kinds || [];
    manual.forEach(function(t){
      var l = document.createElement('label'); l.style.display='flex'; l.style.gap='.5rem'; l.style.alignItems='center';
      var cb = document.createElement('input'); cb.type='checkbox'; cb.value=t.kind; cb.className='oc-manual-cb';
      cb.checked = have.indexOf(t.kind) !== -1;
      var span = document.createElement('span'); span.textContent = t.label + ' (' + t.kind + ')';
      l.appendChild(cb); l.appendChild(span); box.appendChild(l);
    });
  }
  $('oc-save').addEventListener('click', async function(){
    var kinds = [].slice.call(document.querySelectorAll('.oc-manual-cb'))
      .filter(function(c){ return c.checked; }).map(function(c){ return c.value; });
    var res = await fetch(URL, {method:'POST', credentials:'same-origin',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({has_employees: $('oc-employees').checked?1:0,
        pdv_freq: $('oc-pdv-freq').value, regime: $('oc-regime').value, manual_kinds: kinds})});
    var msg = $('oc-msg');
    if(res.ok){ msg.textContent='Spremljeno.'; setTimeout(function(){msg.textContent='';}, 2000); }
    else { msg.textContent='Greška.'; }
  });
  load();
})();
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


_OBAVIJESTI_JS = """
function $(id) { return document.getElementById(id); }

async function markSeen(id) {
  try {
    await fetch('/notifications/' + id + '/seen', { method: 'POST', credentials: 'same-origin' });
  } catch (e) {}
  loadNotifications();
}

function renderNotif(container, rows) {
  container.textContent = '';
  if (!rows.length) {
    const p = document.createElement('p');
    p.className = 'meta';
    p.textContent = 'Nema obavijesti.';
    container.appendChild(p);
    return;
  }
  rows.forEach(function (r) {
    const row = document.createElement('div');
    row.className = 'oblig-row' + (r.seen ? '' : ' warn');
    row.style.cursor = r.seen ? 'default' : 'pointer';

    const chip = document.createElement('span');
    chip.className = 'chip';
    chip.textContent = r.kind;
    row.appendChild(chip);

    const body = document.createElement('span');
    body.textContent = r.body;
    if (!r.seen) body.style.fontWeight = '700';
    row.appendChild(body);

    const at = document.createElement('span');
    at.className = 'due';
    at.textContent = r.at || '';
    row.appendChild(at);

    if (!r.seen) {
      row.addEventListener('click', function () { markSeen(r.id); });
    }
    container.appendChild(row);
  });
}

async function loadNotifications() {
  try {
    const res = await fetch('/notifications.json', { credentials: 'same-origin' });
    if (!res.ok) throw new Error('status ' + res.status);
    const rows = await res.json();
    renderNotif($('notif-list'), rows);
  } catch (err) {
    $('notif-error').textContent = 'Greška pri učitavanju obavijesti. Osvježite stranicu.';
    $('notif-error').style.display = 'block';
  }
}

loadNotifications();
"""


def obavijesti_page() -> str:
    body = f"""<h1>Obavijesti</h1>
<p class="meta">Sve obavijesti — klikni nepročitanu da je označiš pročitanom.</p>
<div id="notif-error" class="chip bad" style="display:none;margin-bottom:1rem"></div>
<div id="notif-list"><p class="meta">Učitavanje…</p></div>
<script>{_OBAVIJESTI_JS}</script>"""
    return page_shell("Obavijesti", body, active="obavijesti")


_DOKUMENTI_JS = """
function $(id) { return document.getElementById(id); }

function toggleExtra() {
  const t = $('doc-type').value;
  $('extra-ponuda').style.display = t === 'ponuda' ? 'block' : 'none';
  $('extra-opomena').style.display = t === 'opomena' ? 'block' : 'none';
}

function addStavkaRow() {
  const row = document.createElement('div');
  row.style.display = 'flex';
  row.style.gap = '.5rem';
  row.style.marginBottom = '.4rem';
  const naziv = document.createElement('input');
  naziv.type = 'text';
  naziv.placeholder = 'Naziv';
  naziv.className = 'stavka-naziv';
  const iznos = document.createElement('input');
  iznos.type = 'number';
  iznos.step = '0.01';
  iznos.placeholder = 'Iznos (EUR)';
  iznos.className = 'stavka-iznos';
  row.appendChild(naziv);
  row.appendChild(iznos);
  $('stavke-rows').appendChild(row);
}

function collectExtra() {
  const t = $('doc-type').value;
  if (t === 'ponuda') {
    const stavke = [];
    $('stavke-rows').querySelectorAll('div').forEach(function (row) {
      const naziv = row.querySelector('.stavka-naziv').value.trim();
      const iznos = parseFloat(row.querySelector('.stavka-iznos').value);
      if (naziv && !isNaN(iznos)) stavke.push({ naziv: naziv, iznos: iznos });
    });
    return { stavke: stavke };
  }
  if (t === 'opomena') {
    return { iznos_duga: parseFloat($('iznos-duga').value) || 0, rok: $('rok').value };
  }
  return {};
}

async function loadTemplates() {
  const res = await fetch('/doc/templates', { credentials: 'same-origin' });
  const types = await res.json();
  const sel = $('doc-type');
  sel.textContent = '';
  types.forEach(function (t) {
    const opt = document.createElement('option');
    opt.value = t;
    opt.textContent = t;
    sel.appendChild(opt);
  });
  toggleExtra();
}

async function loadClients() {
  const res = await fetch('/clients', { credentials: 'same-origin' });
  const rows = await res.json();
  const sel = $('doc-client');
  sel.textContent = '';
  rows.forEach(function (r) {
    const opt = document.createElement('option');
    opt.value = r.id;
    opt.textContent = r.name;
    sel.appendChild(opt);
  });
}

$('doc-type').addEventListener('change', toggleExtra);
$('add-stavka').addEventListener('click', addStavkaRow);

$('doc-form').addEventListener('submit', async function (e) {
  e.preventDefault();
  $('doc-warning').style.display = 'none';
  $('doc-output').textContent = '';
  const clientId = parseInt($('doc-client').value, 10);
  if (!clientId) { $('doc-output').textContent = 'Odaberi klijenta.'; return; }
  const body = { doc_type: $('doc-type').value, client_id: clientId, extra: collectExtra() };
  try {
    const res = await fetch('/doc/generate', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      let detail = res.status;
      try { detail = (await res.json()).detail || detail; } catch (e2) {}
      $('doc-output').textContent = 'Greška: ' + detail;
      return;
    }
    const data = await res.json();
    $('doc-output').textContent = data.text;
    if (data.warning) {
      const missing = (data.gate && Array.isArray(data.gate.missing)) ? data.gate.missing : [];
      $('doc-warning').textContent = missing.length
        ? ('Upozorenje: brojke nedostaju u dokumentu. Nedostaju: ' + missing.join(', '))
        : 'Upozorenje: brojke nedostaju u dokumentu.';
      $('doc-warning').style.display = 'block';
    }
  } catch (err) {
    $('doc-output').textContent = 'Greška u komunikaciji sa serverom.';
  }
});

loadTemplates();
loadClients();
addStavkaRow();
"""


def dokumenti_page() -> str:
    body = f"""<h1>Generator dokumenata</h1>
<p class="meta">Ponuda, dopis ili opomena — iz predloška, s provjerom da brojke nisu izmišljene.</p>
<form id="doc-form" class="stack">
  <label for="doc-type">Vrsta dokumenta</label>
  <select id="doc-type"></select>
  <label for="doc-client">Klijent</label>
  <select id="doc-client"></select>
  <div id="extra-ponuda" style="display:none">
    <label>Stavke (naziv + iznos)</label>
    <div id="stavke-rows"></div>
    <button type="button" class="btn btn-ghost" id="add-stavka">Dodaj stavku</button>
  </div>
  <div id="extra-opomena" style="display:none">
    <label for="iznos-duga">Iznos duga (EUR)</label>
    <input type="number" id="iznos-duga" step="0.01" min="0">
    <label for="rok">Rok plaćanja</label>
    <input type="date" id="rok">
  </div>
  <button type="submit" class="btn">Generiraj</button>
</form>
<div id="doc-warning" class="chip bad" style="display:none;margin-top:1rem"></div>
<pre id="doc-output" class="card" style="white-space:pre-wrap;margin-top:1rem;font-family:var(--font-mono)"></pre>
<script>{_DOKUMENTI_JS}</script>"""
    return page_shell("Dokumenti", body, active="dokumenti")
