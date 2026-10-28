"""Configurazione centralizzata: impostazioni da variabili d'ambiente e
registro delle Knowledge Base (KB).

Principio: una sola pipeline, tante KB. Il codice dei passi 1-7 non sa
quante KB esistono; lo sa solo questo modulo. Una KB e' una cartella
``kb/<nome>/`` con almeno una sottocartella ``corpus/`` oppure un file
``kb.json``. Aggiungere una KB significa creare una cartella, non
modificare il codice.

Struttura di una KB::

    kb/<nome>/
        kb.json              opzionale, vedi DEFAULT_KB
        corpus/              PDF/DOCX sorgente
        golden_set.csv       domande con risposta attesa (mai modificato)
        negative_set.csv     domande fuori dominio (mai modificato)
        testo_estratto/      output passo 1   (derivato, non versionato)
        chunk/               output passo 2   (derivato, non versionato)
        embedding/           output passo 3   (derivato, non versionato)

Variabili d'ambiente (con valori di default per l'uso locale):

    CLIO_BASE_DIR     radice del progetto (default: cartella del repository)
    OLLAMA_URL        http://127.0.0.1:11434
    QDRANT_URL        http://127.0.0.1:6333
    CLIO_EMBED_MODEL  bge-m3
    CLIO_EMBED_DIM    1024
    CLIO_LLM_MODEL    gemma3:4b
    CLIO_MIN_SCORE    vuoto = nessun filtro (default)
    CLIO_UTENTI_FILE  file utenti per l'interfaccia web
                      (default: <base>/utenti.htpasswd, escluso da git)
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

PROFILI_DISPONIBILI = ("sezioni_numerate", "legislativo_ue")

# Valori di default di kb.json. Ogni chiave puo' essere sovrascritta.
DEFAULT_KB = {
    # Profilo di riconoscimento dei titoli usato dal passo 2.
    "profilo_chunking": "sezioni_numerate",
    # Il passo 1 aggiunge in coda alla pagina una versione "colonna: valore"
    # delle tabelle trovate da pdfplumber (il testo semplice resta).
    "estrai_tabelle": True,
    # Profilo legislativo_ue: considerando raggruppati per blocco.
    "considerando_per_chunk": 10,
    # Oltre questa lunghezza un articolo/allegato viene suddiviso.
    "max_caratteri_chunk": 6000,
    # Nomi dei file golden/negative set, relativi alla cartella della KB.
    "golden_set": "golden_set.csv",
    "negative_set": "negative_set.csv",
    # Identificativo e titolo per documento, chiave = nome file senza
    # estensione. Es. {"reg_2016_679": {"id": "GDPR", "titolo": "..."}}
    "documenti": {},
    # Solo legislativo_ue: identificativo per numero di atto riconosciuto
    # nel testo. Es. {"2016/679": "GDPR"}. Ha priorita' piu' bassa di
    # "documenti" ma piu' alta del nome del file.
    "atti": {},
}

RE_NOME_KB = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class ErroreConfigurazione(ValueError):
    """Errore di configurazione con messaggio leggibile dall'utente."""


# ----------------------------------------------------------------------
# Impostazioni da ambiente (lette a ogni chiamata: i test possono
# modificarle con monkeypatch.setenv)
# ----------------------------------------------------------------------

def base_dir() -> Path:
    valore = os.environ.get("CLIO_BASE_DIR")
    if valore:
        return Path(valore).expanduser().resolve()
    return Path(__file__).resolve().parent.parent


def ollama_url() -> str:
    return os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")


def qdrant_url() -> str:
    return os.environ.get("QDRANT_URL", "http://127.0.0.1:6333").rstrip("/")


def modello_embedding() -> str:
    return os.environ.get("CLIO_EMBED_MODEL", "bge-m3")


def dimensione_embedding() -> int:
    valore = os.environ.get("CLIO_EMBED_DIM", "1024")
    try:
        return int(valore)
    except ValueError as exc:
        raise ErroreConfigurazione(f"CLIO_EMBED_DIM non valido: '{valore}'") from exc


def modello_llm() -> str:
    return os.environ.get("CLIO_LLM_MODEL", "gemma3:4b")


def min_score() -> float | None:
    """Soglia minima di similarita'. Di default nessun filtro: lo score da
    solo non separa in modo affidabile domande pertinenti e fuori dominio;
    il rifiuto e' affidato all'istruzione nel prompt. La soglia resta
    disponibile per esperimenti misurati con Golden Set e Negative Set."""
    valore = os.environ.get("CLIO_MIN_SCORE", "").strip()
    if not valore:
        return None
    try:
        return float(valore)
    except ValueError as exc:
        raise ErroreConfigurazione(f"CLIO_MIN_SCORE non valido: '{valore}'") from exc


def file_utenti() -> Path:
    valore = os.environ.get("CLIO_UTENTI_FILE")
    return Path(valore).expanduser() if valore else base_dir() / "utenti.htpasswd"


def kb_root() -> Path:
    return base_dir() / "kb"


def report_dir() -> Path:
    return base_dir() / "report"


def log_dir() -> Path:
    return base_dir() / "log"


def sessioni_dir() -> Path:
    return base_dir() / "sessioni"


# ----------------------------------------------------------------------
# Registro KB
# ----------------------------------------------------------------------

@dataclass
class KB:
    nome: str
    dir: Path
    opzioni: dict = field(default_factory=dict)

    @property
    def collection(self) -> str:
        return f"clio_{self.nome.replace('-', '_')}"

    @property
    def corpus(self) -> Path:
        return self.dir / "corpus"

    @property
    def testo_estratto(self) -> Path:
        return self.dir / "testo_estratto"

    @property
    def chunk(self) -> Path:
        return self.dir / "chunk"

    @property
    def embedding(self) -> Path:
        return self.dir / "embedding"

    def _percorso_set(self, chiave: str) -> Path | None:
        nome = self.opzioni.get(chiave)
        return self.dir / nome if nome else None

    @property
    def golden_set(self) -> Path | None:
        return self._percorso_set("golden_set")

    @property
    def negative_set(self) -> Path | None:
        return self._percorso_set("negative_set")

    def profilo_documento(self, stem: str) -> str:
        doc = self.opzioni["documenti"].get(stem, {})
        return doc.get("profilo", self.opzioni["profilo_chunking"])

    def log_sessioni(self, giorno: date | None = None) -> Path:
        giorno = giorno or date.today()
        return sessioni_dir() / f"sessioni_{self.nome}_{giorno.strftime('%Y%m%d')}.jsonl"


def _leggi_kb_json(cartella: Path) -> dict:
    percorso = cartella / "kb.json"
    if not percorso.exists():
        return {}
    try:
        dati = json.loads(percorso.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ErroreConfigurazione(f"{percorso}: JSON non valido ({exc})") from exc
    if not isinstance(dati, dict):
        raise ErroreConfigurazione(f"{percorso}: atteso un oggetto JSON")
    sconosciute = sorted(set(dati) - set(DEFAULT_KB) - {"descrizione"})
    if sconosciute:
        raise ErroreConfigurazione(
            f"{percorso}: chiavi non riconosciute: {', '.join(sconosciute)}. "
            f"Chiavi ammesse: {', '.join(sorted(DEFAULT_KB))}, descrizione"
        )
    return dati


def elenco_kb() -> list[str]:
    """KB scoperte automaticamente: ogni sottocartella di kb/ che contiene
    corpus/ oppure kb.json."""
    radice = kb_root()
    if not radice.is_dir():
        return []
    return sorted(
        p.name for p in radice.iterdir()
        if p.is_dir() and ((p / "corpus").is_dir() or (p / "kb.json").exists())
    )


def get_kb(nome: str) -> KB:
    """Restituisce la KB richiesta o solleva un errore chiaro, invece di
    proseguire in silenzio su un percorso sbagliato."""
    if not nome or not RE_NOME_KB.match(nome):
        raise ErroreConfigurazione(
            f"Nome KB non valido: '{nome}'. Ammessi: minuscole, cifre, '-' e '_'."
        )
    disponibili = elenco_kb()
    if nome not in disponibili:
        elenco = ", ".join(disponibili) if disponibili else "(nessuna)"
        raise ErroreConfigurazione(
            f"KB '{nome}' non trovata in {kb_root()}. KB disponibili: {elenco}. "
            f"Una KB e' una cartella kb/<nome>/ con corpus/ oppure kb.json."
        )
    cartella = kb_root() / nome
    opzioni = dict(DEFAULT_KB)
    opzioni.update(_leggi_kb_json(cartella))

    profili = {opzioni["profilo_chunking"]}
    profili |= {d.get("profilo") for d in opzioni["documenti"].values() if d.get("profilo")}
    for profilo in profili:
        if profilo not in PROFILI_DISPONIBILI:
            raise ErroreConfigurazione(
                f"KB '{nome}': profilo di chunking '{profilo}' sconosciuto. "
                f"Profili disponibili: {', '.join(PROFILI_DISPONIBILI)}"
            )
    return KB(nome=nome, dir=cartella, opzioni=opzioni)
