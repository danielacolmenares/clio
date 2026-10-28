"""Infrastruttura comune ai passi: log e report per esecuzione.

Ogni esecuzione di un passo produce:
  - un log dettagliato in   log/<passo>_<kb>_<timestamp>.log
  - un report leggibile in  report/<passo>_report_<kb>_<timestamp>.txt
  - eventuali file di risultati in report/ con lo stesso timestamp

I nomi dei file includono KB e timestamp, quindi esecuzioni successive non
si sovrascrivono e restano confrontabili.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

from . import config

LOGGER_NAME = "clio"


class Esecuzione:
    """Contesto di una singola esecuzione di un passo su una KB."""

    def __init__(self, passo: str, nome_kb: str, suffisso: str = "", su_console: bool = True):
        self.passo = passo
        self.nome_kb = nome_kb
        self.timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.tag = "_".join(x for x in (nome_kb, suffisso, self.timestamp) if x)
        self.log_path = config.log_dir() / f"{passo}_{self.tag}.log"
        self.logger = logging.getLogger(f"{LOGGER_NAME}.{passo}")
        self._handlers: list[logging.Handler] = []
        self._su_console = su_console

    def __enter__(self) -> "Esecuzione":
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        formato = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        file_handler = logging.FileHandler(self.log_path, encoding="utf-8")
        file_handler.setFormatter(formato)
        self._handlers.append(file_handler)
        if self._su_console:
            console = logging.StreamHandler(sys.stdout)
            console.setFormatter(formato)
            console.setLevel(logging.INFO)
            self._handlers.append(console)
        for h in self._handlers:
            self.logger.addHandler(h)
        self.logger.setLevel(logging.DEBUG)
        self.logger.propagate = False
        self.logger.info("Avvio passo '%s' (KB: %s)", self.passo, self.nome_kb)
        return self

    def __exit__(self, *exc) -> None:
        self.logger.info("Fine passo '%s'. Log: %s", self.passo, self.log_path)
        for h in self._handlers:
            self.logger.removeHandler(h)
            h.close()
        self._handlers.clear()

    def percorso_report(self, nome: str, estensione: str = "txt") -> Path:
        percorso = config.report_dir() / f"{nome}_{self.tag}.{estensione}"
        percorso.parent.mkdir(parents=True, exist_ok=True)
        return percorso

    def intestazione(self, titolo: str, larghezza: int = 70) -> list[str]:
        return [
            "=" * larghezza,
            f"{titolo} (KB: {self.nome_kb})",
            f"Eseguito il: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "=" * larghezza,
            "",
        ]


def percentuale(parte: int, totale: int) -> str:
    return f"{100 * parte / totale:.1f}%" if totale else "n/d"
