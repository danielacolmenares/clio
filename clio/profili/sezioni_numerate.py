"""Profilo "sezioni_numerate": un chunk per sezione numerata ("3.1 Titolo").

Pensato per documenti con sommario/indice numerato (procedure, manuali).

Criteri di riconoscimento di un titolo di sezione:
  - se il documento ha un sommario (righe "3.1 Titolo ....... 4"), il
    sommario e' la whitelist: il numero deve comparire nel sommario E il
    titolo trovato nel corpo deve somigliare a quello dichiarato. Il solo
    numero non basta: una riga di tabella puo' riusare per caso un numero
    di sezione esistente;
  - senza sommario si usa un'euristica di numerazione crescente (meno
    affidabile: il report va controllato).

"1. Testo" (numero seguito da punto) non e' un titolo di sezione ma un
passo numerato: il pattern richiede uno spazio subito dopo le cifre.

Le sezioni troppo lunghe non vengono suddivise e le sezioni corte non
vengono unite. Un documento senza alcuna sezione riconosciuta produce zero
chunk e un avviso nel report: va gestito in modo esplicito, non ignorato.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from .base import RisultatoSegmentazione, Sezione, righe_con_pagina

# "Pag. 3 a 7": rumore di impaginazione (la pagina e' gia' un metadato).
RE_RIGA_PAGINA_FOOTER = re.compile(r"^Pag\.\s*\d+\s*(a|di)\s*\d+\s*$", re.IGNORECASE)
# Righe di indice con puntini di riempimento e numero di pagina.
RE_RIGA_INDICE = re.compile(r"\.{2,}\s*\d+\s*$")
RE_VOCE_INDICE = re.compile(r"^(\d+(?:\.\d+)*)\s+.+\.{2,}\s*\d+\s*$")
RE_TITOLO_SEZIONE = re.compile(r"^(\d+(?:\.\d+)*)\s+(.+)$")

TITOLI_DA_SCARTARE = {"sommario", "indice", "index", "table of contents"}


def estrai_metadati_documento(testo_preambolo: str) -> dict:
    metadati = {"codice": None, "titolo": None, "versione": None}
    for chiave, etichetta in (("codice", "Codice"), ("titolo", "Titolo"), ("versione", "Versione")):
        m = re.search(rf"^{etichetta}:\s*(.+)$", testo_preambolo, re.MULTILINE)
        if m:
            metadati[chiave] = m.group(1).strip()
    return metadati


def estrai_numeri_da_indice(testo: str) -> dict[str, str]:
    """{numero: titolo_atteso} dalle voci di sommario."""
    voci = {}
    for grezza in testo.split("\n"):
        riga = grezza.strip()
        m = RE_VOCE_INDICE.match(riga)
        if m:
            numero = m.group(1)
            resto = riga[len(numero):].strip()
            voci[numero] = re.sub(r"\.{2,}\s*\d+\s*$", "", resto).strip()
    return voci


def titoli_simili(candidato: str, atteso: str) -> bool:
    """Confronto permissivo: l'estrazione PDF introduce piccole variazioni,
    ma serve una somiglianza reale."""
    def norm(t: str) -> str:
        return re.sub(r"[^a-z0-9 ]", "", t.lower()).strip()

    a, b = norm(candidato), norm(atteso)
    if not a or not b:
        return False
    if a.startswith(b[:15]) or b.startswith(a[:15]):
        return True
    return SequenceMatcher(None, a, b).ratio() >= 0.5


def calcola_gerarchia(numero: str) -> tuple[int, str | None, list[str]]:
    parti = numero.split(".")
    livello = len(parti)
    parent = ".".join(parti[:-1]) if livello > 1 else None
    path = [".".join(parti[: i + 1]) for i in range(livello)]
    return livello, parent, path


def segmenta(testo: str, opzioni: dict | None = None) -> RisultatoSegmentazione:
    avvisi: list[str] = []
    preambolo: list[str] = []
    grezze: list[dict] = []
    corrente = None
    ultimo = (0,)
    dentro_tabella = False
    rumore = 0

    indice = estrai_numeri_da_indice(testo)
    if not indice:
        avvisi.append("Nessun sommario riconosciuto: uso l'euristica di numerazione "
                      "crescente (meno affidabile, verificare il report).")

    for testo_riga, pagina in righe_con_pagina(testo):
        riga = testo_riga.strip()
        if not riga:
            if corrente:
                corrente["corpo"].append(("", pagina))
            continue
        if RE_RIGA_PAGINA_FOOTER.match(riga) or RE_RIGA_INDICE.search(riga):
            rumore += 1
            continue
        if riga == "[TABELLA]":
            dentro_tabella = True
        elif riga == "[/TABELLA]":
            dentro_tabella = False

        m = None if dentro_tabella else RE_TITOLO_SEZIONE.match(riga)
        accettato = False
        if m:
            numero, titolo = m.group(1), m.group(2).strip()
            tupla = tuple(int(x) for x in numero.split("."))
            if indice:
                atteso = indice.get(numero)
                corrisponde = atteso is not None and titoli_simili(titolo, atteso)
                accettato = corrisponde and tupla >= ultimo
            else:
                accettato = tupla >= ultimo
            if accettato and tupla == ultimo:
                avvisi.append(f"Numero di sezione duplicato: '{numero} {titolo}'")

        if accettato:
            if corrente:
                grezze.append(corrente)
            corrente = {"numero": numero, "titolo": titolo, "corpo": [], "pagina": pagina}
            ultimo = tupla
        elif corrente:
            corrente["corpo"].append((testo_riga, pagina))
        else:
            preambolo.append(testo_riga)

    if corrente:
        grezze.append(corrente)

    sezioni = []
    for g in grezze:
        titolo_norm = re.sub(r"[^a-zA-Z ]", "", g["titolo"]).strip().lower()
        if titolo_norm in TITOLI_DA_SCARTARE:
            avvisi.append(f"Sezione '{g['numero']} {g['titolo']}' esclusa (indice/sommario).")
            continue
        livello, parent, path = calcola_gerarchia(g["numero"])
        corpo = g["corpo"] or [("", g["pagina"])]
        if g["pagina"] is not None:
            corpo = [("", g["pagina"])] + corpo  # la pagina del titolo conta
        sezioni.append(Sezione(
            tipo="sezione", sezione=g["numero"], etichetta=f"sez. {g['numero']}",
            titolo=g["titolo"], intestazione=[f"{g['numero']} {g['titolo']}"],
            corpo=corpo, path=path, livello=livello, parent=parent,
        ))

    if not sezioni:
        avvisi.append("ATTENZIONE: nessuna sezione riconosciuta, il documento produce "
                      "zero chunk e non sara' interrogabile.")

    return RisultatoSegmentazione(
        sezioni=sezioni,
        metadati=estrai_metadati_documento("\n".join(preambolo)),
        avvisi=avvisi,
        statistiche={"sezioni": len(sezioni), "righe_rumore_rimosse": rumore,
                     "sommario": bool(indice)},
    )
