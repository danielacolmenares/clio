"""Profilo "legislativo_ue": atti legislativi UE in italiano (testo EUR-Lex).

Struttura attesa del testo estratto da un PDF della Gazzetta ufficiale:

    REGOLAMENTO (UE) 2016/679 DEL PARLAMENTO EUROPEO E DEL CONSIGLIO   <- preambolo
    ...
    considerando quanto segue:
    (1) La protezione delle persone fisiche ...                         <- considerando
    (2) ...
    HANNO ADOTTATO IL PRESENTE REGOLAMENTO:
    CAPO I                                                              <- articoli
    Disposizioni generali
    Sezione 1
    ...
    Articolo 5
    Principi applicabili al trattamento di dati personali
    1. I dati personali sono:
    a) trattati in modo lecito ...
    Fatto a Bruxelles, il 27 aprile 2016.                               <- chiusura (scartata)
    ALLEGATO I                                                          <- allegati
    ...

Chunk prodotti:
  - un chunk per Articolo: titolo "Articolo N – <titolo>", path = Capo/Sezione;
  - un chunk per Allegato;
  - i considerando raggruppati a blocchi (default 10), metadato
    ``considerando: [da, a]``;
  - articoli/allegati oltre ``max_caratteri_chunk`` vengono suddivisi per
    paragrafo numerato ("1.", "2."), poi per punto ("1)", "2)"), poi per
    lettera ("a)", "b)"), infine per righe; ogni parte ripete l'intestazione
    dell'articolo e riporta ``paragrafi``/``punti``/``lettere`` e ``parte``.

Rumore rimosso dal testo (non entra mai nell'embedding): intestazioni e
pie' di pagina della Gazzetta ufficiale (es. "4.5.2016 IT Gazzetta
ufficiale dell'Unione europea L 119/37", "L 119/38 IT ...", righe "ELI:",
contatori "12/144") e note a pie' di pagina con riferimenti "GU L/C". Il
numero di pagina resta come metadato.

Robustezza: i titoli (Capo, Sezione, Articolo, Allegato, considerando)
sono accettati solo se la numerazione e' coerente con quella precedente;
una riga che sembra un titolo ma e' fuori sequenza resta contenuto e
genera un avviso nel report.
"""

from __future__ import annotations

import re

from .base import RisultatoSegmentazione, Riga, Sezione, componi_testo, righe_con_pagina

# ----------------------------------------------------------------------
# Rumore della Gazzetta ufficiale
# ----------------------------------------------------------------------

_GU = r"Gazzetta\s+ufficiale(?:\s+dell.\s?Unione\s+europea)?"
_DATA = r"\d{1,2}\.\d{1,2}\.\d{4}"
_RIF_L = r"L\s*\d+\s*/\s*\d+"

RE_RUMORE = [
    # Formato classico, pagine dispari e pari:
    #   "4.5.2016 IT Gazzetta ufficiale dell'Unione europea L 119/37"
    #   "L 119/38 IT Gazzetta ufficiale dell'Unione europea 4.5.2016"
    re.compile(rf"^(?:{_DATA}\s+|{_RIF_L}\s+)?(?:IT\s+)?{_GU}(?:\s+IT)?"
               rf"(?:\s+(?:{_RIF_L}|Serie\s+L|L\s+serie))?(?:\s+\d{{4}}/\d+)?(?:\s+{_DATA})?$",
               re.IGNORECASE),
    # Formato "atto per atto" (dal 2023): righe spezzate dell'intestazione.
    re.compile(r"^(?:IT\s+)?(?:Serie\s+L|L\s+serie|dell.\s?Unione\s+europea|Gazzetta\s+ufficiale)(?:\s+IT)?$",
               re.IGNORECASE),
    re.compile(r"^IT$"),
    re.compile(rf"^(?:IT\s+)?GU\s+L\s+del\s+{_DATA}$", re.IGNORECASE),
    re.compile(rf"^(?:IT\s+)?(?:Serie\s+L\s+|L\s+serie\s+)?\d{{4}}/\d+\s+{_DATA}$", re.IGNORECASE),
    re.compile(rf"^{_DATA}$"),
    re.compile(rf"^{_RIF_L}$"),
    re.compile(r"^ELI:\s*\S+(?:\s+\d+/\d+)?$", re.IGNORECASE),
    re.compile(r"^\d{1,4}\s*/\s*\d{1,4}$"),      # contatore pagine "12/144"
]

# Note a pie' di pagina: "(1) GU C 229 del 31.7.2012, pag. 90."
RE_NOTA_GU = re.compile(r"^\(\s*\d+\s*\)\s*(?:GU|G\.U\.)\s+[LC]\b")
# Inizio tipico di una nota con riferimento a un altro atto.
RE_NOTA = re.compile(
    r"^\(\s*\d+\s*\)\s*(?:GU|Regolamento|Direttiva|Decisione|Raccomandazione|Posizione|Parere|"
    r"Sentenza|Accordo|Comunicazione|Risoluzione|Conclusioni|Convenzione|Protocollo|Cfr\.|ECLI)\b"
)

# ----------------------------------------------------------------------
# Titoli strutturali
# ----------------------------------------------------------------------

RE_CONSIDERANDO_INIZIO = re.compile(r"considerando\s+quanto\s+segue", re.IGNORECASE)
RE_CONSIDERANDO = re.compile(r"^\(\s*(\d+)\s*\)\s*(.*)$")
RE_ADOZIONE = re.compile(r"^HANNO\s+ADOTTATO\b", re.IGNORECASE)
RE_CAPO = re.compile(r"^(CAPO|TITOLO)\s+([IVXLC]+)\s*$")
RE_SEZIONE = re.compile(r"^Sezione\s+(\d+)\s*$", re.IGNORECASE)
RE_ARTICOLO = re.compile(r"^Articolo\s+(\d+)(?:\s*(bis|ter|quater|quinquies|sexies))?\s*$")
# Numero e titolo sulla stessa riga ("Articolo 5 Principi applicabili ..."):
# accettato solo se il numero e' esattamente il successivo atteso.
RE_ARTICOLO_IN_LINEA = re.compile(r"^Articolo\s+(\d+)\s+([A-ZÀ-Ý][^.;:]{2,200})$")
RE_ALLEGATO = re.compile(r"^ALLEGATO(?:\s+([IVXLC]+))?\s*$")
RE_CHIUSURA = re.compile(r"^Fatto\s+a\s+\S+.*,\s+(?:il\s+)?\d{1,2}\s+\w+\s+\d{4}")
RE_ATTO = re.compile(r"^(REGOLAMENTO|DIRETTIVA|DECISIONE)\b")
RE_NUMERO_ATTO = re.compile(r"\((?:UE|CE|UE,\s*Euratom)\)\s*(?:n\.\s*)?(\d{2,4}/\d+)")

# Unita' per la suddivisione degli articoli lunghi.
RE_PARAGRAFO = re.compile(r"^(\d+)\.\s+\S")
RE_PUNTO = re.compile(r"^(\d+)\)\s+\S")
RE_LETTERA = re.compile(r"^([a-z])\)\s+\S")

ROMANI = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}


def romano_a_intero(s: str) -> int:
    totale = 0
    for i, ch in enumerate(s):
        v = ROMANI[ch]
        totale += -v if i + 1 < len(s) and ROMANI[s[i + 1]] > v else v
    return totale


def e_rumore(riga: str) -> bool:
    return any(r.match(riga) for r in RE_RUMORE) or bool(RE_NOTA_GU.match(riga))


# Sigle che restano maiuscole quando un titolo in maiuscolo viene riportato in minuscolo.
SIGLE = {"IA", "UE", "CSIRT", "TIC", "PMI", "ENISA", "CE"}
# Nomi propri che restano con l'iniziale maiuscola.
NOMI_PROPRI = {"UNIONE": "Unione"}


def _cap(titolo: str) -> str:
    """'PRATICHE DI IA VIETATE' -> 'Pratiche di IA vietate'; altrimenti invariato."""
    if not (titolo.isupper() and len(titolo) > 3):
        return titolo

    def parola(m):
        w = m.group(0)
        if w in SIGLE:
            return w
        return NOMI_PROPRI.get(w, w.lower())

    testo = re.sub(r"[^\W\d_]+", parola, titolo)
    return testo[:1].upper() + testo[1:]


# ----------------------------------------------------------------------
# Preambolo
# ----------------------------------------------------------------------

def estrai_metadati(preambolo: list[str]) -> dict:
    righe = [r.strip() for r in preambolo if r.strip()]
    titolo_righe: list[str] = []
    for i, r in enumerate(righe):
        if RE_ATTO.match(r):
            for r2 in righe[i:]:
                if r2.startswith("(Testo rilevante") or r2.startswith("IL PARLAMENTO") \
                        or r2.startswith("IL CONSIGLIO") or r2.lower().startswith("visto"):
                    break
                titolo_righe.append(r2)
            break
    titolo = re.sub(r"\s+", " ", " ".join(titolo_righe)).strip() or None
    numero = None
    m = RE_NUMERO_ATTO.search(titolo or " ".join(righe[:20]))
    if m:
        numero = m.group(1)
    return {"titolo": titolo, "numero_atto": numero, "codice": numero, "versione": None}


# ----------------------------------------------------------------------
# Suddivisione di articoli lunghi
# ----------------------------------------------------------------------

def _lunghezza(righe: list[Riga]) -> int:
    return sum(len(t) + 1 for t, _ in righe)


def _unita(righe: list[Riga], regex: re.Pattern, lettere: bool) -> tuple[list[Riga], list[tuple]]:
    """Divide le righe in (prologo, [(etichetta, righe)]) secondo una
    numerazione sequenziale: 1,2,3... oppure a,b,c... (solo crescente)."""
    prologo: list[Riga] = []
    unita: list[tuple[str, list[Riga]]] = []
    ultimo = None
    for riga in righe:
        m = regex.match(riga[0].strip())
        if m:
            val = m.group(1)
            if lettere:
                ok = (ultimo is None and val == "a") or (ultimo is not None and val > str(ultimo)
                                                          and ord(val) - ord(str(ultimo)) <= 2)
            else:
                ok = (ultimo is None and val == "1") or (ultimo is not None and int(val) == int(ultimo) + 1)
            if ok:
                unita.append((int(val) if val.isdigit() else val, [riga]))
                ultimo = val
                continue
        (unita[-1][1] if unita else prologo).append(riga)
    return prologo, unita


LIVELLI = (
    ("paragrafi", RE_PARAGRAFO, False),
    ("punti", RE_PUNTO, False),
    ("lettere", RE_LETTERA, True),
)


def _per_righe(righe: list[Riga], max_car: int) -> list[tuple[list[Riga], dict]]:
    pezzi, corrente = [], []
    for r in righe:
        if corrente and _lunghezza(corrente) + len(r[0]) + 1 > max_car:
            pezzi.append((corrente, {}))
            corrente = []
        corrente.append(r)
    if corrente:
        pezzi.append((corrente, {}))
    return pezzi


def dividi(righe: list[Riga], max_car: int, livelli=LIVELLI) -> list[tuple[list[Riga], dict]]:
    """Restituisce [(righe, meta)]; meta indica l'intervallo di unita'
    contenuto in ciascun pezzo, es. {"paragrafi": ["1", "3"]}."""
    if _lunghezza(righe) <= max_car:
        return [(righe, {})]
    for i, (chiave, regex, lettere) in enumerate(livelli):
        prologo, unita = _unita(righe, regex, lettere)
        if len(unita) < 2:
            continue
        if prologo:
            unita[0] = (unita[0][0], prologo + unita[0][1])
        pezzi: list[tuple[list[Riga], dict]] = []
        corrente: list[Riga] = []
        etichette: list[str] = []

        def chiudi():
            if corrente:
                pezzi.append((list(corrente), {chiave: [etichette[0], etichette[-1]]}))
                corrente.clear()
                etichette.clear()

        for etichetta, rr in unita:
            if _lunghezza(rr) > max_car:
                chiudi()
                for sotto, meta in dividi(rr, max_car, livelli[i + 1:]):
                    pezzi.append((sotto, {chiave: [etichetta, etichetta], **meta}))
                continue
            if corrente and _lunghezza(corrente) + _lunghezza(rr) > max_car:
                chiudi()
            corrente.extend(rr)
            etichette.append(etichetta)
        chiudi()
        return pezzi
    return _per_righe(righe, max_car)


def _suddividi_sezione(s: Sezione, max_car: int) -> list[Sezione]:
    pezzi = dividi(s.corpo, max_car)
    if len(pezzi) == 1:
        return [s]
    risultato = []
    for k, (righe, meta) in enumerate(pezzi, start=1):
        extra = dict(s.extra, parte=[k, len(pezzi)], **meta)
        nota = ", ".join(f"{c} {v[0]}" + (f"-{v[1]}" if v[1] != v[0] else "")
                         for c, v in meta.items())
        intest = list(s.intestazione) + [f"(parte {k}/{len(pezzi)}" + (f", {nota})" if nota else ")")]
        risultato.append(Sezione(
            tipo=s.tipo, sezione=s.sezione, etichetta=s.etichetta, titolo=s.titolo,
            intestazione=intest, corpo=righe, path=s.path, livello=s.livello,
            parent=s.parent, extra=extra, prefisso_documento=s.prefisso_documento,
        ))
    return risultato


# ----------------------------------------------------------------------
# Segmentazione
# ----------------------------------------------------------------------

class _Stato:
    def __init__(self):
        self.zona = "preambolo"
        self.preambolo: list[str] = []
        self.considerando: list[tuple[int, list[Riga]]] = []
        self.blocchi: list[dict] = []       # articoli e allegati in ordine
        self.corrente: dict | None = None
        self.capo: str | None = None
        self.sezione: str | None = None
        self.ultimo_capo = 0
        self.ultima_sezione = 0
        self.ultimo_articolo = 0
        self.ultimo_allegato = 0
        # Titolo in attesa: dopo "Articolo 5" la riga seguente e' il titolo.
        self.attesa_titolo: dict | None = None
        # Pagina su cui e' iniziata una nota a pie' di pagina.
        self.nota_pagina: int | None = None


def _ripresa_dopo_nota(st: _Stato, riga: str) -> bool:
    """Dopo l'inizio di una nota, solo un nuovo considerando in sequenza o
    un titolo strutturale riprendono il testo sulla stessa pagina."""
    if _e_titolo_strutturale(riga):
        return True
    if st.zona == "considerando":
        m = RE_CONSIDERANDO.match(riga)
        atteso = (st.considerando[-1][0] + 1) if st.considerando else 1
        return bool(m and int(m.group(1)) == atteso)
    return False


def _titolo_plausibile(riga: str) -> bool:
    return (not RE_PARAGRAFO.match(riga) and not RE_PUNTO.match(riga)
            and not RE_LETTERA.match(riga) and not riga.endswith((".", ";", ":"))
            and len(riga) <= 250 and not riga.startswith("[TABELLA"))


def segmenta(testo: str, opzioni: dict | None = None) -> RisultatoSegmentazione:
    opzioni = opzioni or {}
    per_blocco = int(opzioni.get("considerando_per_chunk", 10))
    max_car = int(opzioni.get("max_caratteri_chunk", 6000))

    st = _Stato()
    avvisi: list[str] = []
    rumore = 0
    note = 0

    def nuovo_blocco(tipo: str, numero: str, pagina, **kw):
        st.corrente = {"tipo": tipo, "numero": numero, "titolo": "", "corpo": [("", pagina)],
                       "path": [p for p in (st.capo, st.sezione) if p], **kw}
        st.blocchi.append(st.corrente)

    for testo_riga, pagina in righe_con_pagina(testo):
        riga = testo_riga.strip()

        if not riga:
            if st.zona == "considerando" and st.considerando:
                st.considerando[-1][1].append(("", pagina))
            elif st.corrente is not None and st.zona in ("articoli", "allegati"):
                st.corrente["corpo"].append(("", pagina))
            continue

        if e_rumore(riga):
            if RE_NOTA_GU.match(riga):
                note += 1
                st.nota_pagina = pagina   # None (testo senza pagine): nessun effetto
            else:
                rumore += 1
            continue

        # --- note a pie' di pagina: dall'inizio della nota a fine pagina --
        if st.nota_pagina is not None:
            if pagina == st.nota_pagina and not _ripresa_dopo_nota(st, riga):
                note += 1
                continue
            st.nota_pagina = None
        if st.zona in ("articoli", "allegati") and pagina is not None and RE_NOTA.match(riga):
            note += 1
            st.nota_pagina = pagina
            continue

        # --- titolo in attesa (Capo/Sezione/Articolo/Allegato) ---------
        if st.attesa_titolo is not None:
            bersaglio = st.attesa_titolo
            gia = bersaglio["titolo"]
            if not gia and _titolo_plausibile(riga) and not _e_titolo_strutturale(riga):
                bersaglio["titolo"] = riga
                continue
            if gia and riga[:1].islower() and not gia.endswith(".") and len(gia) + len(riga) < 250:
                # titolo spezzato su piu' righe (anche con sillabazione)
                bersaglio["titolo"] = gia[:-1] + riga if gia.endswith("-") else f"{gia} {riga}"
                continue
            _aggiorna_path(st)
            st.attesa_titolo = None

        # --- passaggi di zona ------------------------------------------
        if st.zona == "preambolo":
            if RE_CONSIDERANDO_INIZIO.search(riga):
                st.zona = "considerando"
                continue
            m = RE_CONSIDERANDO.match(riga)
            if m and m.group(1) == "1" and any(RE_ATTO.match(p.strip()) for p in st.preambolo):
                st.zona = "considerando"
            elif RE_CAPO.match(riga) or RE_ARTICOLO.match(riga):
                st.zona = "articoli"
            else:
                st.preambolo.append(riga)
                continue

        if st.zona == "considerando":
            if RE_ADOZIONE.match(riga):
                st.zona = "articoli"
                continue
            m_art = RE_ARTICOLO.match(riga) or RE_ARTICOLO_IN_LINEA.match(riga)
            if RE_CAPO.match(riga) or (m_art and m_art.group(1) == "1"):
                st.zona = "articoli"
            else:
                m = RE_CONSIDERANDO.match(riga)
                atteso = (st.considerando[-1][0] + 1) if st.considerando else 1
                sembra_nota = bool(m and RE_NOTA.match(riga) and re.search(r"\bGU\b|\bpag\.", riga))
                if m and int(m.group(1)) == atteso and not sembra_nota:
                    st.considerando.append((atteso, [(riga, pagina)]))
                elif m and st.considerando and pagina is not None:
                    # "(n)" fuori sequenza: nota a pie' di pagina, scartata
                    # insieme alle righe seguenti fino a fine pagina.
                    note += 1
                    st.nota_pagina = pagina
                elif st.considerando:
                    st.considerando[-1][1].append((testo_riga, pagina))
                continue

        if st.zona in ("articoli", "chiusura", "allegati"):
            m_all = RE_ALLEGATO.match(riga)
            if m_all:
                n = romano_a_intero(m_all.group(1)) if m_all.group(1) else 1
                if n > st.ultimo_allegato:
                    st.zona = "allegati"
                    st.ultimo_allegato = n
                    st.capo = st.sezione = None
                    numero = m_all.group(1) or ""
                    nuovo_blocco("allegato", numero, pagina)
                    st.corrente["path"] = []
                    st.attesa_titolo = st.corrente
                    continue
                avvisi.append(f"Riga '{riga}' a pag. {pagina} fuori sequenza: trattata come testo.")

        if st.zona == "chiusura":
            continue      # firme e formule finali: nessun valore per il retrieval

        if st.zona == "articoli":
            if RE_CHIUSURA.match(riga):
                st.zona = "chiusura"
                continue
            m = RE_CAPO.match(riga)
            if m:
                n = romano_a_intero(m.group(2))
                if n > st.ultimo_capo:
                    st.ultimo_capo = n
                    st.ultima_sezione = 0
                    st.sezione = None
                    st.attesa_titolo = {"tipo": "capo", "etichetta": f"{m.group(1).capitalize()} {m.group(2)}",
                                        "titolo": ""}
                    _aggiorna_path(st)
                    continue
                avvisi.append(f"Riga '{riga}' a pag. {pagina} fuori sequenza: trattata come testo.")
            m = RE_SEZIONE.match(riga)
            if m and st.capo is not None:
                n = int(m.group(1))
                if n == st.ultima_sezione + 1:
                    st.ultima_sezione = n
                    st.attesa_titolo = {"tipo": "sezione", "etichetta": f"Sezione {n}", "titolo": ""}
                    _aggiorna_path(st)
                    continue
            m = RE_ARTICOLO.match(riga)
            if m:
                n = int(m.group(1))
                suffisso = m.group(2)
                if suffisso and n == st.ultimo_articolo:
                    ok = True
                else:
                    ok = not suffisso and st.ultimo_articolo < n <= st.ultimo_articolo + 3
                if ok:
                    if n > st.ultimo_articolo + 1:
                        avvisi.append(f"Numerazione articoli: da {st.ultimo_articolo} a {n} "
                                      f"(possibile titolo non riconosciuto).")
                    st.ultimo_articolo = n
                    numero = f"{n}{' ' + suffisso if suffisso else ''}"
                    nuovo_blocco("articolo", numero, pagina)
                    st.attesa_titolo = st.corrente
                    continue
                avvisi.append(f"Riga '{riga}' a pag. {pagina} fuori sequenza: trattata come testo.")
            m = RE_ARTICOLO_IN_LINEA.match(riga)
            if m and int(m.group(1)) == st.ultimo_articolo + 1:
                st.ultimo_articolo = int(m.group(1))
                nuovo_blocco("articolo", m.group(1), pagina)
                st.corrente["titolo"] = m.group(2).strip()
                st.attesa_titolo = st.corrente        # consente la continuazione del titolo
                continue

        if st.corrente is not None:
            st.corrente["corpo"].append((testo_riga, pagina))

    # path definitivo per gli oggetti in attesa a fine testo
    st.attesa_titolo = None

    sezioni: list[Sezione] = []
    sezioni += _sezioni_considerando(st.considerando, per_blocco, max_car)
    for b in st.blocchi:
        sezioni.append(_sezione_da_blocco(b))

    finali: list[Sezione] = []
    n_suddivisi = 0
    for s in sezioni:
        if s.tipo in ("articolo", "allegato"):
            parti = _suddividi_sezione(s, max_car)
            n_suddivisi += len(parti) > 1
            finali.extend(parti)
        else:
            finali.append(s)

    residui = sum(1 for s in finali for t, _ in s.corpo if "Gazzetta ufficiale dell" in t and len(t) < 120)
    if residui:
        avvisi.append(f"{residui} righe brevi contenenti 'Gazzetta ufficiale' rimaste nel testo: "
                      f"verificare i pattern di rumore.")
    n_art = sum(1 for b in st.blocchi if b["tipo"] == "articolo")
    if n_art == 0:
        avvisi.append("ATTENZIONE: nessun articolo riconosciuto. Verificare che il documento "
                      "sia un atto UE e che il profilo sia corretto.")
    senza_titolo = [b["numero"] for b in st.blocchi if b["tipo"] == "articolo" and not b["titolo"]]
    if senza_titolo:
        avvisi.append(f"Articoli senza titolo riconosciuto: {', '.join(senza_titolo[:20])}")

    return RisultatoSegmentazione(
        sezioni=finali,
        metadati=estrai_metadati(st.preambolo),
        avvisi=avvisi,
        statistiche={
            "considerando": len(st.considerando),
            "articoli": n_art,
            "allegati": sum(1 for b in st.blocchi if b["tipo"] == "allegato"),
            "capi": st.ultimo_capo,
            "sezioni_suddivise": n_suddivisi,
            "righe_rumore_rimosse": rumore,
            "note_gu_rimosse": note,
        },
    )


def _e_titolo_strutturale(riga: str) -> bool:
    return bool(RE_CAPO.match(riga) or RE_SEZIONE.match(riga) or RE_ARTICOLO.match(riga)
                or RE_ALLEGATO.match(riga))


def _aggiorna_path(st: _Stato) -> None:
    """Aggiorna capo/sezione correnti quando il titolo e' completo."""
    a = st.attesa_titolo
    if a is None:
        return
    if a.get("tipo") == "capo":
        st.capo = f"{a['etichetta']} – {_cap(a['titolo'])}" if a["titolo"] else a["etichetta"]
    elif a.get("tipo") == "sezione":
        st.sezione = f"{a['etichetta']} – {_cap(a['titolo'])}" if a["titolo"] else a["etichetta"]


def _sezione_da_blocco(b: dict) -> Sezione:
    titolo = _cap(b["titolo"])
    if b["tipo"] == "articolo":
        intest = f"Articolo {b['numero']}" + (f" – {titolo}" if titolo else "")
        return Sezione(
            tipo="articolo", sezione=f"art. {b['numero']}", etichetta=f"art. {b['numero']}",
            titolo=intest, intestazione=([" › ".join(b["path"])] if b["path"] else []) + [intest],
            corpo=b["corpo"], path=b["path"], livello=len(b["path"]) + 1,
            extra={"articolo": b["numero"]}, prefisso_documento=True,
        )
    nome = f"Allegato {b['numero']}".strip()
    intest = nome + (f" – {titolo}" if titolo else "")
    return Sezione(
        tipo="allegato", sezione=nome[0].lower() + nome[1:], etichetta=nome[0].lower() + nome[1:],
        titolo=intest,
        intestazione=[intest], corpo=b["corpo"], path=[], livello=1,
        extra={"allegato": b["numero"] or None}, prefisso_documento=True,
    )


def _sezioni_considerando(considerando: list[tuple[int, list[Riga]]], per_blocco: int,
                          max_car: int) -> list[Sezione]:
    """Blocchi allineati (1-10, 11-20, ...); un blocco troppo lungo viene
    diviso ai confini dei singoli considerando."""
    gruppi: dict[int, list[tuple[int, list[Riga]]]] = {}
    for n, righe in considerando:
        gruppi.setdefault((n - 1) // per_blocco, []).append((n, righe))

    sezioni = []
    for _, gruppo in sorted(gruppi.items()):
        pezzi: list[list[tuple[int, list[Riga]]]] = [[]]
        for item in gruppo:
            if pezzi[-1] and sum(_lunghezza(r) for _, r in pezzi[-1]) + _lunghezza(item[1]) > max_car:
                pezzi.append([])
            pezzi[-1].append(item)
        for pezzo in pezzi:
            da, a = pezzo[0][0], pezzo[-1][0]
            intervallo = f"{da}-{a}" if da != a else f"{da}"
            corpo = [r for _, righe in pezzo for r in righe]
            sezioni.append(Sezione(
                tipo="considerando", sezione=f"considerando {intervallo}",
                etichetta=f"considerando {intervallo}",
                titolo=f"Considerando ({da})" + (f"–({a})" if a != da else ""),
                intestazione=[f"Considerando ({da})" + (f"–({a})" if a != da else "")],
                corpo=corpo, path=[], livello=1,
                extra={"considerando": [da, a]}, prefisso_documento=True,
            ))
    return sezioni


__all__ = ["segmenta", "e_rumore", "dividi", "estrai_metadati", "romano_a_intero", "componi_testo"]
