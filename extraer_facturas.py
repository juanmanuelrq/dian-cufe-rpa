"""Parte 2 – Extracción de datos de las facturas descargadas a DataFrames y CSV.

Uso:
    python extraer_facturas.py
Lee output/pdfs_sin_clave/ (generados por rpa_descarga.py) y escribe:
    output/csv/<cufe>.csv          un CSV por factura (un DataFrame por archivo)
    output/facturas.csv            todas las facturas consolidadas
    output/metrics_extraccion.csv  tiempos y validaciones por archivo

Los PDF de la DIAN tienen capa de texto (no son imágenes escaneadas). Por eso el texto se lee con
pdfplumber, que es exacto y rápido, en vez de OCR.
"""
from __future__ import annotations

import argparse
import logging
import re
import time
import unicodedata
from datetime import datetime
from pathlib import Path

import pandas as pd
import pdfplumber

from rpa_descarga import DEFAULT_NIT, pendientes, read_inputs

log = logging.getLogger("extraccion")

COLUMNS = ["numero_factura", "fecha_emision", "nit_emisor", "codigo", "descripcion", "cantidad", "precio_unitario"]

HEADER_RX = {
    "numero_factura": r"N[úu]mero de Factura:\s*(\S+)",
    "fecha_emision": r"Fecha de Emisi[óo]n:\s*(\d{2}/\d{2}/\d{4})",
    "nit_emisor": r"Nit del Emisor:\s*(\d+)",
}
SUBTOTAL_RX = r"Subtotal\s+([\d\.]+,\d{2})"


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip().lower()


def parse_number(s: str) -> float | None:
    """'$ 1.234.567,89' -> 1234567.89 (formato colombiano)."""
    s = re.sub(r"[^\d,.\-]", "", s or "")
    if not s:
        return None
    return float(s.replace(".", "").replace(",", "."))


def cell_text(page, bbox) -> str:
    """Reconstruye el texto de una celda carácter a carácter.

    La representación gráfica corta las descripciones largas en medio de una palabra
    ("UNILATERA" + "L"), y cuando el corte cae en un espacio el PDF conserva ese espacio al final
    de la línea. Por eso las líneas se unen sin separador; un join con espacio o salto de línea
    dañaría el texto.
    """
    x0, top, x1, bottom = bbox
    chars = [c for c in page.chars
             if c["x0"] >= x0 - 0.5 and c["x1"] <= x1 + 0.5 and c["top"] >= top - 0.5 and c["bottom"] <= bottom + 0.5]
    lines: dict[int, list] = {}
    for c in chars:
        lines.setdefault(round(c["top"]), []).append(c)
    text = "".join("".join(c["text"] for c in sorted(v, key=lambda c: c["x0"])) for _, v in sorted(lines.items()))
    return re.sub(r"\s+", " ", text).strip()


def product_rows(pdf) -> list[dict]:
    """Filas de la tabla 'Detalles de Productos' de todas las páginas."""
    rows = []
    for page in pdf.pages:
        for table in page.find_tables():
            header_idx, colmap = None, {}
            for i, row in enumerate(table.rows):
                texts = [_norm(cell_text(page, c)) if c else "" for c in row.cells]
                if "codigo" in texts and "descripcion" in texts and "cantidad" in texts:
                    header_idx = i
                    colmap = {
                        "codigo": texts.index("codigo"),
                        "descripcion": texts.index("descripcion"),
                        "cantidad": texts.index("cantidad"),
                        "precio_unitario": next(j for j, t in enumerate(texts) if t.startswith("precio unitario")),
                        "nro": texts.index("nro."),
                    }
                    break
            if header_idx is None:
                continue  # otra tabla (p. ej. descuentos/cargos)
            for row in table.rows[header_idx + 1:]:
                cells = row.cells
                get = lambda k: cell_text(page, cells[colmap[k]]) if cells[colmap[k]] else ""
                if not get("nro").isdigit():
                    continue
                rows.append({
                    "codigo": get("codigo"),
                    "descripcion": get("descripcion"),
                    "cantidad": parse_number(get("cantidad")),
                    "precio_unitario": parse_number(get("precio_unitario")),
                })
    return rows


def extract_invoice(path: Path, password: str | None = None) -> tuple[pd.DataFrame, dict]:
    """Devuelve el DataFrame de una factura (una fila por producto) y datos de validación."""
    with pdfplumber.open(path, password=password or "") as pdf:
        text = "\n".join(p.extract_text() or "" for p in pdf.pages)
        header = {}
        for key, rx in HEADER_RX.items():
            m = re.search(rx, text)
            if not m:
                raise ValueError(f"No se encontró '{key}' en {path.name}")
            header[key] = m.group(1)
        header["fecha_emision"] = datetime.strptime(header["fecha_emision"], "%d/%m/%Y").date().isoformat()
        items = product_rows(pdf)
    if not items:
        raise ValueError(f"No se encontraron productos en {path.name}")

    df = pd.DataFrame([{**header, **it} for it in items], columns=COLUMNS)
    m = re.search(SUBTOTAL_RX, text)
    subtotal = parse_number(m.group(1)) if m else None
    suma = round(float((df["cantidad"] * df["precio_unitario"]).sum()), 2)
    check = {"items": len(df), "subtotal_pdf": subtotal, "suma_items": suma,
             "subtotal_ok": subtotal is not None and abs(subtotal - suma) < 0.01}
    return df, check


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", type=Path, default=Path("cufes.txt"))
    ap.add_argument("--nit", default=DEFAULT_NIT)
    ap.add_argument("--out", type=Path, default=Path("output"))
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")

    items = read_inputs(args.input, args.nit)
    # Revisión local (sin red ni CapSolver) de que la Parte 1 dejó todos los PDF.
    faltan = pendientes(items, args.out)
    if faltan:
        log.error("Faltan %d PDF válidos; ejecute primero: python rpa_descarga.py", len(faltan))
        return 1

    csv_dir = args.out / "csv"
    csv_dir.mkdir(parents=True, exist_ok=True)
    frames, metrics = [], []
    t_run = time.perf_counter()
    for n, (cufe, _nit) in enumerate(items, 1):
        pdf_path = args.out / "pdfs_sin_clave" / f"{cufe}.pdf"
        t0 = time.perf_counter()
        row = {"n": n, "cufe": cufe, "archivo": pdf_path.name, "estado": "ok", "error": ""}
        try:
            df, check = extract_invoice(pdf_path)
            df.insert(0, "cufe", cufe)
            df.to_csv(csv_dir / f"{cufe}.csv", index=False, encoding="utf-8")
            frames.append(df)
            row.update(check, numero_factura=df.at[0, "numero_factura"])
            if not check["subtotal_ok"]:
                row["estado"] = "revisar"
                log.warning("[%d] %s: la suma de ítems (%s) no cuadra con el subtotal (%s)",
                            n, df.at[0, "numero_factura"], check["suma_items"], check["subtotal_pdf"])
        except Exception as exc:
            row.update(estado="error", error=f"{type(exc).__name__}: {exc}")
            log.error("[%d] %s… %s", n, cufe[:12], row["error"])
        row["tiempo_s"] = round(time.perf_counter() - t0, 3)
        metrics.append(row)
        if row["estado"] != "error":
            log.info("[%d/%d] %-12s %d ítem/s en %.2fs (subtotal %s)", n, len(items), row["numero_factura"],
                     row["items"], row["tiempo_s"], "OK" if row["subtotal_ok"] else "NO CUADRA")

    total = time.perf_counter() - t_run
    if frames:
        pd.concat(frames, ignore_index=True).to_csv(args.out / "facturas.csv", index=False, encoding="utf-8")
    pd.DataFrame(metrics).to_csv(args.out / "metrics_extraccion.csv", index=False, encoding="utf-8")

    ok = sum(m["estado"] == "ok" for m in metrics)
    log.info("=" * 60)
    log.info("Facturas: %d ok / %d | Ítems: %d | Tiempo total: %.2fs (%.3fs por factura)",
             ok, len(metrics), sum(len(f) for f in frames), total, total / max(len(metrics), 1))
    log.info("CSV: %s/ y %s", csv_dir, args.out / "facturas.csv")
    return 0 if ok == len(metrics) else 1


if __name__ == "__main__":
    raise SystemExit(main())
