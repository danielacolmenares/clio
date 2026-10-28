"""Strutture comuni ai profili di chunking.

Un profilo riceve il testo prodotto dal passo 1 (con marcatori
``[PAGINA N]``) e restituisce una lista di ``Sezione``: unita' logiche
gia' pronte per diventare chunk. Il passo 2 (``clio.chunking``) aggiunge
identificativo del documento, hash e salvataggio.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

RE_MARCATORE_PAGINA = re.compile(r"^\[PAGINA (\d+)\]$")

Riga = tuple[str, "int | None"]   # (testo della riga, pagina)


@dataclass
class Sezione:
    tipo: str                        # sezione / articolo / allegato / considerando
    sezione: str                     # identificativo usato per la valutazione (es. "3.1", "art. 33")
    etichetta: str                   # forma usata nella citazione (es. "sez. 3.1", "art. 33")
    titolo: str                      # titolo della sezione/articolo
    intestazione: list[str]          # righe di intestazione incluse nel testo per l'embedding
    corpo: list[Riga] = field(default_factory=list)
    path: list[str] = field(default_factory=list)
    livello: int = 1
    parent: str | None = None
    extra: dict = field(default_factory=dict)   # articolo, paragrafi, punti, considerando, parte...
    # True se il testo per l'embedding va preceduto dall'identificativo del
    # documento (utile quando piu' documenti hanno articoli omonimi).
    prefisso_documento: bool = False

    @property
    def pagine(self) -> list[int]:
        return sorted({p for _, p in self.corpo if p is not None})


@dataclass
class RisultatoSegmentazione:
    sezioni: list[Sezione]
    metadati: dict                     # titolo, codice, versione, numero_atto...
    avvisi: list[str] = field(default_factory=list)
    statistiche: dict = field(default_factory=dict)


def righe_con_pagina(testo: str) -> list[Riga]:
    """Converte il testo del passo 1 in righe (testo, pagina), togliendo i
    marcatori di pagina. Le righe vuote sono mantenute come separatori."""
    pagina = None
    risultato: list[Riga] = []
    for grezza in testo.split("\n"):
        m = RE_MARCATORE_PAGINA.match(grezza.strip())
        if m:
            pagina = int(m.group(1))
            continue
        risultato.append((grezza.rstrip(), pagina))
    return risultato


RE_SILLABAZIONE = re.compile(r"[A-Za-zÀ-ÿ]-$")


def componi_testo(righe: list[Riga]) -> str:
    """Testo pulito di un corpo: ricompone le parole spezzate a fine riga
    ("tratta-" + "mento") e comprime le righe vuote consecutive."""
    uscita: list[str] = []
    for testo, _ in righe:
        t = testo.strip()
        if (t and uscita and uscita[-1] and RE_SILLABAZIONE.search(uscita[-1])
                and t[0].islower()):
            uscita[-1] = uscita[-1][:-1] + t
            continue
        if not t and (not uscita or not uscita[-1]):
            continue
        uscita.append(t)
    return "\n".join(uscita).strip()
