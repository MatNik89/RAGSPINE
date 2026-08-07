"""HTML za /login — čisti f-string builder, bez template engine ovisnosti."""

_CSS = """
body{font-family:system-ui,sans-serif;max-width:340px;margin:4rem auto;padding:0 1rem;color:#1a1a1a}
form{display:flex;flex-direction:column;gap:.6rem}
input{padding:.5rem;font-size:1rem}
button{padding:.5rem;font-size:1rem;background:#2563eb;color:#fff;border:none;border-radius:4px;cursor:pointer}
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
<form method="post" action="/auth/login">
  <input type="text" name="username" placeholder="Korisničko ime" required autofocus>
  <input type="password" name="password" placeholder="Lozinka" required>
  <button type="submit">Prijava</button>
</form>
</body>
</html>"""
