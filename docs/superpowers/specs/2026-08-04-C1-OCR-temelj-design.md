# C1 — OCR temelj (dizajn)

**Datum:** 2026-08-04
**Kontekst:** Prvi pod-komad doc-inteligencije (C). Cilj: postojeći skenirani PDF-ovi
u spojenim mapama postanu **pretraživi** (dobiju tekstualni sloj), i to bez vanjskog
VLM-a — lokalnim tesseractom (hrv+eng je instaliran), uz VLM (`ocr_url`) kao fallback
za loše skenove. Radi na cijeloj postojećoj hrpi odmah (vezanje na klijenta je kasnije,
odluka A). Nadograđuje postojeći `ragspine/docs/ocr.py` (VLM-only) na dva motora.

## Opseg (samo C1)
- **Dva OCR motora:** tesseract lokalno (primarno, hrv+eng) + VLM preko `ocr_url` (fallback
  kad je tesseract prazan/kratak I `ocr_url` je konfiguriran).
- **Audit:** koje PDF-ove u spojenoj mapi treba OCR-ati (nemaju tekstualni sloj).
- **„OCR-aj mapu":** endpoint + dashboard akcija koja OCR-a sve PDF-ove-bez-teksta u
  spojenoj mapi; **pretraživi sloj se upisuje U ISTI PDF** (inkrementalni save; NAS backup
  pokriva rizik). Izgled PDF-a se ne mijenja, samo postaje pretraživ + tekst ide u RAG indeks.

NE u C1: registar tipova dokumenata (C2), vađenje polja/rokovi (C3), skener-tok (E).

## Motori i dispatcher
- Novi `ocr.ocr_page_tesseract(png: bytes, cfg) -> str` — pošalje PNG tesseractu
  (`tesseract stdin stdout -l {lang}`) preko `core.subproc` runnera; jezik iz
  `cfg.ocr_langs` (novo, default `"hrv+eng"`); vrati tekst ili `""` (nikad iznimka).
  tesseract nedostupan (binarka fali) → `""`.
- Postojeći `ocr.ocr_page(png, cfg)` = VLM (ostaje).
- Novi dispatcher `ocr.ocr_page_best(png, cfg, transport=None) -> tuple[str, str]` →
  `(tekst, motor)`. Redoslijed: tesseract prvo; ako je rezultat kraći od
  `_MIN_OK_CHARS` (npr. 20) I `cfg.ocr_url` je zadan → probaj VLM; vrati bolji (dulji).
  Motor u povratu (`"tesseract"`/`"vlm"`/`"none"`) za obavijest/log.
- `ocr_pdf` prelazi na `ocr_page_best`; **upis u isti PDF** (`write_text_layer` s
  `out_path=path` + inkrementalni save — `doc.save(path, incremental=True,
  encryption=PDF_ENCRYPT_KEEP)`; ako incremental padne, save u temp pa `os.replace`).
  Vrati i `engines` (koji su motori korišteni po stranici, sažeto).

## Scoping (bitno — spojene mape su pod mount_roots)
- `resolve_scoped_path` sada dopušta i `cfg.mount_roots` (spojene mape), uz postojeće
  `nas_root`/`data_dir`. Put mora biti pod nekim od tih korijena (realpath, anti-symlink);
  inače `ValueError`. Tako OCR radi na KLIJENTI mapama spojenim kroz `folders`.

## Audit + „OCR-aj mapu"
- `ocr.audit_folder(cfg, base) -> dict` — prošeće mapu, vrati
  `{n_pdf, n_pdf_no_text, sample:[putanje bez teksta, max 20]}` (read-only; koristi
  `has_text_layer`). Za prikaz „koliko treba OCR-ati".
- `bulk_ocr` prelazi na `ocr_page_best` (tesseract+VLM); vraća
  `{processed, skipped, engines:{tesseract,vlm}, errors:[...]}`.
- Endpoint `POST /folders/{folder_id}/ocr` → scope folder path (kroz `folders._scoped`),
  pozovi `bulk_ocr`, kreiraj obavijest `folder_ocred` („OCR gotov za „X": P obrađeno,
  S preskočeno"), vrati sažetak. Auth: `require_user_web`.
- Endpoint `GET /folders/{folder_id}/ocr/audit` → `audit_folder` sažetak (za dugme label).
- Dashboard orijentacijska kartica: uz „Skeniraj sad" dodaj **„OCR-aj mapu"**
  (vidljivo kad `scan.n_pdf_no_text > 0`), poziva `POST /folders/{id}/ocr` pa `loadDashboard`.

## Sigurnost / invarijante
- OCR mijenja PDF (dodaje nevidljivi tekst) — **in-place, svjesno** (korisnik odabrao;
  NAS backup). Vizualni sadržaj netaknut; incremental save ne prepisuje original-bytes,
  dodaje append. Fallback save-preko-temp koristi `os.replace` (atomično).
- Scoped realpath (mount_roots ∪ nas_root ∪ data_dir); nula pisanja izvan korijena.
- tesseract/VLM greške → `""` (degradira, ne ruši); prazan OCR se ne indeksira.
- Dijakritika u tekstualnom sloju traži Unicode TTF (postoji upozorenje u
  `write_text_layer`); RAG indeks je neovisan o fontu.
- Idempotentno: `ocr_pdf` preskače PDF koji već ima tekstualni sloj (`has_text_layer`),
  osim `force=True`.

## Testiranje
- `ocr_page_tesseract`: sintetički PNG s tekstom „PDV 25%" (PIL nacrta) → tesseract vrati
  tekst koji sadrži „PDV"/„25". (Skip ako `tesseract` binarka nije na PATH-u.)
- `ocr_page_best`: monkeypatch tesseract→"" + VLM transport→"tekst" → vrati ("tekst","vlm");
  tesseract→"dovoljno teksta" → vrati (...,"tesseract") bez VLM poziva.
- `resolve_scoped_path`: put pod mount_root prolazi; izvan svih korijena → ValueError.
- `audit_folder`: mapa s 1 PDF-bez-teksta + 1 s tekstom → `n_pdf_no_text==1`.
- API: `POST /folders/{id}/ocr` na mapi sa skeniranim (bez-teksta) PDF-om → `processed>=1`,
  PDF poslije ima tekstualni sloj (`has_text_layer` True), obavijest `folder_ocred` kreirana.
- Bez `ocr_url` i s tesseractom: OCR i dalje radi (tesseract-only put).

## Kasnije (izvan C1)
C2 registar tipova + export JSON; C3 hibridno vađenje polja + rokovi→dashboard(7 dana);
E skener-tok; F wizard.
