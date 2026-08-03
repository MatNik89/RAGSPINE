"""HTML za /ui/model — odabir LLM providera (mozak vs gorivo). API ključ se
šalje ali nikad ne vraća (GET daje samo has_api_key). Reuse design-shell."""
from ragspine.web.templates_ui import page_shell

_MODEL_JS = """
function $(id){ return document.getElementById(id); }

async function loadModel(){
  var res = await fetch('/model', {credentials:'same-origin'});
  if(!res.ok) return;
  var d = await res.json();
  $('m-provider').value = d.provider || 'anthropic';
  $('m-model').value = d.model || '';
  $('m-base').value = d.base_url || '';
  $('m-embed').value = d.embed_model || '';
  $('m-ollama').value = d.ollama_url || '';
  $('m-key').placeholder = d.has_api_key ? '•••••••• (spremljeno — prazno zadržava)' : 'API ključ';
}

async function saveModel(){
  var payload = {
    provider: $('m-provider').value, model: $('m-model').value.trim(),
    base_url: $('m-base').value.trim(), api_key: $('m-key').value,
    embed_model: $('m-embed').value.trim(), ollama_url: $('m-ollama').value.trim(),
  };
  var res = await fetch('/model', {method:'POST', credentials:'same-origin',
    headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)});
  var msg = $('m-msg');
  if(res.ok){ $('m-key').value=''; msg.textContent='Spremljeno.'; msg.className='meta'; loadModel(); }
  else { var e=await res.json().catch(function(){return{};}); msg.textContent='Greška: '+(e.detail||res.status); }
}

async function testModel(){
  var msg = $('m-msg'); msg.textContent='Testiram vezu…'; msg.className='meta';
  var res = await fetch('/model/test', {method:'POST', credentials:'same-origin'});
  var d = await res.json();
  if(d.ok){ msg.textContent='✓ Veza radi (model: '+(d.model||'?')+')'; }
  else { msg.textContent='✗ Ne radi: '+(d.error||'nepoznato'); }
}

$('m-save').addEventListener('click', saveModel);
$('m-test').addEventListener('click', testModel);
loadModel();
"""


def model_page() -> str:
    body = f"""<h1>Model (LLM)</h1>
<p class="meta"><a href="/ui/postavke">← Postavke</a> · RAGSPINE je mozak; model je gorivo.
Prebaci providera bilo kad — pravila, memorija i pretraga ostaju iste.</p>
<div class="card" style="max-width:640px">
  <form class="stack" onsubmit="return false">
    <label for="m-provider">Provider</label>
    <select id="m-provider">
      <option value="anthropic">Anthropic (Claude)</option>
      <option value="openai">OpenAI-compat (ChatGPT i sl.)</option>
      <option value="ollama">Lokalni (Ollama)</option>
    </select>
    <label for="m-model">Model (naziv)</label>
    <input type="text" id="m-model" placeholder="npr. claude-… / gpt-… / llama3.1">
    <label for="m-base">Base URL <span class="meta">(opcionalno; za Ollama = adresa servera)</span></label>
    <input type="text" id="m-base" placeholder="npr. http://192.168.1.50:11434">
    <label for="m-key">API ključ <span class="meta">(za Claude/OpenAI; ne treba za Ollama)</span></label>
    <input type="password" id="m-key" autocomplete="off" placeholder="API ključ">
    <label for="m-embed">Embedding model <span class="meta">(za semantičku pretragu)</span></label>
    <input type="text" id="m-embed" placeholder="intfloat/multilingual-e5-large">
    <label for="m-ollama">Ollama URL <span class="meta">(ako se razlikuje od Base URL-a)</span></label>
    <input type="text" id="m-ollama" placeholder="http://127.0.0.1:11434">
    <div style="display:flex;gap:.5rem;align-items:center;margin-top:.3rem">
      <button type="button" class="btn" id="m-save">Spremi</button>
      <button type="button" class="btn btn-ghost" id="m-test">Testiraj vezu</button>
      <span id="m-msg" class="meta"></span>
    </div>
  </form>
  <p class="meta" style="margin-top:.75rem">🔒 API ključ se sprema lokalno u bazu i nikad se
  ne prikazuje natrag. Za lokalni model (Ollama) podaci ne izlaze iz mreže.</p>
</div>
<script>{_MODEL_JS}</script>"""
    return page_shell("Model", body, active="postavke")
