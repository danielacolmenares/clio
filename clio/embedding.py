"""Passo 3 di 7 – Embedding (chunk -> vettori).

Legge i JSON del passo 2 (kb/<nome>/chunk/), genera l'embedding del campo
``testo`` con Ollama (default bge-m3), verifica la dimensione attesa
(default 1024) e salva record chunk + vettore in kb/<nome>/embedding/,
mantenendo la stessa struttura di cartelle. Report e log per esecuzione.

Un vettore di dimensione inattesa e' un errore: il record non viene
salvato e il passo termina con codice 2.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from . import clienti, config
from .config import KB
from .esecuzione import Esecuzione


class DimensioneErrata(ValueError):
    pass


def vettorizza(testo: str, modello: str, dimensione_attesa: int) -> list[float]:
    vettore = clienti.ollama_embed(testo, modello)
    if len(vettore) != dimensione_attesa:
        raise DimensioneErrata(
            f"Dimensione embedding inattesa: {len(vettore)} (attesa: {dimensione_attesa})."
        )
    return vettore


def elabora_record(record: dict, modello: str, dimensione: int) -> dict:
    testo = record.get("testo")
    if not isinstance(testo, str) or not testo.strip():
        raise ValueError("Campo 'testo' assente o vuoto.")
    vettore = vettorizza(testo, modello, dimensione)
    uscita = dict(record)
    uscita["embedding_model"] = modello
    uscita["embedding_dimension"] = len(vettore)
    uscita["embedding"] = vettore
    return uscita


def esegui(kb: KB) -> int:
    modello = config.modello_embedding()
    dimensione = config.dimensione_embedding()
    with Esecuzione("embedding", kb.nome) as es:
        log = es.logger
        log.info("Modello: %s | dimensione attesa: %s | Ollama: %s", modello, dimensione, config.ollama_url())
        if not kb.chunk.is_dir():
            log.error("Cartella non trovata: %s. Eseguire prima il passo 2 (chunk).", kb.chunk)
            return 1
        file_json = sorted(kb.chunk.rglob("*.json"))
        if not file_json:
            log.warning("Nessun chunk in %s. Eseguire prima il passo 2 (chunk).", kb.chunk)
            return 0

        if kb.embedding.exists():
            shutil.rmtree(kb.embedding)   # niente record residui di run precedenti
        risultati = []
        inizio = time.perf_counter()
        for i, f in enumerate(file_json, start=1):
            rel = f.relative_to(kb.chunk)
            esito = {"file": str(rel), "id": None, "citazione": None, "stato": "ERRORE",
                     "durata": None, "errore": None}
            try:
                record = json.loads(f.read_text(encoding="utf-8"))
                esito["id"], esito["citazione"] = record.get("id"), record.get("citazione")
                t0 = time.perf_counter()
                uscita = elabora_record(record, modello, dimensione)
                esito["durata"] = time.perf_counter() - t0
                dest = kb.embedding / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(json.dumps(uscita, ensure_ascii=False), encoding="utf-8")
                esito["stato"] = "OK"
                log.info("[%s/%s] %s ok (%.2fs)", i, len(file_json), record.get("id"), esito["durata"])
            except Exception as exc:
                esito["errore"] = str(exc)
                log.error("[%s/%s] %s: %s", i, len(file_json), rel, exc)
            risultati.append(esito)

        durata = time.perf_counter() - inizio
        ok = [r for r in risultati if r["stato"] == "OK"]
        tempi = [r["durata"] for r in ok]
        righe = es.intestazione("EMBEDDING - REPORT")
        righe += [
            "CONFIGURAZIONE", "-" * 70,
            f"Modello: {modello}", f"Endpoint Ollama: {config.ollama_url()}",
            f"Dimensione attesa: {dimensione}", "",
            "RIEPILOGO", "-" * 70,
            f"Record trovati: {len(risultati)}", f"Embedding creati: {len(ok)}",
            f"Errori: {len(risultati) - len(ok)}",
            f"Tempo totale: {durata:.2f} s",
            f"Tempo medio per record: {(sum(tempi) / len(tempi) if tempi else 0):.2f} s",
            "", "=" * 70, "ERRORI", "=" * 70,
        ]
        righe += [f"{r['file']} ({r['citazione']}): {r['errore']}" for r in risultati if r["stato"] != "OK"] or ["Nessuno"]
        report = es.percorso_report("embedding_report")
        report.write_text("\n".join(righe), encoding="utf-8")
        print("\n".join(righe[:18]))
        print(f"Report: {report}")
        return 2 if len(ok) != len(risultati) else 0


def leggi_record(cartella: Path) -> list[tuple[Path, dict]]:
    return [(f, json.loads(f.read_text(encoding="utf-8"))) for f in sorted(cartella.rglob("*.json"))]
