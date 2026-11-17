# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

This repository is the solution to the task brief `PRUEBA ANALISTA DE INNOVACION 2026 (1) (3) (2) (2).docx`, a 24-hour technical test for a Data Science Analyst role. The brief is in Spanish. All data in it is fictitious and is used only for evaluation. To read the brief: `pandoc "<file>.docx" -t plain`. User-facing docs (`README.md`) and code comments are in Spanish.

Part 1 (the RPA) is done. Part 2 (extraction to CSV) is pending.

## Commands

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python rpa_descarga.py            # Part 1: downloads PDFs for cufes.txt (skips existing)
.venv/bin/python rpa_descarga.py --force    # re-download everything (costs 2 CapSolver tokens per CUFE)
```

There are no tests. The CapSolver API key is read from `.env` as `capsolver_key`, with `CAPSOLVER_API_KEY` as a fallback. Never print it.

## Part 1 architecture (verified against the live site, Nov 2026)

- The CAPTCHA is **Cloudflare Turnstile**, in interactive mode, and it is solved through **CapSolver** (`rpa/captcha.py`, task type `AntiTurnstileTaskProxyLess`).
  - In headless Playwright, Cloudflare doesn't even serve the search form. That is why the flow is plain `requests`, not a browser.
  - Do not try to auto-click the Turnstile checkbox. The user chose a paid solver, and the permission classifier blocks automating that click.
- `rpa/dian.py` runs a 4-request flow:
  1. GET the search form.
  2. POST the CUFE, **the NIT (now required by the site)** and token #1.
  3. Replay the auto-submitted `#postForm` to `/Document/ShowDocumentToPublic`.
  4. POST `/Document/DownloadPDF` with **a second Turnstile token** in the `captcha` field.
- The NIT given by the evaluator is `900156264`, the same for all 10 CUFEs. It is also the **password of the downloaded PDFs**.
- Outputs:
  - `output/pdfs/` holds the encrypted originals.
  - `output/pdfs_sin_clave/` holds decrypted copies made with pypdf. Part 2 should read these.
  - `output/metrics_rpa.csv` is an append-only log keyed by `run_id`. Skipped files are not logged.
  - `output/debug/` is gitignored.
- Invoice numbers do not share one prefix (`FEBQ-243564`, `CRIA-1406`, `FE-6599`…). In the PDF text, the labels and values are split across lines unless you use `pdftotext -layout`.

## The task

**Part 1: RPA download.** Build an automation in Python that does the following for each CUFE (Código Único de Facturación Electrónica) listed below:

1. Opens https://catalogo-vpfe.dian.gov.co/User/SearchDocument.
2. Searches for the CUFE.
3. Downloads the PDF of the invoice's graphical representation.

The page is protected by a CAPTCHA, and the brief requires the automated process to solve it.

CUFEs:
```
eccc372e4bdb8125cf2e896b405ddb91918c7c6d92238892e3b36bd3b6a86929a3c47ed18ae8f559a1bf51b89ab560f1
8e7b821b46445807245ad6c223d6a054923cea3e5dc59983e55a40cfeac2a4f570eccb198446b27d15c8d834c2e2ced9
6a7ec741dd95ff7f5240481b3ed287f85eea3b665a8b26b91b376095f9bb134d2397ec96586fd16e116ef247a6506770
c2e740942c1925d57f2e82254f21992cbad461a0df57fd42a1475fbe71ba5ab68a824c03487cbc19e80c50e436dd30fe
3e6331e537f08a54ec2d9f38904afaae80b16ffa502a12fd9aba388048d50d76b311809be9236aaa1bd1cc781a9d87f3
f639eb72baf2e301eddde8e8e5087295831a12c781d4cf6b4671932cdfa3599223aa3375af807a1f5d66aa1543c9fb91
2a90ab2ce6b54e505fc9f463bffb528244abef80d9ab1a9a39d771169c7cfe52b9a48b6feab1fdceb8b4b59ff6e319db
341a447c4891f8e302771bf6a6d95d7011e72d44a8cb670356cbb5e92549824eb0071c542261d837978cfd351d2131d1
bbed883f80308e0e0e16e52a1286d71274b1a78419875458c590fcec41c065eda762aaef478775dc219de3c5e2d6400b
70d1aab6f793d4fc7151d69400138cf1bd147e54aaeaf274072898f9d12d979e86e8de79fef27668896db76589323ad0
```

**Part 2: Extraction to CSV.** Extract text from the exported invoices. Build one DataFrame per file and export the data to CSV. Fields to extract:

- From the "Datos del Documento" and "Datos del Emisor/Vendedor" sections: Número de factura, Fecha de emisión, NIT del emisor.
- From the "Detalles del Producto" table: Código, Descripción, Cantidad, Precio unitario.

The header fields repeat on every product line item.

The brief also asks for a report of the time each step took and of the process's metric constraints (for example success rate, CAPTCHA failures and per-invoice latency). Instrument the pipeline so it captures these.

## Deliverables

- Python code.
- A GitHub repository with a clear README.
- The answers written back into the original .docx.
