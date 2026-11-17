"""Parte 1 – RPA: descarga los PDF de la representación gráfica de facturas DIAN por CUFE.

Uso:
    python rpa_descarga.py                       # usa cufes.txt y el NIT por defecto
    python rpa_descarga.py --nit 900156264 --force
El archivo de entrada admite una columna opcional con el NIT: "cufe,nit" por línea.
"""
from __future__ import annotations

import argparse
import csv
import logging
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from pypdf import PdfReader, PdfWriter

from rpa.captcha import CapSolverSolver
from rpa.dian import DianClient

DEFAULT_NIT = "900156264"
PRICE_PER_TOKEN_USD = 1.20 / 1000  # CapSolver, Turnstile (nov-2026)
PHASES = ["form_s", "captcha_s", "search_s", "captcha2_s", "download_s"]

log = logging.getLogger("rpa")


def read_inputs(path: Path, default_nit: str) -> list[tuple[str, str]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split(",")]
        rows.append((parts[0], parts[1] if len(parts) > 1 and parts[1] else default_nit))
    return rows


def unlock(src: Path, dst: Path, password: str) -> None:
    """Guarda una copia sin contraseña (el PDF de la DIAN viene cifrado con el NIT)."""
    reader = PdfReader(src)
    if reader.is_encrypted and not reader.decrypt(password):
        raise ValueError("Contraseña (NIT) incorrecta para el PDF")
    writer = PdfWriter(clone_from=reader)
    with dst.open("wb") as fh:
        writer.write(fh)


def pdf_valido(path: Path, password: str) -> bool:
    """True si el archivo existe, es un PDF y se puede abrir con la contraseña (NIT)."""
    try:
        if not path.is_file() or path.stat().st_size == 0:
            return False
        with path.open("rb") as fh:
            if fh.read(4) != b"%PDF":
                return False
        reader = PdfReader(path)
        if reader.is_encrypted and not reader.decrypt(password):
            return False
        return len(reader.pages) > 0
    except Exception:
        return False


def pendientes(items: list[tuple[str, str]], out: Path, force: bool = False) -> list[tuple[str, str]]:
    """CUFEs que necesitan descarga (y, por tanto, CapSolver). No hace llamadas de red.

    Si el PDF original ya es válido pero falta su copia sin clave, la regenera localmente.
    Pensada también para la Parte 2: comprobar antes de extraer que todos los PDF existen.
    """
    pdf_dir, unlocked_dir = out / "pdfs", out / "pdfs_sin_clave"
    unlocked_dir.mkdir(parents=True, exist_ok=True)
    todo = []
    for cufe, nit in items:
        pdf_path = pdf_dir / f"{cufe}.pdf"
        if force or not pdf_valido(pdf_path, nit):
            todo.append((cufe, nit))
            continue
        copia = unlocked_dir / f"{cufe}.pdf"
        if not pdf_valido(copia, nit):
            unlock(pdf_path, copia, nit)
            log.info("%s… copia sin clave regenerada desde el original (sin CapSolver)", cufe[:12])
    return todo


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", type=Path, default=Path("cufes.txt"))
    ap.add_argument("--nit", default=DEFAULT_NIT, help="NIT emisor/receptor (también es la clave del PDF)")
    ap.add_argument("--out", type=Path, default=Path("output"))
    ap.add_argument("--retries", type=int, default=3)
    ap.add_argument("--force", action="store_true", help="volver a descargar aunque el PDF exista")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    pdf_dir, unlocked_dir = args.out / "pdfs", args.out / "pdfs_sin_clave"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    unlocked_dir.mkdir(parents=True, exist_ok=True)

    items = read_inputs(args.input, args.nit)

    # Control de costo: se decide qué descargar ANTES de tocar la API de CapSolver.
    todo = set(pendientes(items, args.out, args.force))
    if not todo:
        log.info("Los %d PDF ya están descargados y son válidos: no se llama a CapSolver ni a la DIAN.", len(items))
        log.info("Use --force para volver a descargarlos.")
        return 0
    log.info("%d de %d CUFEs pendientes de descarga", len(todo), len(items))

    solver = CapSolverSolver()
    balance0 = solver.balance()
    log.info("Saldo CapSolver inicial: US$%.4f", balance0)

    metrics = []
    run_start = time.perf_counter()
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")

    for n, (cufe, nit) in enumerate(items, 1):
        short = cufe[:12]
        pdf_path = pdf_dir / f"{cufe}.pdf"
        row = {"n": n, "cufe": cufe, "nit": nit, "estado": "", "intentos": 0, "bytes": 0, "error": "",
               **{p: 0.0 for p in PHASES}, "total_s": 0.0, "inicio": datetime.now().isoformat(timespec="seconds")}

        if (cufe, nit) not in todo:
            log.info("[%d/%d] %s… ya descargado y válido, se omite", n, len(items), short)
            row.update(estado="omitido", bytes=pdf_path.stat().st_size)
            metrics.append(row)
            continue

        t0 = time.perf_counter()
        for attempt in range(1, args.retries + 1):
            row["intentos"] = attempt
            timings: dict[str, float] = {}

            @contextmanager
            def clock(phase):
                s = time.perf_counter()
                try:
                    yield
                finally:
                    timings[phase] = timings.get(phase, 0) + time.perf_counter() - s

            try:
                res = DianClient(solver).download(cufe, nit, clock)
                pdf_path.write_bytes(res.pdf)
                unlock(pdf_path, unlocked_dir / f"{cufe}.pdf", nit)
                row.update(estado="ok", bytes=len(res.pdf))  # se conserva el error de intentos previos
                for p in PHASES:
                    row[p] = round(row[p] + timings.get(p, 0), 2)
                break
            except Exception as exc:  # se registra y se reintenta
                row["error"] = f"{type(exc).__name__}: {exc}"
                for p in PHASES:
                    row[p] = round(row[p] + timings.get(p, 0), 2)
                log.warning("[%d/%d] %s… intento %d falló: %s", n, len(items), short, attempt, row["error"])
                time.sleep(2 * attempt)
        else:
            row["estado"] = "error"

        row["total_s"] = round(time.perf_counter() - t0, 2)
        log.info("[%d/%d] %s… %s en %.1fs (%d intento/s, %d bytes)",
                 n, len(items), short, row["estado"], row["total_s"], row["intentos"], row["bytes"])
        metrics.append(row)

    run_total = time.perf_counter() - run_start
    # Registro acumulativo: una fila por CUFE procesado en cada corrida (los omitidos no se registran).
    metrics_path = args.out / "metrics_rpa.csv"
    attempted = [{"run_id": run_id, **m} for m in metrics if m["estado"] != "omitido"]
    if attempted:
        new_file = not metrics_path.exists()
        with metrics_path.open("a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(attempted[0].keys()))
            if new_file:
                w.writeheader()
            w.writerows(attempted)

    done = [m for m in metrics if m["estado"] == "ok"]
    failed = [m for m in metrics if m["estado"] == "error"]
    balance1 = solver.balance()
    tokens = sum(m["intentos"] for m in metrics) * 2
    log.info("=" * 60)
    log.info("Descargados: %d | Omitidos: %d | Fallidos: %d | Total: %d",
             len(done), len(metrics) - len(done) - len(failed), len(failed), len(metrics))
    if done:
        avg = sum(m["total_s"] for m in done) / len(done)
        log.info("Tiempo total: %.1fs | Promedio por factura: %.1fs", run_total, avg)
        for p in PHASES:
            log.info("  %-11s promedio %.2fs", p, sum(m[p] for m in done) / len(done))
    log.info("Tokens captcha solicitados (aprox.): %d (~US$%.4f) | Saldo: US$%.4f -> US$%.4f",
             tokens, tokens * PRICE_PER_TOKEN_USD, balance0, balance1)
    log.info("Métricas: %s", metrics_path)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
