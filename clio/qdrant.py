"""Passo 4 di 7 – Caricamento su Qdrant.

Legge i record del passo 3 (kb/<nome>/embedding/), ricrea da zero la
collection della KB (``clio_<nome>``) e carica ogni chunk come punto:

  - id: numerico, derivato dall'id del chunk ("CH000001" -> 1)
  - vector: il campo "embedding"
  - payload: tutti gli altri campi (documento, sezione, citazione, path,
    pagine, testo, hash...), usati per filtri e per citare la fonte.

Ricreare la collection a ogni esecuzione evita di mescolare punti vecchi e
nuovi. Ogni KB ha la propria collection: il caricamento di una KB non
tocca mai i punti di un'altra.
"""

from __future__ import annotations

import re
import time

from . import clienti, config
from .config import KB
from .embedding import leggi_record
from .esecuzione import Esecuzione

DISTANZA = "Cosine"
DIMENSIONE_BATCH = 64


def ricrea_collection(collection: str, dimensione: int, log=None) -> None:
    esiste = clienti.qdrant("GET", f"/collections/{collection}", consenti_404=True)
    if esiste.get("status") != "not_found":
        if log:
            log.info("Collection '%s' esistente: eliminata per ricrearla pulita.", collection)
        clienti.qdrant("DELETE", f"/collections/{collection}")
    clienti.qdrant("PUT", f"/collections/{collection}",
                   {"vectors": {"size": dimensione, "distance": DISTANZA}})
    if log:
        log.info("Collection '%s' creata (size=%s, distance=%s).", collection, dimensione, DISTANZA)


def id_numerico(id_chunk: str | None) -> int:
    """Qdrant accetta id interi o UUID; l'id originale resta nel payload."""
    cifre = re.sub(r"\D", "", id_chunk or "")
    if not cifre:
        raise ValueError(f"Impossibile ricavare un id numerico da '{id_chunk}'.")
    return int(cifre)


def costruisci_punto(record: dict, dimensione: int) -> dict:
    vettore = record.get("embedding")
    if not isinstance(vettore, list) or len(vettore) != dimensione:
        trovata = len(vettore) if isinstance(vettore, list) else "N/A"
        raise ValueError(f"Embedding assente o di dimensione errata (attesa {dimensione}, trovata {trovata}).")
    return {
        "id": id_numerico(record.get("id")),
        "vector": vettore,
        "payload": {k: v for k, v in record.items() if k != "embedding"},
    }


def carica(collection: str, punti: list[dict]) -> None:
    for i in range(0, len(punti), DIMENSIONE_BATCH):
        clienti.qdrant("PUT", f"/collections/{collection}/points?wait=true",
                       {"points": punti[i:i + DIMENSIONE_BATCH]})


def esegui(kb: KB) -> int:
    dimensione = config.dimensione_embedding()
    with Esecuzione("qdrant", kb.nome) as es:
        log = es.logger
        log.info("Collection: %s | Qdrant: %s", kb.collection, config.qdrant_url())
        if not kb.embedding.is_dir():
            log.error("Cartella non trovata: %s. Eseguire prima il passo 3 (embed).", kb.embedding)
            return 1
        record = leggi_record(kb.embedding)
        if not record:
            log.warning("Nessun record in %s. Eseguire prima il passo 3 (embed).", kb.embedding)
            return 0

        modelli = sorted({r.get("embedding_model") for _, r in record})
        if len(modelli) > 1:
            log.error("Record generati con modelli diversi (%s): rieseguire il passo 3.", modelli)
            return 1

        inizio = time.perf_counter()
        punti, errori = [], []
        for f, r in record:
            try:
                punti.append(costruisci_punto(r, dimensione))
            except Exception as exc:
                errori.append(f"{f.relative_to(kb.embedding)}: {exc}")
                log.error("%s: %s", f.name, exc)
        id_doppi = len(punti) - len({p["id"] for p in punti})
        if id_doppi:
            log.error("%s id duplicati: rieseguire i passi 2 e 3.", id_doppi)
            return 1

        caricati = 0
        if punti:
            ricrea_collection(kb.collection, dimensione, log)
            try:
                carica(kb.collection, punti)
                caricati = len(punti)
            except Exception as exc:
                errori.append(f"Caricamento fallito: {exc}")
                log.error("Caricamento fallito: %s", exc)

        righe = es.intestazione("QDRANT - REPORT CARICAMENTO")
        righe += [
            "CONFIGURAZIONE", "-" * 70,
            f"Collection: {kb.collection}", f"Dimensione vettori: {dimensione}",
            f"Distanza: {DISTANZA}", f"Endpoint: {config.qdrant_url()}",
            f"Modello embedding: {modelli[0] if modelli else '-'}", "",
            "RIEPILOGO", "-" * 70,
            f"Record trovati: {len(record)}", f"Punti caricati: {caricati}",
            f"Errori: {len(errori)}", f"Tempo totale: {time.perf_counter() - inizio:.2f} s",
            "", "ERRORI", "-" * 70,
        ] + (errori or ["Nessuno"])
        report = es.percorso_report("qdrant_report")
        report.write_text("\n".join(righe), encoding="utf-8")
        print("\n".join(righe[:20]))
        print(f"Report: {report}")
        return 2 if errori else 0
