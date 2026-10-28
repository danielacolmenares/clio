"""Fixture comuni. Nessun test usa la rete: le connessioni socket sono
bloccate e i servizi Ollama/Qdrant sono simulati in memoria."""

from __future__ import annotations

import hashlib
import json
import math
import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clio import clienti  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class ReteBloccata(RuntimeError):
    pass


@pytest.fixture(autouse=True)
def blocca_rete(monkeypatch):
    def vietato(*args, **kwargs):
        raise ReteBloccata("Accesso alla rete vietato nei test")

    monkeypatch.setattr(socket.socket, "connect", vietato)
    monkeypatch.setattr(socket.socket, "connect_ex", vietato)
    monkeypatch.setattr(socket, "create_connection", vietato)
    monkeypatch.setattr(socket, "getaddrinfo", vietato)


@pytest.fixture(autouse=True)
def niente_attese(monkeypatch):
    monkeypatch.setattr(clienti, "_attesa", lambda tentativo: None)


@pytest.fixture
def base(tmp_path, monkeypatch):
    """Radice di progetto temporanea con una KB 'demo' (profilo legislativo)."""
    monkeypatch.setenv("CLIO_BASE_DIR", str(tmp_path))
    for var in ("OLLAMA_URL", "QDRANT_URL", "CLIO_EMBED_MODEL", "CLIO_EMBED_DIM",
                "CLIO_LLM_MODEL", "CLIO_MIN_SCORE", "CLIO_UTENTI_FILE"):
        monkeypatch.delenv(var, raising=False)
    kb = tmp_path / "kb" / "demo"
    (kb / "corpus").mkdir(parents=True)
    (kb / "kb.json").write_text(json.dumps({
        "profilo_chunking": "legislativo_ue",
        "estrai_tabelle": False,
        "atti": {"2099/123": "GDPR"},
    }), encoding="utf-8")
    return tmp_path


def vettore_finto(testo: str, dim: int = 1024) -> list[float]:
    """Vettore deterministico: bag of words con hashing, normalizzato."""
    v = [0.0] * dim
    for parola in testo.lower().split():
        h = int(hashlib.md5(parola.encode()).hexdigest(), 16)
        v[h % dim] += 1.0
    norma = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / norma for x in v]


class ServiziFinti:
    """Simula Ollama e Qdrant intercettando clienti.http_json."""

    def __init__(self, dim: int = 1024, risposta_llm: str = "Risposta [GDPR, art. 4]."):
        self.dim = dim
        self.risposta_llm = risposta_llm
        self.collezioni: dict[str, dict] = {}
        self.chiamate: list[tuple[str, str, dict | None]] = []
        self.prompt: list[str] = []
        self.risultati_ricerca: list[dict] | None = None   # forza la risposta della ricerca

    def __call__(self, metodo, url, corpo=None, timeout=60, tentativi=3, consenti_404=False):
        self.chiamate.append((metodo, url, corpo))
        if url.endswith("/api/embed"):
            return {"embeddings": [vettore_finto(corpo["input"], self.dim)]}
        if url.endswith("/api/generate"):
            self.prompt.append(corpo["prompt"])
            return {"response": self.risposta_llm}
        path = url.split("6333", 1)[-1]
        parti = path.strip("/").split("/")
        nome = parti[1] if len(parti) > 1 else None
        if metodo == "GET" and len(parti) == 2:
            if nome not in self.collezioni:
                if consenti_404:
                    return dict(clienti.NON_TROVATO)
                raise clienti.ErroreServizio("404")
            return {"result": {"status": "green"}}
        if metodo == "DELETE":
            self.collezioni.pop(nome, None)
            return {"result": True}
        if metodo == "PUT" and len(parti) == 2:
            self.collezioni[nome] = {"config": corpo, "punti": {}}
            return {"result": True}
        if metodo == "PUT" and parti[2].startswith("points"):
            for p in corpo["points"]:
                self.collezioni[nome]["punti"][p["id"]] = p
            return {"result": {"status": "completed"}}
        if metodo == "POST" and parti[-1] == "search":
            if self.risultati_ricerca is not None:
                return {"result": self.risultati_ricerca[: corpo["limit"]]}
            punti = self.collezioni[nome]["punti"].values()
            punteggi = sorted(
                ({"id": p["id"], "score": sum(a * b for a, b in zip(corpo["vector"], p["vector"])),
                  "payload": p["payload"]} for p in punti),
                key=lambda r: (-r["score"], r["id"]),
            )
            return {"result": punteggi[: corpo["limit"]]}
        raise AssertionError(f"Chiamata non prevista: {metodo} {url}")

    def conteggio(self, suffisso: str) -> int:
        return sum(1 for _, u, _ in self.chiamate if u.endswith(suffisso))


@pytest.fixture
def servizi(monkeypatch):
    finti = ServiziFinti()
    monkeypatch.setattr(clienti, "http_json", finti)
    return finti


@pytest.fixture
def testo_eurlex() -> str:
    return (FIXTURES / "eurlex_sintetico.txt").read_text(encoding="utf-8")
