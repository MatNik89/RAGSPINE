"""HTML za /obveze — čisti f-string builder, bez template engine ovisnosti."""
import html

from ragspine.business.obveze import KINDS

_CSS = """
body{font-family:system-ui,sans-serif;max-width:900px;margin:2rem auto;padding:0 1rem;color:#1a1a1a}
h1{font-size:1.4rem}
form.selector{display:flex;gap:.5rem;align-items:center;margin-bottom:1.5rem}
select,input[type=month]{padding:.3rem .5rem;font-size:1rem}
table{width:100%;border-collapse:collapse}
th,td{padding:.5rem .75rem;text-align:left;border-bottom:1px solid #ddd}
tr.unsent{background:#fdecea;color:#7a1f14}
tr.sent{background:#eafaf1;color:#14532d}
button{padding:.3rem .8rem;border:none;border-radius:4px;cursor:pointer;font-size:.9rem}
tr.unsent button{background:#c0392b;color:#fff}
tr.sent button{background:#27ae60;color:#fff}
.meta{font-size:.8rem;opacity:.8}
"""


def render_obveze(kind: str, period: str, rows: list[dict]) -> str:
    kind_e, period_e = html.escape(kind), html.escape(period)
    options = "".join(
        f'<option value="{html.escape(k)}"{" selected" if k == kind else ""}>{html.escape(k)}</option>'
        for k in KINDS
    )

    body_rows = []
    for r in rows:
        client = html.escape(str(r["client"]))
        obligation_id = html.escape(str(r["obligation_id"]))
        sent = bool(r["sent"])
        css = "sent" if sent else "unsent"
        label = "Vrati" if sent else "Pošalji"
        sent_val = "0" if sent else "1"
        meta = ""
        if sent and r.get("sent_by"):
            meta = f'<div class="meta">{html.escape(str(r["sent_by"]))} · {html.escape(str(r.get("sent_at") or ""))}</div>'
        body_rows.append(f"""<tr class="{css}">
  <td>{client}{meta}</td>
  <td>{"Poslano" if sent else "Nije poslano"}</td>
  <td><form method="post" action="/obveze/mark">
    <input type="hidden" name="obligation_id" value="{obligation_id}">
    <input type="hidden" name="sent" value="{sent_val}">
    <input type="hidden" name="kind" value="{kind_e}">
    <input type="hidden" name="period" value="{period_e}">
    <button type="submit">{label}</button>
  </form></td>
</tr>""")

    rows_html = "\n".join(body_rows) if body_rows else '<tr><td colspan="3">Nema obveza za ovaj period.</td></tr>'

    return f"""<!DOCTYPE html>
<html lang="hr">
<head>
<meta charset="utf-8">
<title>Obveze — {kind_e} {period_e}</title>
<style>{_CSS}</style>
</head>
<body>
<h1>Obveze — {kind_e} {period_e}</h1>
<form class="selector" method="get" action="/obveze">
  <select name="kind">{options}</select>
  <input type="month" name="period" value="{period_e}">
  <button type="submit">Prikaži</button>
</form>
<table>
<thead><tr><th>Klijent</th><th>Status</th><th></th></tr></thead>
<tbody>
{rows_html}
</tbody>
</table>
</body>
</html>"""
