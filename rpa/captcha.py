"""Resolución del captcha Cloudflare Turnstile mediante servicios externos."""
from __future__ import annotations

import os
import time
from typing import Protocol

import requests
from dotenv import load_dotenv


class CaptchaError(RuntimeError):
    pass


class CaptchaSolver(Protocol):
    def solve(self, url: str, sitekey: str) -> str: ...


class CapSolverSolver:
    """Cliente mínimo de la API de CapSolver para Turnstile (sin proxy)."""

    API = "https://api.capsolver.com"

    def __init__(self, api_key: str | None = None, timeout: float = 120, poll: float = 2):
        load_dotenv()
        self.api_key = api_key or os.getenv("capsolver_key") or os.getenv("CAPSOLVER_API_KEY")
        if not self.api_key:
            raise CaptchaError("Falta la API key de CapSolver (capsolver_key en .env)")
        self.timeout = timeout
        self.poll = poll
        self.http = requests.Session()

    def _post(self, path: str, payload: dict) -> dict:
        r = self.http.post(f"{self.API}/{path}", json={"clientKey": self.api_key, **payload}, timeout=30)
        r.raise_for_status()
        data = r.json()
        if data.get("errorId"):
            raise CaptchaError(f"{data.get('errorCode')}: {data.get('errorDescription')}")
        return data

    def balance(self) -> float:
        return float(self._post("getBalance", {}).get("balance", 0))

    def solve(self, url: str, sitekey: str) -> str:
        task = {"type": "AntiTurnstileTaskProxyLess", "websiteURL": url, "websiteKey": sitekey}
        data = self._post("createTask", {"task": task})
        if data.get("status") == "ready":
            return data["solution"]["token"]
        task_id = data["taskId"]
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            time.sleep(self.poll)
            res = self._post("getTaskResult", {"taskId": task_id})
            if res.get("status") == "ready":
                return res["solution"]["token"]
            if res.get("status") == "failed":
                raise CaptchaError(f"Tarea fallida: {res}")
        raise CaptchaError(f"Timeout de {self.timeout}s esperando a CapSolver")
