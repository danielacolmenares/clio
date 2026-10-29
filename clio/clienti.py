"""Client HTTP minimi verso Ollama e Qdrant (solo libreria standard).

Tutte le chiamate di rete della pipeline passano da ``http_json``: i test
la sostituiscono con monkeypatch e non toccano mai la rete.
"""

from __future__ import annotations

import json
import os
import logging
import time
import urllib.error
import urllib.request

from . import config

log = logging.getLogger("clio.clienti")

MAX_TENTATIVI = 3
TIMEOUT_EMBED = 120
# Su sola CPU una risposta puo' superare i 3 minuti: valore modificabile con CLIO_TIMEOUT_LLM.
TIMEOUT_LLM = float(os.environ.get("CLIO_TIMEOUT_LLM", "600"))
# Finestra di contesto del modello, in token. Va dichiarata: il valore
# predefinito di Ollama e' piu' piccolo del prompt con tre articoli lunghi, e
# in quel caso Ollama taglia l'INIZIO del prompt, cioe' proprio le regole.
# Modificabile con CLIO_NUM_CTX.
NUM_CTX = int(os.environ.get("CLIO_NUM_CTX", "8192"))
TIMEOUT_QDRANT = 60
NON_TROVATO = {"status": "not_found", "http_code": 404}


class ErroreServizio(RuntimeError):
    """Servizio esterno (Ollama/Qdrant) non raggiungibile o risposta non valida."""


def _attesa(tentativo: int) -> None:
    time.sleep(1.5 * tentativo)


def http_json(metodo: str, url: str, corpo: dict | None = None, timeout: float = 60,
              tentativi: int = MAX_TENTATIVI, consenti_404: bool = False) -> dict:
    """Chiamata JSON con retry. Con ``consenti_404`` un 404 non e' un
    errore (es. collection non ancora esistente) e restituisce NON_TROVATO."""
    data = json.dumps(corpo, ensure_ascii=False).encode("utf-8") if corpo is not None else None
    ultimo_errore: Exception | None = None
    for tentativo in range(1, tentativi + 1):
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}, method=metodo
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                testo = resp.read().decode("utf-8")
                return json.loads(testo) if testo.strip() else {}
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and consenti_404:
                return dict(NON_TROVATO)
            ultimo_errore = exc
            if 400 <= exc.code < 500:
                break  # errore del client: ripetere non serve
        except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as exc:
            ultimo_errore = exc
        log.warning("Tentativo %s/%s fallito: %s %s -> %s", tentativo, tentativi, metodo, url, ultimo_errore)
        if tentativo < tentativi:
            _attesa(tentativo)
    raise ErroreServizio(f"Chiamata fallita ({metodo} {url}): {ultimo_errore}")


# ----------------------------------------------------------------------
# Ollama
# ----------------------------------------------------------------------

def ollama_embed(testo: str, modello: str | None = None) -> list[float]:
    risposta = http_json(
        "POST", f"{config.ollama_url()}/api/embed",
        {"model": modello or config.modello_embedding(), "input": testo},
        timeout=TIMEOUT_EMBED,
    )
    embeddings = risposta.get("embeddings")
    if not isinstance(embeddings, list) or not embeddings:
        raise ErroreServizio("Risposta Ollama senza campo 'embeddings' valido.")
    vettore = embeddings[0]
    if not isinstance(vettore, list) or not vettore:
        raise ErroreServizio("Embedding vuoto o non valido.")
    return vettore


def ollama_generate(prompt: str, modello: str | None = None) -> str:
    """Generazione deterministica: temperature 0 e seed fisso, altrimenti
    la stessa domanda puo' produrre risposte diverse tra due esecuzioni e
    il confronto tra run perde valore."""
    risposta = http_json(
        "POST", f"{config.ollama_url()}/api/generate",
        {
            "model": modello or config.modello_llm(),
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0, "seed": 42, "num_ctx": NUM_CTX},
        },
        timeout=TIMEOUT_LLM,
    )
    testo = (risposta.get("response") or "").strip()
    if not testo:
        raise ErroreServizio("Risposta LLM vuota.")
    return testo


# ----------------------------------------------------------------------
# Qdrant (API REST)
# ----------------------------------------------------------------------

def qdrant(metodo: str, path: str, corpo: dict | None = None, consenti_404: bool = False) -> dict:
    return http_json(metodo, f"{config.qdrant_url()}{path}", corpo,
                     timeout=TIMEOUT_QDRANT, consenti_404=consenti_404)


def qdrant_cerca(collection: str, vettore: list[float], limite: int) -> list[dict]:
    risposta = qdrant(
        "POST", f"/collections/{collection}/points/search",
        {"vector": vettore, "limit": limite, "with_payload": True},
    )
    return risposta.get("result", []) or []
