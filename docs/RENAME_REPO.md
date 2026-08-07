# Rename GitHub repoa RAGSPINE → ATLAS (ručno, vlasnik)

1. GitHub → repo Settings → Repository name → `ATLAS` → Rename.
   (GitHub postavlja redirect sa starog imena, klonovi rade dalje.)
2. Na svakom stroju s klonom:
   `git remote set-url origin https://github.com/MatNik89/ATLAS.git`
3. U repou zatim počisti `compat: URL` retke (README, install skripte).
