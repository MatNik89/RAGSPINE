"""HTML za /ui/racunalo — stanje računala, preduvjeti za pokretanje RAGSPINE,
i kompresija-svjestan izbor lokalnog modela (koji LLM stane, na kojoj
kvantizaciji). XSS-safe (createElement/textContent), bez vanjskih resursa."""
from ragspine.web.templates_ui import page_shell

_PILL = {
    "fits": ("\\u2713 stane", "ok"),
    "tight": ("\\u26a0 tijesno", "warn"),
    "too_big": ("\\u2715 preveliko", "bad"),
    "unknown": ("?", ""),
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
    ? 'Sve obavezno zadovoljeno \\u2014 RAGSPINE mo\\u017ee raditi.'
    : 'Nedostaje ne\\u0161to obavezno (crveno) \\u2014 RAGSPINE ne\\u0107e ispravno raditi dok se ne rije\\u0161i.';

  // --- modeli (kompresija-svjestan fit) ---
  var mb = $('models'); mb.textContent='';
  d.models.forEach(function(m){
    if(m.role !== 'chat') return;
    var tr=document.createElement('tr');
    cell(tr, m.name);
    cell(tr, m.params);
    // po kvantizaciji: pill
    m.quants.forEach(function(q){
      var info = PILL[q.pill] || ['?',''];
      var td=document.createElement('td');
      var b=document.createElement('span'); b.className='pill '+info[1];
      b.textContent = q.quant + ': ' + info[0] + ' (' + q.size_gb + ' GB)' + (q.gpu_ready ? ' \\u26a1' : '');
      td.appendChild(b); tr.appendChild(td);
    });
    var best=document.createElement('td');
    if(m.best_quant){ best.className='st ok'; best.textContent='preporuka: '+m.best_quant; }
    else if(m.tight_quant){ best.className='st warn'; best.textContent='tijesno: '+m.tight_quant; }
    else { best.className='st fail'; best.textContent='ne stane'; }
    tr.appendChild(best);
    mb.appendChild(tr);
  });
  var note = 'Tier hardvera: ' + (d.recommended_tier || '?') +
    ' \\u00b7 Ollama: ' + (d.ollama_installed ? 'instaliran' : 'nije instaliran') +
    ' \\u00b7 ve\\u0107 povu\\u010deno: ' + ((d.already_pulled||[]).join(', ') || 'ni\\u0161ta');
  if(d.llmfit) note += ' \\u00b7 llmfit: aktivan';
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
  <h2>Lokalni modeli \\u2014 koji stanu (po kvantizaciji)</h2>
  <p class="muted">Kompresija (kvantizacija) smanjuje model: ista pamet, manje memorije.
     \\u26a1 = mo\\u017ee na GPU (br\\u017ee). Preporuka = najkvalitetnija koja stane.</p>
  <table><tbody id="models"></tbody></table>
  <p id="models-note" class="muted" style="margin-top:8px"></p>
</div>
<script>__SCRIPT__</script>
"""
    return page_shell("Računalo i modeli", body.replace("__SCRIPT__", js), active="postavke")
