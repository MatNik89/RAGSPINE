"""HTML za /obveze — čisti f-string builder, bez template engine ovisnosti.
Reuse-a design-system shell iz templates_ui (page_shell + .oblig-row/.chip/.btn).

Ekran: odabir tipa (PDV/JOPPD, proširivo) -> mjesečna navigacija -> lista
klijenata podijeljena na "Za predati" i "Predano". Kvačica na klijentu ga
označi poslanim i spusti u sekciju ispod (i obratno)."""
import html

from atlas.web.templates_ui import page_shell, print_button, script_json

_MJ_NOM = ("Siječanj", "Veljača", "Ožujak", "Travanj", "Svibanj", "Lipanj", "Srpanj",
           "Kolovoz", "Rujan", "Listopad", "Studeni", "Prosinac")


def _shift_month(period: str, delta: int) -> str:
    # Defense-in-depth: endpoints validate period as \\d{4}-\\d{2}, but
    # render_obligations must never crash if called with a malformed one — echoed
    # values stay html.escaped/script_json'd regardless.
    try:
        y, m = int(period[:4]), int(period[5:7])
    except ValueError:
        return period
    idx = (y * 12 + (m - 1)) + delta
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def _month_label(period: str) -> str:
    try:
        m = int(period[5:7])
        return f"{_MJ_NOM[m - 1]} {period[:4]}."
    except (ValueError, IndexError):
        return period


_MARK_JS = """
function $(id) { return document.getElementById(id); }

async function postMark(id, sent) {
  try {
    const res = await fetch('/obveze/mark', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ obligation_id: Number(id), kind: KIND, period: PERIOD, sent: sent ? 1 : 0 }),
    });
    return res.ok;
  } catch (e) { return false; }
}

function updateCounts() {
  $('count-unsent').textContent = $('list-unsent').querySelectorAll('.oblig-row').length;
  $('count-sent').textContent = $('list-sent').querySelectorAll('.oblig-row').length;
  ['unsent', 'sent'].forEach(function (which) {
    var list = $('list-' + which);
    var empty = $('empty-' + which);
    empty.style.display = list.querySelectorAll('.oblig-row').length ? 'none' : 'block';
  });
}

async function onToggle(cb) {
  var row = cb.closest('.oblig-row');
  var id = row.getAttribute('data-id');
  var sent = cb.checked;
  cb.disabled = true;
  var ok = await postMark(id, sent);
  cb.disabled = false;
  if (!ok) { cb.checked = !sent; toast('Greška pri spremanju. Pokušajte ponovno.', 'bad'); return; }
  row.className = 'oblig-row ' + (sent ? 'ok' : 'bad');
  var status = row.querySelector('.ostatus');
  status.textContent = sent ? 'Predano' : 'Nije predano';
  status.className = 'chip ostatus ' + (sent ? 'ok' : 'bad');
  $(sent ? 'list-sent' : 'list-unsent').appendChild(row);
  updateCounts();
}
"""


def _row_html(r: dict) -> str:
    client = html.escape(str(r["client"]))
    obligation_id = int(r["obligation_id"])
    sent = bool(r["sent"])
    state = "ok" if sent else "bad"
    status = "Predano" if sent else "Nije predano"
    checked = " checked" if sent else ""
    meta = ""
    if sent and r.get("sent_by"):
        meta = (f'<div class="meta">{html.escape(str(r["sent_by"]))} · '
                f'{html.escape(str(r.get("sent_at") or ""))}</div>')
    return f"""<div class="oblig-row {state}" data-id="{obligation_id}">
  <label class="oblig-check"><input type="checkbox"{checked} onchange="onToggle(this)">
    <span class="oname">{client}{meta}</span></label>
  <span class="chip ostatus {state}">{status}</span>
</div>"""


def _section(title: str, list_id: str, count_id: str, empty_id: str,
             empty_text: str, rows: list[dict]) -> str:
    rows_html = "\n".join(_row_html(r) for r in rows)
    empty_style = "display:none" if rows else "display:block"
    return f"""<div class="oblig-section">
  <h2>{title} <span class="sec-count">(<span id="{count_id}">{len(rows)}</span>)</span></h2>
  <div id="{list_id}">
{rows_html}
  </div>
  <p class="meta" id="{empty_id}" style="{empty_style}">{empty_text}</p>
</div>"""


def render_obligations(kind: str, period: str, rows: list[dict],
                  tabs: list[tuple[str, str]] | None = None) -> str:
    kind_e, period_e = html.escape(kind), html.escape(period)
    prev_m, next_m = _shift_month(period, -1), _shift_month(period, 1)

    tabs = tabs or [(kind, kind)]
    tabs_html = "".join(
        f'<a class="obveze-tab{" active" if t_kind == kind else ""}" '
        f'href="/obveze?kind={html.escape(t_kind)}&period={period_e}">{html.escape(t_label)}</a>'
        for t_kind, t_label in tabs
    ) + '<a class="obveze-tab tab-add" href="/ui/obveze-tipovi" title="Dodaj/uredi vrste obveza">+ vrsta</a>'

    unsent = [r for r in rows if not r["sent"]]
    sent = [r for r in rows if r["sent"]]
    unsent_html = _section("Za predati", "list-unsent", "count-unsent", "empty-unsent",
                           "Sve obveze za ovaj mjesec su predane. \U0001F389", unsent)
    sent_html = _section("Predano", "list-sent", "count-sent", "empty-sent",
                         "Još nijedna obveza nije označena predanom.", sent)

    default_subject = f"Podsjetnik: {kind} obveza nije predana"
    default_body = f"Poštovani, molimo dostavite dokumentaciju za {kind} ({period}) što prije."

    body = f"""<div style="display:flex;justify-content:space-between;align-items:center;gap:1rem">
  <h1>Obveze</h1>{print_button(f"Ispis — {kind_e} {_month_label(period)}")}</div>
<div class="obveze-tabs" role="tablist" aria-label="Vrsta obveze">{tabs_html}</div>
<div class="month-nav">
  <a class="step" href="/obveze?kind={kind_e}&period={html.escape(prev_m)}" aria-label="Prethodni mjesec">&#8249;</a>
  <span class="month-label">{html.escape(_month_label(period))}</span>
  <a class="step" href="/obveze?kind={kind_e}&period={html.escape(next_m)}" aria-label="Sljedeći mjesec">&#8250;</a>
</div>

{unsent_html}
{sent_html}

<div class="card" style="margin-bottom:1.5rem">
  <h2>Kampanja podsjetnika</h2>
  <p class="meta">Pošalji podsjetnik svim klijentima koji još nisu predali {kind_e} za {html.escape(_month_label(period))}.
  Prvo se uvijek prikaže probni ispis (dry-run) — stvarno slanje traži potvrdu.</p>
  <button type="button" class="btn" id="campaign-btn">Pošalji podsjetnik nepredanima</button>
  <div id="campaign-confirm" class="stack" style="display:none;margin-top:.75rem;max-width:520px">
    <label for="campaign-subject">Predmet</label>
    <input type="text" id="campaign-subject" value="{html.escape(default_subject)}">
    <label for="campaign-body">Poruka</label>
    <textarea id="campaign-body" rows="3">{html.escape(default_body)}</textarea>
    <label><input type="checkbox" id="campaign-real"> stvarno pošalji</label>
    <button type="button" class="btn btn-danger" id="campaign-send">Pošalji</button>
  </div>
  <div id="campaign-result" style="margin-top:.75rem"></div>
</div>
<script>
const KIND = {script_json(kind)};
const PERIOD = {script_json(period)};
</script>
<script>{_MARK_JS}</script>
<script>{_CAMPAIGN_JS}</script>"""

    return page_shell(f"Obveze — {kind} {period}", body, active="obveze")


_APPLIES_LABELS = {
    "pdv": "PDV obveznici",
    "employees": "Ima zaposlene",
    "dobit": "Porez na dobit (d.o.o.)",
    "dohodak": "Dohodaš (obrt, knjige)",
    "pausal": "Paušalni obrt",
    "all_active": "Svi aktivni",
    "manual": "Ručno po klijentu",
}

_TYPES_JS = """
function $(id){ return document.getElementById(id); }
var APPLIES = {pdv:'PDV obveznici', employees:'Ima zaposlene', dobit:'Porez na dobit (d.o.o.)', dohodak:'Dohodaš (obrt, knjige)', pausal:'Paušalni obrt', all_active:'Svi aktivni', manual:'Ručno po klijentu'};
var FREQ = {monthly:'Mjesečno', quarterly:'Tromjesečno', yearly:'Godišnje'};

function fillForm(t){
  $('t-kind').value = t.kind; $('t-kind').readOnly = true;
  $('t-label').value = t.label || ''; $('t-rule').value = t.rule || '';
  $('t-frequency').value = t.frequency || 'monthly';
  $('t-applies').value = t.applies_to || 'all_active';
  $('t-active').checked = !!t.active; $('t-sort').value = t.sort;
  $('form-title').textContent = 'Uredi: ' + t.kind;
}
function resetForm(){
  $('t-kind').value=''; $('t-kind').readOnly=false; $('t-label').value='';
  $('t-rule').value=''; $('t-frequency').value='monthly'; $('t-applies').value='all_active';
  $('t-active').checked=true; $('t-sort').value=100; $('form-title').textContent='Nova vrsta';
}

function renderTypes(rows){
  var body = $('types-body'); body.textContent='';
  rows.forEach(function(t){
    var tr = document.createElement('tr'); tr.style.cursor='pointer';
    tr.addEventListener('click', function(){ fillForm(t); });
    function td(text, mono){ var d=document.createElement('td'); if(mono)d.className='num'; d.textContent=text; return d; }
    tr.appendChild(td(t.kind));
    tr.appendChild(td(t.label||''));
    tr.appendChild(td(t.rule||'—'));
    tr.appendChild(td(FREQ[t.frequency]||t.frequency));
    tr.appendChild(td(APPLIES[t.applies_to]||t.applies_to));
    var st=document.createElement('td'); var chip=document.createElement('span');
    chip.className='chip '+(t.active?'ok':''); chip.textContent=t.active?'aktivna':'skrivena';
    st.appendChild(chip); tr.appendChild(st);
    body.appendChild(tr);
  });
}

async function loadTypes(){
  var res = await fetch('/obveze/tipovi', {credentials:'same-origin'});
  if(res.ok) renderTypes(await res.json());
}

async function saveType(){
  var payload = {
    kind: $('t-kind').value.trim(), label: $('t-label').value.trim(),
    rule: $('t-rule').value.trim(), frequency: $('t-frequency').value,
    applies_to: $('t-applies').value, active: $('t-active').checked?1:0,
    sort: Number($('t-sort').value)||100,
  };
  if(!payload.kind){ toast('Šifra (kind) je obavezna.', 'bad'); return; }
  var res = await fetch('/obveze/tipovi', {method:'POST', credentials:'same-origin',
    headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)});
  if(!res.ok){ var e = await res.json().catch(function(){return {};}); toast('Greška: '+(e.detail||res.status), 'bad'); return; }
  resetForm(); loadTypes();
}

$('t-save').addEventListener('click', saveType);
$('t-reset').addEventListener('click', resetForm);
loadTypes();
"""


def obligations_none_page() -> str:
    body = """<h1>Obveze</h1>
<p class="meta">Nijedna vrsta obveze trenutno nije aktivna.</p>
<p><a class="btn" href="/ui/obveze-tipovi">Uredi vrste obveza</a></p>"""
    return page_shell("Obveze", body, active="obveze")


def obligation_types_page() -> str:
    freq_opts = "".join(f'<option value="{k}">{html.escape(v)}</option>'
                        for k, v in (("monthly", "Mjesečno"), ("quarterly", "Tromjesečno"),
                                     ("yearly", "Godišnje")))
    applies_opts = "".join(f'<option value="{k}">{html.escape(v)}</option>'
                           for k, v in _APPLIES_LABELS.items())
    body = f"""<h1>Vrste obveza</h1>
<p class="meta">Ovdje dodaješ i uređuješ vrste obveza (PDV, JOPPD, najam…). ATLAS ih
odmah poveže: pojave se kao tab na Obvezama, u kalendaru i na dashboardu.
<a href="/obveze">← natrag na Obveze</a></p>

<table class="ledger" style="margin:1rem 0">
  <thead><tr><th>Šifra</th><th>Naziv</th><th class="num">Rok (pravilo)</th><th>Frekvencija</th><th>Tko je obveznik</th><th>Status</th></tr></thead>
  <tbody id="types-body"><tr><td colspan="6">Učitavanje…</td></tr></tbody>
</table>

<div class="card" style="max-width:640px">
  <h2 id="form-title">Nova vrsta</h2>
  <form class="stack" onsubmit="return false">
    <label for="t-kind">Šifra (npr. NAJAM) — kratka, VELIKA slova</label>
    <input type="text" id="t-kind" maxlength="20" placeholder="NAJAM">
    <label for="t-label">Naziv</label>
    <input type="text" id="t-label" placeholder="Najamnina">
    <label for="t-rule">Rok — pravilo (npr. <code>monthly:15</code>, <code>quarterly:20</code>, <code>yearly:04-30</code>)</label>
    <input type="text" id="t-rule" placeholder="monthly:15">
    <label for="t-frequency">Frekvencija</label>
    <select id="t-frequency">{freq_opts}</select>
    <label for="t-applies">Tko je obveznik</label>
    <select id="t-applies">{applies_opts}</select>
    <label><input type="checkbox" id="t-active" checked> aktivna (prikaži kao tab)</label>
    <input type="hidden" id="t-sort" value="100">
    <div style="display:flex;gap:.5rem">
      <button type="button" class="btn" id="t-save">Spremi</button>
      <button type="button" class="btn btn-ghost" id="t-reset">Nova / očisti</button>
    </div>
  </form>
  <p class="meta" style="margin-top:.75rem">„Ručno po klijentu" (npr. najam) → obvezu potom
  označiš pojedinom klijentu na njegovom kartonu.</p>
</div>
<script>{_TYPES_JS}</script>"""
    return page_shell("Vrste obveza", body, active="postavke")


_CAMPAIGN_JS = """
function renderCampaignResult(data, real) {
  const box = document.getElementById('campaign-result');
  box.textContent = '';
  const p = document.createElement('p');
  p.className = 'meta';
  p.textContent = real
    ? ('Poslano ' + data.audience + ' klijentima.')
    : ('Poslat će podsjetnik ' + data.audience + ' klijentima.');
  box.appendChild(p);
  Object.keys(data.results || {}).forEach(function (status) {
    const chip = document.createElement('span');
    chip.className = 'chip';
    chip.style.marginRight = '.3rem';
    chip.textContent = status + ': ' + data.results[status];
    box.appendChild(chip);
  });
}

async function runCampaign(really) {
  const subject = document.getElementById('campaign-subject').value.trim();
  const body = document.getElementById('campaign-body').value.trim();
  if (!subject || !body) { toast('Unesite predmet i tekst poruke.', 'bad'); return; }
  // dry_run defaults true; only false when the "stvarno pošalji" box is checked
  const dry_run = !really;
  try {
    const res = await fetch('/messaging/campaign', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        filter: 'compliance_missing', kind: KIND, period: PERIOD,
        subject: subject, body: body, dry_run: dry_run,
      }),
    });
    if (!res.ok) { toast('Greška: ' + res.status, 'bad'); return; }
    const data = await res.json();
    renderCampaignResult(data, really);
  } catch (err) {
    toast('Greška u komunikaciji sa serverom.', 'bad');
  }
}

document.getElementById('campaign-btn').addEventListener('click', function () {
  document.getElementById('campaign-confirm').style.display = 'flex';
  runCampaign(false);
});

document.getElementById('campaign-send').addEventListener('click', function () {
  const really = document.getElementById('campaign-real').checked;
  runCampaign(really);
});
"""
