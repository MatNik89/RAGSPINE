"""HTML za /ui/racunalo — stanje računala, preduvjeti za pokretanje ATLAS,
i kompresija-svjestan izbor lokalnog modela (koji LLM stane, na kojoj
kvantizaciji). XSS-safe (createElement/textContent), bez vanjskih resursa."""
from atlas.web.templates_ui import page_shell

_PILL = {
    "Good": ("\\ud83d\\udfe2", "ok"),       # 🟢 Green
    "Marginal": ("\\ud83d\\udfe1", "warn"), # 🟡 Yellow
    "Too Tight": ("\\ud83d\\udd34", "bad"), # 🔴 Red
}
_REQ = {"ok": "\\u2713", "warn": "\\u26a0", "fail": "\\u2715"}

_JS = """
function $(id){ return document.getElementById(id); }

function cell(parent, text, cls){
  var td = document.createElement('td');
  td.textContent = text;
  if(cls) td.className = cls;
  parent.appendChild(td);
  return td;
}
var PILL = __PILL__;
var REQ = __REQ__;

async function load(){
  var res = await fetch('/preflight', {credentials:'same-origin'});
  if(!res.ok){ $('err').textContent = 'Ne mogu dohvatiti stanje.'; return; }
  var d = await res.json();

  // --- stanje ---
  var st = d.state;
  var s = $('state'); s.textContent='';
  var pairs = [
    ['Operativni sustav', st.os],
    ['Python', st.python],
    ['CPU jezgre', String(st.cpu_cores)],
    ['RAM ukupno', st.ram_total_gb + ' GB (slobodno ' + st.ram_free_gb + ' GB)'],
    ['Slobodan disk', st.disk_free_gb + ' GB'],
    ['GPU', st.gpu || 'nema'],
    ['VRAM', st.vram_gb ? (st.vram_gb + ' GB') : 'n/a']
  ];
  pairs.forEach(function(p){
    var tr=document.createElement('tr'); cell(tr,p[0],'k'); cell(tr,p[1]); s.appendChild(tr);
  });

  // --- preduvjeti ---
  var rb = $('reqs'); rb.textContent='';
  d.requirements.forEach(function(r){
    var tr=document.createElement('tr');
    cell(tr, REQ[r.status] || '', 'st ' + r.status);
    cell(tr, r.naziv);
    cell(tr, r.detalj);
    cell(tr, r.status === 'ok' ? '' : r.fix, 'fix');
    rb.appendChild(tr);
  });
  $('reqs-note').textContent = d.requirements_ok
    ? 'Sve obavezno zadovoljeno \\u2014 ATLAS mo\\u017ee raditi.'
    : 'Nedostaje ne\\u0161to obavezno (crveno) \\u2014 ATLAS ne\\u0107e ispravno raditi dok se ne rije\\u0161i.';

  // --- modeli (llmfit — hardver-osjetljiva preporuka) ---
  var mb = $('models'); mb.textContent='';
  if(d.models && d.models.length > 0) {
    d.models.forEach(function(m){
      var tr=document.createElement('tr');
      // Oznaka: Good/Marginal/Too Tight
      var info = PILL[m.fit_label] || ['?',''];
      var td_pill=document.createElement('td'); td_pill.className='st '+info[1];
      td_pill.textContent = info[0];
      tr.appendChild(td_pill);
      // Naziv modela (ollama_name)
      cell(tr, m.ollama_name);
      // Parametri
      cell(tr, m.params);
      // Kvantizacija + memorija
      var memory_text = '~' + m.memory_gb.toFixed(1) + ' GB';
      cell(tr, (m.best_quant || '—') + ' (' + memory_text + ')');
      // Brzina
      cell(tr, '~' + Math.round(m.tps) + ' tok/s');
      // Namjena
      cell(tr, m.use_case);
      mb.appendChild(tr);
    });
  } else {
    var tr=document.createElement('tr');
    var td=document.createElement('td'); td.colSpan=6; td.textContent='Nema dostupnih modela (llmfit nije dostupan).';
    tr.appendChild(td);
    mb.appendChild(tr);
  }
  var note = 'Tier hardvera: ' + (d.recommended_tier || '?') +
    ' \\u00b7 Ollama: ' + (d.ollama_installed ? 'instaliran' : 'nije instaliran') +
    ' \\u00b7 ve\\u0107 povu\\u010deno: ' + ((d.already_pulled||[]).join(', ') || 'ni\\u0161ta') +
    ' \\u00b7 Modeli: llmfit';
  $('models-note').textContent = note;
}
document.addEventListener('DOMContentLoaded', load);
"""


def preflight_page() -> str:
    js = (_JS
          .replace("__PILL__", "{" + ",".join(f'"{k}":["{v[0]}","{v[1]}"]' for k, v in _PILL.items()) + "}")
          .replace("__REQ__", "{" + ",".join(f'"{k}":"{v}"' for k, v in _REQ.items()) + "}"))
    body = """
<style>
  .k{ color:var(--muted); width:38%; }
  td.st{ text-align:center; font-weight:700; }
  .st.ok{ color:#16a34a; } .st.warn{ color:#d97706; } .st.fail{ color:#dc2626; }
  .fix{ color:var(--muted); font-size:.85em; }
  .pill{ display:inline-block; padding:2px 6px; border-radius:6px; font-size:.82em; }
  .pill.ok{ background:#dcfce7; color:#166534; } .pill.warn{ background:#fef9c3; color:#854d0e; }
  .pill.bad{ background:#fee2e2; color:#991b1b; }
  table{ width:100%; border-collapse:collapse; } td{ padding:4px 6px; vertical-align:top; }
</style>
<h1>Ra\\u010dunalo i modeli</h1>
<p id="err" style="color:#dc2626"></p>

<div class="card">
  <h2>Stanje ra\\u010dunala</h2>
  <table><tbody id="state"></tbody></table>
</div>

<div class="card">
  <h2>Preduvjeti za pokretanje</h2>
  <p id="reqs-note" class="muted"></p>
  <table><tbody id="reqs"></tbody></table>
</div>

<div class="card">
  <h2>Lokalni modeli \\u2014 rangirano po llmfit score-u</h2>
  <p class="muted">llmfit mjeri tvoje ra\\u010dunalo i za svaki model ra\\u010duna kvantizaciju
     koja stane, o\\u010dekivanu brzinu (tok/s) i ocjenu. Prvi red \\u2014 najbolji.</p>
  <table><tbody id="models"></tbody></table>
  <p id="models-note" class="muted" style="margin-top:8px"></p>
</div>
<script>__SCRIPT__</script>
"""
    return page_shell("Računalo i modeli", body.replace("__SCRIPT__", js), active="postavke")
