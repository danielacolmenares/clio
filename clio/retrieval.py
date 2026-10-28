"""Passo 5 di 7 – Retrieval e valutazione sul Golden Set.

Per ogni domanda del Golden Set della KB:
  1. embedding della domanda (stesso modello dei chunk);
  2. ricerca dei top-K chunk nella collection della KB;
  3. confronto con ``documento_atteso`` / ``sezione_attesa``;
  4. esito:
       OK        match esatto (documento + sezione) al primo posto
       PARZIALE  documento corretto (o match esatto) nei top-K, non al primo posto
       FALLITO   documento atteso assente dai top-K
       ERRORE    eccezione durante la domanda

Metriche: hit rate Top-1/Top-3/Top-K sul match esatto e, come diagnostica,
sul solo documento.

Il Golden Set non viene mai modificato: e' il riferimento congelato. Ogni
esecuzione produce un CSV di risultati separato
(report/retrieval_results_<kb>_<timestamp>.csv) e un report leggibile, cosi'
esecuzioni con modelli o parametri diversi restano confrontabili.

Formato golden_set.csv (intestazione):
    id,domanda,documento_atteso,sezione_attesa,risposta_attesa,tipo_test,difficolta,tipo_risposta

``sezione_attesa`` accetta: "art. 33" / "articolo 33" / "33", "allegato III",
"considerando 39" (corrisponde al blocco che lo contiene), "3.1" (profilo
sezioni_numerate). Un valore che inizia con "(" indica che la sezione non e'
verificabile: si controlla solo il documento.
"""

from __future__ import annotations

import csv
import re
import time
from pathlib import Path

from . import clienti, config
from .config import KB
from .esecuzione import Esecuzione, percentuale

TOP_K = 5
SEZIONE_NON_VERIFICABILE_PREFIX = "("
COLONNE_GOLDEN = ["id", "domanda", "documento_atteso", "sezione_attesa", "risposta_attesa",
                  "tipo_test", "difficolta", "tipo_risposta"]
COLONNE_NEGATIVE = ["id", "domanda", "nota"]


# ----------------------------------------------------------------------
# Ricerca (usata anche dai passi 6 e 7)
# ----------------------------------------------------------------------

def cerca(kb: KB, domanda: str, top_k: int) -> tuple[list[dict], float, float]:
    """Restituisce (risultati Qdrant, tempo embedding, tempo ricerca)."""
    t0 = time.perf_counter()
    vettore = clienti.ollama_embed(domanda)
    t1 = time.perf_counter()
    risultati = clienti.qdrant_cerca(kb.collection, vettore, top_k)
    return risultati, t1 - t0, time.perf_counter() - t1


def filtra_per_score(risultati: list[dict], min_score: float | None) -> list[dict]:
    """Nessun filtro se min_score e' None (default)."""
    if min_score is None:
        return risultati
    return [r for r in risultati if (r.get("score") or 0) >= min_score]


# ----------------------------------------------------------------------
# Confronto con il Golden Set
# ----------------------------------------------------------------------

def chiave_sezione(valore: str | None) -> tuple:
    s = re.sub(r"\s+", " ", (valore or "").strip().lower())
    m = re.match(r"^(?:art\.?|articolo)\s*(\d+)\s*([a-z]*)\b", s)
    if m:
        return ("art", m.group(1) + m.group(2))
    m = re.match(r"^(?:all\.?|allegato)\s*([ivxlc]*)$", s)
    if m:
        return ("allegato", m.group(1))
    m = re.match(r"^considerando\s*(\d+)(?:\s*[-–]\s*(\d+))?$", s)
    if m:
        da = int(m.group(1))
        return ("considerando", da, int(m.group(2) or da))
    m = re.match(r"^(?:sez\.?|sezione)?\s*(\d+(?:\.\d+)*)\.?$", s)
    if m:
        return ("num", m.group(1))
    return ("testo", s)


def sezione_corrisponde(attesa: str, sezione_chunk: str | None) -> bool:
    a, c = chiave_sezione(attesa), chiave_sezione(sezione_chunk)
    if a[0] == "considerando" and c[0] == "considerando":
        return c[1] <= a[1] and a[2] <= c[2]
    if a[0] in ("art", "num") and c[0] in ("art", "num"):
        return a[1] == c[1]
    return a == c


def documento_corrisponde(atteso: str, payload: dict) -> bool:
    atteso = atteso.strip().lower()
    candidati = {str(payload.get("documento") or "").lower(),
                 Path(str(payload.get("file_origine") or "")).stem.lower()}
    return atteso in candidati


def valuta(riga: dict, risultati: list[dict]) -> dict:
    documento_atteso = (riga.get("documento_atteso") or "").strip()
    sezione_attesa = (riga.get("sezione_attesa") or "").strip()
    verifica_sezione = bool(sezione_attesa) and not sezione_attesa.startswith(SEZIONE_NON_VERIFICABILE_PREFIX)

    rank_doc = rank_esatto = score_doc = score_esatto = None
    for i, r in enumerate(risultati, start=1):
        p = r.get("payload", {}) or {}
        doc_ok = documento_corrisponde(documento_atteso, p)
        sez_ok = (not verifica_sezione) or sezione_corrisponde(sezione_attesa, p.get("sezione"))
        if doc_ok and rank_doc is None:
            rank_doc, score_doc = i, r.get("score")
        if doc_ok and sez_ok and rank_esatto is None:
            rank_esatto, score_esatto = i, r.get("score")

    if rank_esatto == 1:
        esito = "OK"
    elif rank_esatto is not None or rank_doc is not None:
        esito = "PARZIALE"
    else:
        esito = "FALLITO"

    top1 = (risultati[0].get("payload", {}) or {}) if risultati else {}
    return {
        "id": riga.get("id"), "domanda": riga.get("domanda"),
        "documento_atteso": documento_atteso, "sezione_attesa": sezione_attesa,
        "verifica_sezione": verifica_sezione,
        "top1_citazione": top1.get("citazione"),
        "top1_score": round(risultati[0].get("score") or 0, 4) if risultati else None,
        "rank_documento": rank_doc,
        "score_documento": round(score_doc, 4) if score_doc is not None else None,
        "rank_esatto": rank_esatto,
        "score_esatto": round(score_esatto, 4) if score_esatto is not None else None,
        "esito": esito,
        "risultati": risultati,
    }


def hit_rate(valutazioni: list[dict], k: int, esatto: bool = True) -> float:
    campo = "rank_esatto" if esatto else "rank_documento"
    if not valutazioni:
        return 0.0
    colpiti = sum(1 for v in valutazioni if v.get(campo) is not None and v[campo] <= k)
    return 100 * colpiti / len(valutazioni)


def leggi_set(percorso: Path, obbligatorie: list[str]) -> list[dict]:
    """Legge un CSV di domande in sola lettura, verificando le colonne
    obbligatorie. Le righe senza domanda vengono ignorate."""
    with open(percorso, encoding="utf-8", newline="") as f:
        lettore = csv.DictReader(f)
        mancanti = [c for c in obbligatorie if c not in (lettore.fieldnames or [])]
        if mancanti:
            raise ValueError(f"{percorso.name}: colonne mancanti: {', '.join(mancanti)}")
        return [r for r in lettore if (r.get("domanda") or "").strip()]


# ----------------------------------------------------------------------
# Esecuzione del passo
# ----------------------------------------------------------------------

COLONNE_RISULTATI = ["id", "domanda", "documento_atteso", "sezione_attesa", "top1_citazione",
                     "top1_score", "rank_documento", "score_documento", "rank_esatto",
                     "score_esatto", "tempo_embedding", "tempo_qdrant", "esito", "errore"]


def esegui(kb: KB, top_k: int = TOP_K) -> int:
    with Esecuzione("retrieval", kb.nome) as es:
        log = es.logger
        golden = kb.golden_set
        if golden is None:
            log.error("La KB '%s' non dichiara un Golden Set (kb.json: golden_set).", kb.nome)
            return 1
        if not golden.exists():
            log.error("Golden Set non trovato: %s", golden)
            return 1
        domande = leggi_set(golden, COLONNE_GOLDEN[:4])
        if not domande:
            log.warning("Golden Set vuoto: %s", golden)
            return 0
        log.info("Domande: %s | collection: %s | top-k: %s", len(domande), kb.collection, top_k)

        valutazioni = []
        inizio = time.perf_counter()
        for riga in domande:
            try:
                risultati, t_emb, t_q = cerca(kb, riga["domanda"], top_k)
                v = valuta(riga, risultati)
                v.update(tempo_embedding=round(t_emb, 3), tempo_qdrant=round(t_q, 3), errore=None)
            except Exception as exc:
                log.error("%s: %s", riga.get("id"), exc)
                v = valuta(riga, [])
                v.update(esito="ERRORE", errore=str(exc), tempo_embedding=None, tempo_qdrant=None)
            log.info("%s - %s (rank doc=%s, rank esatto=%s)", v["id"], v["esito"],
                     v["rank_documento"], v["rank_esatto"])
            valutazioni.append(v)
        durata = time.perf_counter() - inizio

        csv_path = es.percorso_report("retrieval_results", "csv")
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=COLONNE_RISULTATI, extrasaction="ignore")
            w.writeheader()
            w.writerows(valutazioni)

        righe = scrivi_report(es, kb, valutazioni, top_k, durata, csv_path)
        print("\n".join(righe[:32]))
        return 0


def scrivi_report(es: Esecuzione, kb: KB, valutazioni: list[dict], top_k: int,
                  durata: float, csv_path: Path) -> list[str]:
    n = len(valutazioni)
    conta = {e: sum(v["esito"] == e for v in valutazioni) for e in ("OK", "PARZIALE", "FALLITO", "ERRORE")}
    ks = sorted({1, 3, top_k})
    righe = es.intestazione("RETRIEVAL - VALUTAZIONE SU GOLDEN SET")
    righe += [
        "CONFIGURAZIONE", "-" * 70,
        f"Modello embedding: {config.modello_embedding()}",
        f"Collection: {kb.collection}", f"Top-K: {top_k}",
        f"Risultati dettagliati: {csv_path.name}", "",
        "HIT RATE (documento + sezione)", "-" * 70,
        *[f"Top-{k}: {hit_rate(valutazioni, k):.1f}%" for k in ks], "",
        "HIT RATE (solo documento, diagnostica)", "-" * 70,
        *[f"Top-{k}: {hit_rate(valutazioni, k, esatto=False):.1f}%" for k in ks], "",
        "ESITI", "-" * 70,
        f"Domande: {n}",
        f"OK (match esatto al primo posto): {conta['OK']} ({percentuale(conta['OK'], n)})",
        f"PARZIALE (trovato ma non al primo posto): {conta['PARZIALE']} ({percentuale(conta['PARZIALE'], n)})",
        f"FALLITO (documento assente dai top-{top_k}): {conta['FALLITO']} ({percentuale(conta['FALLITO'], n)})",
        f"ERRORE: {conta['ERRORE']}",
        f"Tempo totale: {durata:.2f} s", "",
        "=" * 70, "DETTAGLIO", "=" * 70,
    ]
    for v in valutazioni:
        righe += [
            "", f"[{v['id']}] {v['domanda']}",
            f"  Atteso: {v['documento_atteso']}, {v['sezione_attesa']}"
            + ("" if v["verifica_sezione"] else " (sezione non verificata)"),
            f"  Top-1: [{v['top1_citazione']}] score={v['top1_score']}",
            f"  Rank documento: {v['rank_documento']} | rank esatto: {v['rank_esatto']}",
            f"  Esito: {v['esito']}" + (f" ({v['errore']})" if v.get("errore") else ""),
        ]
        if v["esito"] not in ("OK", "ERRORE"):
            for i, r in enumerate(v["risultati"], start=1):
                p = r.get("payload", {}) or {}
                righe.append(f"    {i}. [{p.get('citazione')}] score={round(r.get('score') or 0, 4)}")
    report = es.percorso_report("retrieval_report")
    report.write_text("\n".join(righe), encoding="utf-8")
    righe.append(f"Report: {report}")
    return righe
