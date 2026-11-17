"""Cliente HTTP del catálogo VPFE de la DIAN (búsqueda por CUFE y descarga del PDF).

Flujo observado en el sitio (nov-2026):
  1. GET  /User/SearchDocument            -> formulario con __RequestVerificationToken y sitekey Turnstile.
  2. POST /User/SearchDocument            -> DocumentKey (CUFE) + SearchDocumentNit (obligatorio)
                                             + cf-turnstile-response. Responde una página con un
                                             formulario auto-enviado (#postForm: Id, Token, Captcha, Nit).
  3. POST /Document/ShowDocumentToPublic  -> página de detalle con el formulario de descarga.
  4. POST /Document/DownloadPDF           -> trackId + token + captcha (un SEGUNDO token Turnstile).
                                             Devuelve application/pdf cifrado con el NIT como contraseña.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from .captcha import CaptchaSolver

BASE = "https://catalogo-vpfe.dian.gov.co"
SEARCH_URL = f"{BASE}/User/SearchDocument"
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)


class DianError(RuntimeError):
    pass


@dataclass
class DownloadResult:
    pdf: bytes
    filename: str
    timings: dict[str, float] = field(default_factory=dict)


def _form_fields(form) -> dict[str, str]:
    return {i["name"]: i.get("value", "") for i in form.find_all("input") if i.get("name")}


def _validation_errors(soup: BeautifulSoup) -> str:
    errs = [e.get_text(strip=True) for e in soup.select(".field-validation-error, .validation-summary-errors")]
    return "; ".join(e for e in errs if e)


class DianClient:
    def __init__(self, solver: CaptchaSolver, timeout: float = 60):
        self.solver = solver
        self.timeout = timeout
        self.http = requests.Session()
        self.http.headers.update({"User-Agent": UA, "Accept-Language": "es-CO,es;q=0.9"})

    def _soup(self, resp: requests.Response) -> BeautifulSoup:
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")

    def download(self, cufe: str, nit: str, clock) -> DownloadResult:
        """Ejecuta el flujo completo. `clock(fase)` es un context manager que mide cada fase."""
        with clock("form_s"):
            soup = self._soup(self.http.get(SEARCH_URL, timeout=self.timeout))
            form = soup.find("form", id="search-document-form")
            widget = soup.select_one(".cf-turnstile")
            if not form or not widget:
                raise DianError("No se encontró el formulario de búsqueda o el captcha")
            sitekey = widget["data-sitekey"]

        with clock("captcha_s"):
            token = self.solver.solve(SEARCH_URL, sitekey)

        with clock("search_s"):
            data = _form_fields(form)
            data.update({"DocumentKey": cufe, "SearchDocumentNit": nit, "cf-turnstile-response": token})
            soup = self._soup(self.http.post(SEARCH_URL, data=data, timeout=self.timeout))
            post = soup.find("form", id="postForm")
            if not post:
                raise DianError(f"Búsqueda rechazada: {_validation_errors(soup) or 'sin formulario de resultado'}")
            detail_url = urljoin(BASE, post["action"])
            resp = self.http.post(detail_url, data=_form_fields(post), timeout=self.timeout)
            soup = self._soup(resp)
            dl = soup.find("form", action="/Document/DownloadPDF")
            if not dl:
                raise DianError("La página de detalle no tiene formulario de descarga (¿CUFE/NIT incorrectos?)")
            dl_widget = soup.select_one(".cf-turnstile")
            dl_sitekey = dl_widget["data-sitekey"] if dl_widget else sitekey

        with clock("captcha2_s"):
            token2 = self.solver.solve(resp.url, dl_sitekey)

        with clock("download_s"):
            data = _form_fields(dl)
            data["captcha"] = token2
            r = self.http.post(urljoin(BASE, dl["action"]), data=data, timeout=self.timeout * 1.5)
            r.raise_for_status()
            ctype = r.headers.get("Content-Type", "").lower()
            if "application/pdf" not in ctype or not r.content.startswith(b"%PDF"):
                raise DianError(f"La descarga no devolvió un PDF (Content-Type={ctype!r})")

        return DownloadResult(pdf=r.content, filename=f"{cufe}.pdf")
