"""HTML za /ui/backup — sigurnosne kopije baze: napravi sad, popis, preuzmi.
XSS-safe (createElement/textContent). Restore je CLI (ragspine restore) dok je
server zaustavljen — živi restore uz otvorene konekcije nije siguran."""
from ragspine.web.templates_ui import page_shell

_JS = r"""
function $(id){ return document.getElementById(id); }
function fmtSize(b){ return b > 1048576 ? (b/1048576).toFixed(1)+' MB' : (b/1024).toFixed(0)+' KB'; }
function fmtTime(s){ var d=new Date(s*1000); return d.toLocaleString('hr-HR'); }

async function load(){
  var res = await fetch('/backup/list', {credentials:'same-origin'});
  if(!res.ok){ $('err').textContent='Ne mogu dohvatiti popis.'; return; }
  var rows = await res.json();
  var body = $('list'); body.textContent='';
  if(!rows.length){ var tr=document.createElement('tr'); var td=document.createElement('td');
    td.colSpan=3; td.textContent='Još nema kopija.'; tr.appendChild(td); body.appendChild(tr); return; }
  rows.forEach(function(r){
    var tr=document.createElement('tr');
    var t1=document.createElement('td'); t1.textContent=fmtTime(r.mtime); tr.appendChild(t1);
    var t2=document.createElement('td'); t2.textContent=fmtSize(r.size); tr.appendChild(t2);
    var t3=document.createElement('td');
    var a=document.createElement('a'); a.href='/backup/download/'+encodeURIComponent(r.name);
    a.textContent='Preuzmi'; a.className='btn btn-ghost'; t3.appendChild(a);
    tr.appendChild(t3); body.appendChild(tr);
  });
}

async function makeNow(){
  var b=$('mk'); b.disabled=true; b.textContent='Radim kopiju…';
  var res=await fetch('/backup', {method:'POST', credentials:'same-origin'});
  b.disabled=false; b.textContent='Napravi kopiju sad';
  var j=await res.json().catch(function(){return {};});
  $('mk-msg').textContent = res.ok ? ('✓ Kopija: '+j.name+' ('+fmtSize(j.size)+')') : ('Greška: '+(j.detail||''));
  load();
}

document.addEventListener('DOMContentLoaded', function(){
  $('mk').addEventListener('click', makeNow);
  load();
});
"""

_BODY = """
<style>
  table{ width:100%; border-collapse:collapse; } td,th{ padding:6px 8px; text-align:left; }
  th{ color:var(--muted); font-weight:600; border-bottom:1px solid #e5e7eb; }
</style>
<h1>Sigurnosne kopije</h1>
<p id="err" style="color:#dc2626"></p>

<div class="card">
  <p class="muted">Kopija je potpuni, konzistentan snimak baze (svi klijenti, dokumenti,
    obveze). Automatski se radi svaki dan; ovdje možeš napraviti kopiju ručno i preuzeti je.</p>
  <button class="btn primary" id="mk">Napravi kopiju sad</button>
  <span id="mk-msg" class="muted" style="margin-left:10px"></span>
</div>

<div class="card">
  <h2>Postojeće kopije</h2>
  <table>
    <thead><tr><th>Vrijeme</th><th>Veličina</th><th>Preuzmi</th></tr></thead>
    <tbody id="list"></tbody>
  </table>
</div>

<div class="card">
  <h2>Vraćanje (restore)</h2>
  <p class="muted">Vraćanje kopije radi se iz terminala <b>dok je server zaustavljen</b>
    (živo vraćanje uz otvorene veze nije sigurno):</p>
  <pre>ragspine restore &lt;putanja-do-kopije&gt;.db</pre>
  <p class="muted">Trenutna baza se prije vraćanja spremi kao <code>ragspine.db.prerestore</code>.</p>
</div>
<script>__SCRIPT__</script>
"""


def backup_page() -> str:
    return page_shell("Sigurnosne kopije", _BODY.replace("__SCRIPT__", _JS), active="postavke")
