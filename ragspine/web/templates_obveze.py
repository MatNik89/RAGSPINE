"""HTML za /obveze — čisti f-string builder, bez template engine ovisnosti.
Reuse-a design-system shell iz templates_ui (page_shell + .oblig-row/.chip/.btn).

Ekran: odabir tipa (PDV/JOPPD, proširivo) -> mjesečna navigacija -> lista
klijenata podijeljena na "Za predati" i "Predano". Kvačica na klijentu ga
označi poslanim i spusti u sekciju ispod (i obratno)."""
import html

from ragspine.business.obveze import KINDS
from ragspine.web.templates_ui import page_shell, script_json

# Tipovi ponuđeni kao tabovi na ekranu (proširivo — dodaj kad zatreba).
# Data-model (obveze.KINDS) drži i DOH za digest/dashboard; ovdje se ne prikazuje
# dok korisnik ne zatraži.
OBVEZE_TABS = ("PDV", "JOPPD")

_MJ_NOM = ("Siječanj", "Veljača", "Ožujak", "Travanj", "Svibanj", "Lipanj", "Srpanj",
           "Kolovoz", "Rujan", "Listopad", "Studeni", "Prosinac")


def _shift_month(period: str, delta: int) -> str:
    # Defense-in-depth: endpoints validate period as \\d{4}-\\d{2}, but
    # render_obveze must never crash if called with a malformed one — echoed
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
  if (!ok) { cb.checked = !sent; alert('Greška pri spremanju. Pokušajte ponovno.'); return; }
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


def render_obveze(kind: str, period: str, rows: list[dict]) -> str:
    kind_e, period_e = html.escape(kind), html.escape(period)
    prev_m, next_m = _shift_month(period, -1), _shift_month(period, 1)

    tabs = "".join(
        f'<a class="obveze-tab{" active" if t == kind else ""}" '
        f'href="/obveze?kind={html.escape(t)}&period={period_e}">{html.escape(t)}</a>'
        for t in OBVEZE_TABS
    )

    unsent = [r for r in rows if not r["sent"]]
    sent = [r for r in rows if r["sent"]]
    unsent_html = _section("Za predati", "list-unsent", "count-unsent", "empty-unsent",
                           "Sve obveze za ovaj mjesec su predane. \U0001F389", unsent)
    sent_html = _section("Predano", "list-sent", "count-sent", "empty-sent",
                         "Još nijedna obveza nije označena predanom.", sent)

    default_subject = f"Podsjetnik: {kind} obveza nije predana"
    default_body = f"Poštovani, molimo dostavite dokumentaciju za {kind} ({period}) što prije."

    body = f"""<h1>Obveze</h1>
<div class="obveze-tabs" role="tablist" aria-label="Vrsta obveze">{tabs}</div>
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
  if (!subject || !body) { alert('Unesite predmet i tekst poruke.'); return; }
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
    if (!res.ok) { alert('Greška: ' + res.status); return; }
    const data = await res.json();
    renderCampaignResult(data, really);
  } catch (err) {
    alert('Greška u komunikaciji sa serverom.');
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
