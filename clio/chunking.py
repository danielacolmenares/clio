"""Passo 2 di 7 – Chunking (testo -> chunk JSON con metadati).

Cosa fa:
    1. legge kb/<nome>/testo_estratto/*.txt (con marcatori [PAGINA N])
    2. segmenta ogni documento con il profilo configurato per la KB o per
       il singolo documento (``sezioni_numerate`` o ``legislativo_ue``)
    3. salva un JSON per chunk in kb/<nome>/chunk/<documento>/chunk_NNNN.json
    4. scrive report e log

Principio: il campo ``testo`` (destinato all'embedding) contiene solo
segnale semantico: intestazione (titolo, eventuale percorso Capo/Sezione)
e contenuto. Numeri di pagina e intestazioni di impaginazione non entrano
mai nel testo: la pagina e' un metadato, disponibile per citare la fonte.

Ogni chunk ha:
    id, documento (identificativo breve, es. "GDPR"), titolo_documento,
    codice, versione, profilo, tipo, sezione, titolo_sezione, citazione
    (es. "GDPR, art. 33"), livello, parent, path, pagine, testo, caratteri,
    hash (SHA-256 del testo) e, quando pertinenti, articolo, allegato,
    considerando, paragrafi, punti, lettere, parte.

L'output e' deterministico: stessi testi in ingresso -> stessi file JSON.
I chunk con testo identico (stesso hash) vengono salvati una sola volta.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from .config import KB
from .esecuzione import Esecuzione
from .profili import PROFILI
from .profili.base import RisultatoSegmentazione, Sezione, componi_testo


def risolvi_documento(kb: KB, stem: str, metadati: dict) -> tuple[str, str]:
    """(identificativo, titolo) del documento. Priorita' per l'id:
    kb.json "documenti" > kb.json "atti" (numero atto riconosciuto) > nome file."""
    conf = kb.opzioni["documenti"].get(stem, {})
    numero = metadati.get("numero_atto")
    doc_id = conf.get("id") or (kb.opzioni["atti"].get(numero) if numero else None) or stem
    titolo = conf.get("titolo") or metadati.get("titolo") or stem
    return doc_id, titolo


def costruisci_chunk(sezione: Sezione, metadati: dict, doc_id: str, titolo_doc: str,
                     profilo: str, indice_globale: int, file_origine: str) -> dict:
    intestazione = list(sezione.intestazione)
    if sezione.prefisso_documento and intestazione:
        intestazione[0] = f"{doc_id} – {intestazione[0]}"
    corpo = componi_testo(sezione.corpo)
    testo = ("\n".join(intestazione) + ("\n\n" + corpo if corpo else "")).strip()
    record = {
        "id": f"CH{indice_globale:06d}",
        "documento": doc_id,
        "titolo_documento": titolo_doc,
        "codice": metadati.get("codice"),
        "versione": metadati.get("versione"),
        "file_origine": file_origine,
        "profilo": profilo,
        "tipo": sezione.tipo,
        "sezione": sezione.sezione,
        "titolo_sezione": sezione.titolo,
        "citazione": f"{doc_id}, {sezione.etichetta}",
        "livello": sezione.livello,
        "parent": sezione.parent,
        "path": sezione.path,
        "pagine": sezione.pagine,
    }
    record.update(sezione.extra)
    record.update({
        "testo": testo,
        "caratteri": len(testo),
        "hash": hashlib.sha256(testo.encode("utf-8")).hexdigest(),
    })
    return record


def chunk_documento(kb: KB, path_txt: Path) -> tuple[RisultatoSegmentazione, str, str, str]:
    stem = path_txt.stem
    profilo = kb.profilo_documento(stem)
    risultato = PROFILI[profilo](path_txt.read_text(encoding="utf-8"), kb.opzioni)
    doc_id, titolo = risolvi_documento(kb, stem, risultato.metadati)
    return risultato, profilo, doc_id, titolo


def _report(es: Esecuzione, kb: KB, per_doc: list[dict], duplicati: int) -> list[str]:
    righe = es.intestazione("CHUNKING - REPORT")
    righe += [
        "RIEPILOGO", "-" * 70,
        f"Documenti processati:  {len(per_doc)}",
        f"Chunk totali:          {sum(len(d['chunk']) for d in per_doc)}",
        f"Chunk duplicati saltati: {duplicati}",
        f"Avvisi totali:         {sum(len(d['avvisi']) for d in per_doc)}",
        f"Output:                {kb.chunk}",
        "", "=" * 70, "DETTAGLIO PER DOCUMENTO", "=" * 70,
    ]
    for d in per_doc:
        righe += ["", f"File: {d['file']}", f"Documento: {d['documento']}",
                  f"Titolo: {d['titolo']}", f"Profilo: {d['profilo']}",
                  f"Statistiche: {json.dumps(d['statistiche'], ensure_ascii=False)}",
                  f"Chunk: {len(d['chunk'])}"]
        if d["chunk"]:
            lun = [c["caratteri"] for c in d["chunk"]]
            righe += [f"Chunk piu' lungo: {max(lun)} caratteri",
                      f"Chunk piu' corto: {min(lun)} caratteri", "Elenco:"]
            for c in d["chunk"]:
                pag = ",".join(str(p) for p in c["pagine"]) or "?"
                parte = f" parte {c['parte'][0]}/{c['parte'][1]}" if c.get("parte") else ""
                righe.append(f"  - [{c['citazione']}]{parte} {c['titolo_sezione'][:70]} "
                             f"({c['caratteri']} car., pag. {pag})")
        if d["avvisi"]:
            righe.append("Avvisi:")
            righe += [f"  - {a}" for a in d["avvisi"]]
        righe.append("-" * 70)
    return righe


def esegui(kb: KB) -> int:
    with Esecuzione("chunking", kb.nome) as es:
        log = es.logger
        if not kb.testo_estratto.is_dir():
            log.error("Cartella non trovata: %s. Eseguire prima il passo 1 (parse).", kb.testo_estratto)
            return 1
        file_txt = sorted(kb.testo_estratto.glob("*.txt"))
        if not file_txt:
            log.warning("Nessun .txt in %s. Eseguire prima il passo 1 (parse).", kb.testo_estratto)
            return 0

        # Ricostruzione completa: nessun chunk residuo di esecuzioni
        # precedenti puo' finire nei passi successivi.
        if kb.chunk.exists():
            shutil.rmtree(kb.chunk)
        kb.chunk.mkdir(parents=True)

        indice_globale = 1
        hash_visti: dict[str, str] = {}
        duplicati = 0
        per_doc = []
        for path_txt in file_txt:
            log.info("Elaborazione: %s", path_txt.name)
            risultato, profilo, doc_id, titolo = chunk_documento(kb, path_txt)
            cartella = kb.chunk / path_txt.stem
            cartella.mkdir(parents=True, exist_ok=True)
            generati = []
            for sezione in risultato.sezioni:
                chunk = costruisci_chunk(sezione, risultato.metadati, doc_id, titolo,
                                         profilo, indice_globale, path_txt.name)
                if chunk["hash"] in hash_visti:
                    duplicati += 1
                    risultato.avvisi.append(
                        f"Chunk [{chunk['citazione']}] identico a {hash_visti[chunk['hash']]}: saltato.")
                    continue
                hash_visti[chunk["hash"]] = chunk["id"]
                destinazione = cartella / f"chunk_{len(generati) + 1:04d}.json"
                destinazione.write_text(json.dumps(chunk, ensure_ascii=False, indent=2), encoding="utf-8")
                generati.append(chunk)
                indice_globale += 1
            for a in risultato.avvisi:
                log.warning("%s - %s", path_txt.stem, a)
            log.info("%s: %s chunk (documento '%s', profilo %s)", path_txt.stem, len(generati), doc_id, profilo)
            per_doc.append({"file": path_txt.name, "documento": doc_id, "titolo": titolo,
                            "profilo": profilo, "chunk": generati, "avvisi": risultato.avvisi,
                            "statistiche": risultato.statistiche})

        report = es.percorso_report("chunking_report")
        righe = _report(es, kb, per_doc, duplicati)
        report.write_text("\n".join(righe), encoding="utf-8")
        print("\n".join(righe[:11]))
        print(f"Report: {report}")
        return 0
