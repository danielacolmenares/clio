"""Passo 6 di 7 – Risposta con LLM, solo dal contesto recuperato.

Per ogni domanda (Golden Set e/o Negative Set):
  1. embedding della domanda e ricerca dei top-K chunk (passo 5);
  2. filtro MIN_SCORE opzionale (disattivo di default);
  3. se non resta nessun chunk: risposta deterministica, NESSUNA chiamata
     all'LLM;
  4. altrimenti prompt con i chunk numerati e separati, con l'istruzione di
     rispondere SOLO da quelli, citando la fonte nel formato [GDPR, art. 33],
     e di dichiarare "Non trovo questa informazione nei documenti." quando
     la risposta non e' nel contesto;
  5. le fonti vengono aggiunte alla risposta in modo deterministico, a
     partire dai metadati dei chunk (non dal testo del modello). Le
     citazioni inline del modello vengono confrontate con i chunk
     recuperati: quelle che non corrispondono sono segnalate;
  6. CSV per la valutazione umana (colonne di giudizio lasciate vuote) e
     riepilogo: per il Negative Set conta le domande correttamente
     dichiarate fuori dominio.

Due versioni di prompt: A (riferimento) e B (variante con regole piu'
dettagliate su domande in piu' parti, dettagli mancanti e citazioni). Una
variante sostituisce il riferimento solo se migliora i risultati misurati
su Golden Set, Negative Set e verifica di determinismo.
"""

from __future__ import annotations

import csv
import re
import time
import unicodedata

from . import clienti, config
from .config import KB
from .esecuzione import Esecuzione, percentuale
from .retrieval import COLONNE_GOLDEN, cerca, chiave_sezione, filtra_per_score, leggi_set

TOP_K = 3
FRASE_FUORI_DOMINIO = "Non trovo questa informazione nei documenti."

PROMPT_A = """Sei Clio, un assistente che risponde a domande sui documenti di una base documentale.

REGOLE OBBLIGATORIE:
- Rispondi ESCLUSIVAMENTE sulla base delle fonti fornite qui sotto.
- NON usare conoscenza esterna o generica, anche se la conosci.
- Se l'informazione richiesta NON e' presente nelle fonti fornite, rispondi
  esattamente "{frase}" invece di inventare o dedurre una risposta.
- Quando rispondi, cita sempre la fonte usando l'etichetta tra parentesi
  quadre indicata nell'intestazione della fonte (es. [GDPR, art. 33]).

FONTI RECUPERATE:

{blocchi}

DOMANDA: {domanda}

RISPOSTA:"""

PROMPT_B = """Sei Clio, un assistente che risponde a domande sui documenti di una base documentale.

REGOLE SUL CONTENUTO:
- Rispondi ESCLUSIVAMENTE sulla base delle fonti fornite qui sotto.
- NON usare conoscenza esterna o generica, anche se sarebbe corretta. Se
  un'informazione non compare nel testo delle fonti, non includerla.
- Se aggiungi un'informazione presa da una fonte diversa da quella
  principale, indicalo esplicitamente con la sua etichetta.

REGOLE SU DOMANDE CON PIU' PARTI:
- Rispondi a ciascuna parte della domanda in modo esplicito.
- Se una parte non trova risposta nelle fonti, dichiaralo solo per quella
  parte invece di sostituirla con un'altra informazione non richiesta.

REGOLE SU "INFORMAZIONE NON TROVATA":
- Usa la frase esatta "{frase}" SOLO quando l'intera domanda non trova
  risposta nelle fonti.
- Se manca solo un dettaglio, rispondi e aggiungi una nota separata, es.
  "Nota: la fonte non specifica [dettaglio]."

REGOLE DI CITAZIONE:
- Cita usando ESATTAMENTE l'etichetta tra parentesi quadre indicata
  nell'intestazione della fonte (es. [GDPR, art. 33]).
- NON inventare numeri di articolo, paragrafo o sezione che non compaiono
  nelle fonti.
- Non riportare punteggi, numeri di fonte ("FONTE 1") o altri dettagli
  tecnici del sistema di ricerca.

FONTI RECUPERATE:

{blocchi}

DOMANDA: {domanda}

RISPOSTA:"""

PROMPT = {"A": PROMPT_A, "B": PROMPT_B}


# ----------------------------------------------------------------------
# Prompt e citazioni
# ----------------------------------------------------------------------

def costruisci_prompt(domanda: str, chunk: list[dict], versione: str = "A") -> str:
    if versione not in PROMPT:
        raise ValueError(f"Versione prompt sconosciuta: '{versione}' (ammesse: {', '.join(PROMPT)})")
    blocchi = []
    for i, r in enumerate(chunk, start=1):
        p = r.get("payload", {}) or {}
        intest = f"FONTE {i} – [{p.get('citazione')}] {p.get('titolo_sezione') or ''}".rstrip()
        if p.get("titolo_documento"):
            intest += f"\nDocumento: {p.get('titolo_documento')}"
        if versione == "A":
            intest += f"\n(rilevanza: {round(r.get('score') or 0, 3)})"
        blocchi.append(f"{intest}\n{p.get('testo', '')}\n")
    return PROMPT[versione].format(frase=FRASE_FUORI_DOMINIO,
                                   blocchi="\n---\n".join(blocchi), domanda=domanda)


def _norm(testo: str) -> str:
    """Minuscole, senza accenti e punteggiatura, spazi singoli."""
    testo = unicodedata.normalize("NFKD", testo)
    testo = "".join(c for c in testo if not unicodedata.combining(c)).lower()
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", testo).split())


def dichiara_fuori_dominio(testo: str | None) -> bool:
    """True se il testo contiene la frase prevista per le domande fuori
    dominio (confronto tollerante su maiuscole, accenti e punteggiatura)."""
    return bool(testo) and _norm(FRASE_FUORI_DOMINIO) in _norm(testo)


def fonti_da_risultati(risultati: list[dict]) -> list[dict]:
    """Fonti deterministiche dai metadati dei chunk: una per citazione,
    nell'ordine di rilevanza, con le pagine unite."""
    fonti: dict[str, dict] = {}
    for r in risultati:
        p = r.get("payload", {}) or {}
        cit = p.get("citazione") or f"{p.get('documento')}, {p.get('sezione')}"
        if cit not in fonti:
            fonti[cit] = {"citazione": cit, "documento": p.get("documento"),
                          "sezione": p.get("sezione"), "titolo_sezione": p.get("titolo_sezione"),
                          "pagine": [], "score": round(r.get("score") or 0, 3)}
        fonti[cit]["pagine"] = sorted(set(fonti[cit]["pagine"]) | set(p.get("pagine") or []))
    return list(fonti.values())


def formatta_pagine(pagine: list[int]) -> str:
    if not pagine:
        return ""
    if len(pagine) == 1:
        return f"pag. {pagine[0]}"
    if pagine == list(range(pagine[0], pagine[-1] + 1)):
        return f"pagg. {pagine[0]}-{pagine[-1]}"
    return "pagg. " + ", ".join(str(p) for p in pagine)


def blocco_fonti(fonti: list[dict]) -> str:
    righe = ["Fonti:"]
    for f in fonti:
        dettagli = ", ".join(x for x in (f.get("titolo_sezione") or "", formatta_pagine(f["pagine"])) if x)
        righe.append(f"- [{f['citazione']}]" + (f" {dettagli}" if dettagli else ""))
    return "\n".join(righe)


RE_CITAZIONE = re.compile(r"\[([^\[\]\n,]{1,60}),\s*([^\[\]\n]{1,60})\]")


def estrai_citazioni(testo: str) -> list[str]:
    return [f"{d.strip()}, {s.strip()}" for d, s in RE_CITAZIONE.findall(testo or "")]


def _chiave_citazione(cit: str) -> tuple:
    doc, _, sez = cit.partition(",")
    sez = re.sub(r"\s*,?\s*(par\.|paragrafo|lett\.|lettera|punto)\s.*$", "", sez.strip(), flags=re.I)
    return (doc.strip().lower(), chiave_sezione(sez))


def citazioni_non_recuperate(testo: str, fonti: list[dict]) -> list[str]:
    """Citazioni inline del modello che non corrispondono a nessun chunk
    recuperato (indizio di citazione inventata)."""
    note = {_chiave_citazione(f["citazione"]) for f in fonti}
    note_considerando = [k for k in note if k[1][0] == "considerando"]
    fuori = []
    for c in estrai_citazioni(testo):
        k = _chiave_citazione(c)
        if k in note:
            continue
        if k[1][0] == "considerando" and any(
                n[0] == k[0] and n[1][1] <= k[1][1] and k[1][2] <= n[1][2] for n in note_considerando):
            continue
        if k[1][0] in ("art", "num") and any(n[0] == k[0] and n[1][0] in ("art", "num")
                                             and n[1][1] == k[1][1] for n in note):
            continue
        fuori.append(c)
    return fuori


# ----------------------------------------------------------------------
# Risposta a una domanda (usata anche dall'interfaccia web)
# ----------------------------------------------------------------------

def rispondi(kb: KB, domanda: str, top_k: int = TOP_K, versione_prompt: str = "A",
             modello: str | None = None, min_score: float | None = None) -> dict:
    inizio = time.perf_counter()
    risultati, t_emb, t_q = cerca(kb, domanda, top_k)
    selezionati = filtra_per_score(risultati, min_score)
    fonti = fonti_da_risultati(selezionati)
    esito = {
        "domanda": domanda, "risultati": selezionati, "fonti": fonti,
        "tempo_embedding": round(t_emb, 3), "tempo_qdrant": round(t_q, 3), "tempo_llm": None,
        "risposta_modello": None, "chiamata_llm": False,
        "citazioni_inline": [], "citazioni_non_recuperate": [],
    }
    if not selezionati:
        # Nessun contesto: risposta deterministica, l'LLM non viene chiamato.
        esito["fuori_dominio"] = True
        esito["risposta"] = FRASE_FUORI_DOMINIO
        esito["fonti"] = []
    else:
        prompt = costruisci_prompt(domanda, selezionati, versione_prompt)
        t0 = time.perf_counter()
        testo = clienti.ollama_generate(prompt, modello)
        esito["tempo_llm"] = round(time.perf_counter() - t0, 3)
        esito["chiamata_llm"] = True
        esito["risposta_modello"] = testo
        esito["fuori_dominio"] = dichiara_fuori_dominio(testo)
        esito["citazioni_inline"] = estrai_citazioni(testo)
        esito["citazioni_non_recuperate"] = citazioni_non_recuperate(testo, fonti)
        if esito["fuori_dominio"]:
            # Le fonti recuperate non sono state usate: non vanno mostrate
            # come se sostenessero la risposta.
            esito["fonti"] = []
            esito["risposta"] = testo
        else:
            esito["risposta"] = f"{testo}\n\n{blocco_fonti(fonti)}"
    esito["tempo_totale"] = round(time.perf_counter() - inizio, 3)
    return esito


# ----------------------------------------------------------------------
# Esecuzione batch del passo
# ----------------------------------------------------------------------

COLONNE_CSV = [
    "id", "domanda", "tipo_set", "documento_atteso", "sezione_attesa", "risposta_attesa", "nota",
    "risposta_generata", "fonti", "chunk_usati", "score_chunk_1", "score_chunk_2", "score_chunk_3",
    "citazioni_inline", "citazioni_non_recuperate", "dichiarato_fuori_dominio",
    "chiamata_llm_effettuata", "tempo_embedding", "tempo_qdrant", "tempo_llm", "tempo_totale",
    "modello_llm", "modello_embedding", "versione_prompt", "errore",
    # Colonne per la valutazione umana: lasciate vuote di proposito.
    "correttezza_contenuto", "aderenza_al_contesto", "completezza", "citazioni_corrette",
    "allucinazione_sospetta", "dichiarazione_informazione_insufficiente", "note_valutatore",
]


def _riga_csv(riga: dict, tipo_set: str, esito: dict | None, errore: str | None,
              modello: str, versione: str) -> dict:
    out = {c: "" for c in COLONNE_CSV}
    out.update({k: riga.get(k, "") for k in ("id", "domanda", "documento_atteso", "sezione_attesa",
                                               "risposta_attesa", "nota")})
    out.update(tipo_set=tipo_set, modello_llm=modello, modello_embedding=config.modello_embedding(),
               versione_prompt=versione, errore=errore or "")
    if esito:
        out.update(
            risposta_generata=esito["risposta_modello"] if esito["chiamata_llm"] else esito["risposta"],
            fonti=" | ".join(f"[{f['citazione']}]" for f in esito["fonti"]),
            chunk_usati=" | ".join(f"[{(r.get('payload') or {}).get('citazione')}] "
                                   f"({round(r.get('score') or 0, 3)})" for r in esito["risultati"]),
            citazioni_inline=" | ".join(esito["citazioni_inline"]),
            citazioni_non_recuperate=" | ".join(esito["citazioni_non_recuperate"]),
            dichiarato_fuori_dominio=esito["fuori_dominio"],
            chiamata_llm_effettuata=esito["chiamata_llm"],
            tempo_embedding=esito["tempo_embedding"], tempo_qdrant=esito["tempo_qdrant"],
            tempo_llm=esito["tempo_llm"] if esito["tempo_llm"] is not None else "",
            tempo_totale=esito["tempo_totale"],
        )
        for i, r in enumerate(esito["risultati"][:3], start=1):
            out[f"score_chunk_{i}"] = round(r.get("score") or 0, 4)
    return out


def riepilogo(righe: list[dict], tipo_set: str) -> list[str]:
    n = len(righe)
    errori = sum(1 for r in righe if r["errore"])
    llm = sum(1 for r in righe if r["chiamata_llm_effettuata"] is True)
    deterministiche = sum(1 for r in righe if r["chiamata_llm_effettuata"] is False)
    fuori = [r for r in righe if r["dichiarato_fuori_dominio"] is True]
    cit_dubbie = sum(1 for r in righe if r["citazioni_non_recuperate"])
    out = [
        f"SET: {tipo_set}", "-" * 70,
        f"Domande: {n}", f"Risposte generate dall'LLM: {llm}",
        f"Risposte deterministiche (nessun chunk, LLM non chiamato): {deterministiche}",
        f"Errori: {errori}",
        f"Risposte con citazioni inline non corrispondenti ai chunk recuperati: {cit_dubbie}",
    ]
    if tipo_set == "negative":
        validi = n - errori
        out.append(f"Dichiarate correttamente fuori dominio: {len(fuori)}/{validi} "
                   f"({percentuale(len(fuori), validi)})")
        sbagliate = [r["id"] for r in righe if not r["errore"] and r["dichiarato_fuori_dominio"] is not True]
        if sbagliate:
            out.append(f"Da verificare (risposta data a domanda fuori dominio): {', '.join(sbagliate)}")
    else:
        out.append(f"Dichiarate fuori dominio (possibili falsi negativi): {len(fuori)}"
                   + (f" -> {', '.join(r['id'] for r in fuori)}" if fuori else ""))
    out.append("")
    return out


def esegui(kb: KB, quale: str = "entrambi", versione_prompt: str = "A",
           modello: str | None = None, top_k: int = TOP_K) -> int:
    modello = modello or config.modello_llm()
    versione_prompt = versione_prompt.upper()
    if versione_prompt not in PROMPT:
        raise ValueError(f"Versione prompt sconosciuta: '{versione_prompt}'")
    insiemi = {"golden": ["golden"], "negative": ["negative"], "entrambi": ["golden", "negative"]}
    if quale not in insiemi:
        raise ValueError("Set ammessi: golden, negative, entrambi")
    min_score = config.min_score()

    suffisso = f"{modello.replace(':', '-').replace('/', '-')}_prompt{versione_prompt}"
    with Esecuzione("risposta", kb.nome, suffisso=suffisso) as es:
        log = es.logger
        log.info("Modello LLM: %s | prompt %s | top-k %s | MIN_SCORE %s", modello, versione_prompt, top_k, min_score)
        sommario = es.intestazione("RISPOSTA LLM - RIEPILOGO")
        sommario += [f"Modello LLM: {modello}", f"Modello embedding: {config.modello_embedding()}",
                     f"Prompt: {versione_prompt}", f"Top-K: {top_k}", f"MIN_SCORE: {min_score}", ""]
        codice = 0
        for tipo_set in insiemi[quale]:
            percorso = kb.golden_set if tipo_set == "golden" else kb.negative_set
            if percorso is None or not percorso.exists():
                log.warning("Set '%s' non disponibile per la KB '%s' (%s): saltato.", tipo_set, kb.nome, percorso)
                sommario += [f"SET: {tipo_set} - SALTATO (file non disponibile)", ""]
                continue
            obbligatorie = COLONNE_GOLDEN[:4] if tipo_set == "golden" else ["id", "domanda"]
            domande = leggi_set(percorso, obbligatorie)
            righe = []
            for riga in domande:
                log.info("[%s] %s", riga["id"], riga["domanda"])
                try:
                    esito = rispondi(kb, riga["domanda"], top_k, versione_prompt, modello, min_score)
                    righe.append(_riga_csv(riga, tipo_set, esito, None, modello, versione_prompt))
                except Exception as exc:
                    log.error("%s: %s", riga["id"], exc)
                    righe.append(_riga_csv(riga, tipo_set, None, str(exc), modello, versione_prompt))
                    codice = 2
            csv_path = es.percorso_report(f"risposta_results_{tipo_set}", "csv")
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=COLONNE_CSV)
                w.writeheader()
                w.writerows(righe)
            sommario += riepilogo(righe, tipo_set) + [f"Risultati: {csv_path.name}", ""]
        report = es.percorso_report("risposta_report")
        report.write_text("\n".join(sommario), encoding="utf-8")
        print("\n".join(sommario))
        print(f"Report: {report}")
        return codice
