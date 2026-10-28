"""Lettura dei log delle sessioni dell'interfaccia web (passo 7).

Legge tutti i file ``sessioni/sessioni_<kb>_<AAAAMMGG>.jsonl`` della KB e
li mostra in ordine cronologico. Filtri combinabili: utente, giorno, solo
errori, formato breve (una riga per domanda).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from . import config


def trova_file(nome_kb: str, giorno: str | None = None) -> list[Path]:
    cartella = config.sessioni_dir()
    if not cartella.is_dir():
        return []
    schema = f"sessioni_{nome_kb}_{giorno}.jsonl" if giorno else f"sessioni_{nome_kb}_*.jsonl"
    return sorted(cartella.glob(schema))


def leggi(file_trovati: list[Path]) -> list[dict]:
    righe = []
    for percorso in file_trovati:
        for linea in percorso.read_text(encoding="utf-8").splitlines():
            if not linea.strip():
                continue
            try:
                righe.append(json.loads(linea))
            except json.JSONDecodeError:
                print(f"AVVISO: riga non leggibile in {percorso.name}, saltata.", file=sys.stderr)
    righe.sort(key=lambda r: r.get("timestamp", ""))
    return righe


def filtra(righe: list[dict], utente: str | None = None, solo_errori: bool = False) -> list[dict]:
    if utente:
        righe = [r for r in righe if r.get("utente") == utente]
    if solo_errori:
        righe = [r for r in righe if r.get("errore")]
    return righe


def formatta(righe: list[dict], breve: bool = False) -> str:
    out = []
    for r in righe:
        if breve:
            tag = " [ERRORE]" if r.get("errore") else (" [fuori dominio]" if r.get("fuori_dominio") else "")
            out.append(f"[{r.get('timestamp')}] {r.get('utente', ''):<12} {r.get('domanda')}{tag}")
            continue
        out += ["=" * 70, f"Quando:   {r.get('timestamp')}", f"Utente:   {r.get('utente')}",
                f"Domanda:  {r.get('domanda')}"]
        if r.get("errore"):
            out.append(f"ERRORE:   {r['errore']}")
        else:
            out.append(f"Risposta: {r.get('risposta')}")
            if r.get("tempo_totale") is not None:
                out.append(f"Tempo:    {r['tempo_totale']}s")
        out.append("")
    return "\n".join(out)


def esegui(nome_kb: str, utente: str | None = None, giorno: str | None = None,
           solo_errori: bool = False, breve: bool = False) -> int:
    file_trovati = trova_file(nome_kb, giorno)
    if not file_trovati:
        print(f"Nessun file di sessioni per la KB '{nome_kb}'" + (f" nel giorno {giorno}" if giorno else "")
              + f" in {config.sessioni_dir()}.")
        return 0
    righe = filtra(leggi(file_trovati), utente, solo_errori)
    print(f"KB: {nome_kb} | file letti: {len(file_trovati)} | interazioni: {len(righe)}\n")
    print(formatta(righe, breve) if righe else "Nessuna interazione corrisponde ai filtri.")
    return 0
