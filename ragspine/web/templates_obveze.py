"""HTML za /obveze — čisti f-string builder, bez template engine ovisnosti.
Reuse-a design-system shell iz templates_ui (page_shell + .oblig-row/.chip/.btn)."""
import html
import json

from ragspine.business.obveze import KINDS
from ragspine.web.templates_ui import page_shell

_CAMPAIGN_JS = """
function $(id) { return document.getElementById(id); }

function renderCampaignResult(data, real) {
  const box = $('campaign-result');
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
  const subject = $('campaign-subject').value.trim();
  const body = $('campaign-body').value.trim();
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

$('campaign-btn').addEventListener('click', function () {
  $('campaign-confirm').style.display = 'flex';
  runCampaign(false);
});

$('campaign-send').addEventListener('click', function () {
  const really = $('campaign-real').checked;
  runCampaign(really);
});
"""


def _row_html(r: dict, kind_e: str, period_e: str) -> str:
    client = html.escape(str(r["client"]))
    obligation_id = int(r["obligation_id"])
    sent = bool(r["sent"])
    state = "ok" if sent else "bad"
    label = "Vrati" if sent else "Pošalji"
    sent_val = "0" if sent else "1"
    chip_label = "Predano" if sent else "Nije predano"
    meta = ""
    if sent and r.get("sent_by"):
        meta = f'<div class="meta">{html.escape(str(r["sent_by"]))} · {html.escape(str(r.get("sent_at") or ""))}</div>'
    return f"""<div class="oblig-row {state}">
  <span>{client}{meta}</span>
  <span class="chip {state}">{chip_label}</span>
  <form method="post" action="/obveze/mark" style="margin-left:auto">
    <input type="hidden" name="obligation_id" value="{obligation_id}">
    <input type="hidden" name="sent" value="{sent_val}">
    <input type="hidden" name="kind" value="{kind_e}">
    <input type="hidden" name="period" value="{period_e}">
    <button type="submit" class="btn{' btn-ghost' if sent else ''}">{label}</button>
  </form>
</div>"""


def render_obveze(kind: str, period: str, rows: list[dict]) -> str:
    kind_e, period_e = html.escape(kind), html.escape(period)
    options = "".join(
        f'<option value="{html.escape(k)}"{" selected" if k == kind else ""}>{html.escape(k)}</option>'
        for k in KINDS
    )

    rows_html = ("\n".join(_row_html(r, kind_e, period_e) for r in rows)
                 if rows else '<p class="meta">Nema obveza za ovaj period.</p>')

    default_subject = f"Podsjetnik: {kind} obveza nije predana"
    default_body = f"Poštovani, molimo dostavite dokumentaciju za {kind} ({period}) što prije."

    body = f"""<h1>Obveze — {kind_e} {period_e}</h1>
<form class="selector" method="get" action="/obveze" style="display:flex;gap:.5rem;align-items:center;margin:1rem 0">
  <select name="kind">{options}</select>
  <input type="month" name="period" value="{period_e}">
  <button type="submit" class="btn btn-ghost">Prikaži</button>
</form>

<div class="card" style="margin-bottom:1.5rem">
  <h2>Kampanja podsjetnika</h2>
  <p class="meta">Pošalji podsjetnik svim klijentima koji još nisu predali {kind_e} za {period_e}.
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

<div id="oblig-rows">
{rows_html}
</div>
<script>
const KIND = {json.dumps(kind)};
const PERIOD = {json.dumps(period)};
</script>
<script>{_CAMPAIGN_JS}</script>"""

    return page_shell(f"Obveze — {kind} {period}", body, active="obveze")
