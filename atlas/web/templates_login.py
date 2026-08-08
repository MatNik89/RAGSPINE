"""HTML za /login — čisti f-string builder, bez template engine ovisnosti.

Dvokoračni tok (isti ekran, JS mijenja prikaz):
1) korak "user": samo korisničko ime + Dalje -> POST /login/step.
2) prema state-u iz koraka 1:
   - "password": polje šifre -> POST /auth/login (postojeći tok).
   - "activate": dva polja nove šifre -> POST /login/activate.
Namjerno IZVAN design systema (templates_ui CSS_TOKENS) — korisnik radi
vizualni redizajn odvojeno, ovdje se dira samo tok."""

_CSS = """
body{font-family:system-ui,sans-serif;max-width:340px;margin:4rem auto;padding:0 1rem;color:#1a1a1a}
form{display:flex;flex-direction:column;gap:.6rem}
input{padding:.5rem;font-size:1rem}
button{padding:.5rem;font-size:1rem;background:#2563eb;color:#fff;border:none;border-radius:4px;cursor:pointer}
.err{color:#b91c1c;font-size:.9rem;margin:0;min-height:1.1rem}
.hint{color:#444;font-size:.95rem;margin:0 0 .2rem}
"""

_JS = """
function $(id){ return document.getElementById(id); }

function showErr(msg){ $('err').textContent = msg || ''; }

function apiErr(e){
  return (e && typeof e.detail === 'string') ? e.detail : 'Došlo je do greške — pokušajte ponovno.';
}

async function step(){
  showErr('');
  var username = $('username').value.trim();
  if (!username) return;
  var res = await fetch('/login/step', {method:'POST', credentials:'same-origin',
    headers:{'Content-Type':'application/json'}, body: JSON.stringify({username: username})});
  if (!res.ok){ showErr(apiErr(await res.json().catch(function(){return{};}))); return; }
  var d = await res.json();
  $('username').readOnly = true;
  $('step1').style.display = 'none';
  if (d.state === 'activate'){
    $('hello').textContent = 'Dobrodošao, ' + username + ' — postavi svoju šifru';
    $('activate').style.display = '';
    $('pw1').focus();
  } else {
    $('password').style.display = '';
    $('pw').focus();
  }
}

async function doPassword(){
  showErr('');
  var res = await fetch('/auth/login', {method:'POST', credentials:'same-origin',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({username: $('username').value.trim(), password: $('pw').value})});
  if (res.ok){ location.href = '/'; return; }
  showErr(apiErr(await res.json().catch(function(){return{};})));
}

async function doActivate(){
  showErr('');
  var p1 = $('pw1').value, p2 = $('pw2').value;
  if (p1.length < 8){ showErr('Šifra mora imati barem 8 znakova.'); return; }
  if (p1 !== p2){ showErr('Šifre se ne poklapaju.'); return; }
  var res = await fetch('/login/activate', {method:'POST', credentials:'same-origin',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({username: $('username').value.trim(), password: p1, password2: p2})});
  if (res.ok){ location.href = '/'; return; }
  showErr(apiErr(await res.json().catch(function(){return{};})));
}

$('login-form').addEventListener('submit', function(e){
  e.preventDefault();
  if ($('activate').style.display !== 'none') doActivate();
  else if ($('password').style.display !== 'none') doPassword();
  else step();
});
"""


def render_login() -> str:
    return f"""<!DOCTYPE html>
<html lang="hr">
<head>
<meta charset="utf-8">
<title>Prijava — ATLAS</title>
<style>{_CSS}</style>
</head>
<body>
<h1>Prijava</h1>
<form id="login-form" autocomplete="off">
  <input type="text" id="username" name="username" placeholder="Korisničko ime" required autofocus>
  <div id="step1">
    <button type="submit" id="step1-btn">Dalje</button>
  </div>
  <div id="password" style="display:none">
    <input type="password" id="pw" name="password" placeholder="Lozinka" required>
    <button type="submit" id="pw-btn">Prijava</button>
  </div>
  <div id="activate" style="display:none">
    <p class="hint" id="hello"></p>
    <input type="password" id="pw1" placeholder="Nova šifra (min 8 znakova)" required minlength="8">
    <input type="password" id="pw2" placeholder="Ponovi šifru" required minlength="8">
    <button type="submit" id="act-btn">Postavi šifru i prijavi se</button>
  </div>
  <p class="err" id="err"></p>
</form>
<script>{_JS}</script>
</body>
</html>"""
