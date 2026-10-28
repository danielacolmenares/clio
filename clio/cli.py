"""Punto di ingresso unico della pipeline.

    python -m clio.cli <passo> --kb <nome> [opzioni]

Passi (in ordine):
    1  parse      PDF/DOCX del corpus -> testo con marcatori di pagina
    2  chunk      testo -> chunk JSON con metadati (profilo da kb.json)
    3  embed      chunk -> vettori (Ollama)
    4  upload     vettori -> collection Qdrant clio_<kb>, ricreata da zero
    5  retrieval  valutazione sul Golden Set (OK/PARZIALE/FALLITO, hit rate)
    6  risposta   risposte LLM su Golden/Negative Set -> CSV per revisione umana
    7  web        chat web (127.0.0.1, autenticazione Basic)

Comandi di servizio:
    tutto      esegue i passi 1-4 in sequenza, fermandosi al primo errore
    kb         elenca le KB disponibili
    sessioni   legge i log delle sessioni web
    utente     aggiunge/aggiorna un utente dell'interfaccia web

I passi si possono indicare anche con il numero: ``python -m clio.cli 2 --kb demo``.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from . import config

ALIAS = {"1": "parse", "2": "chunk", "3": "embed", "4": "upload",
         "5": "retrieval", "6": "risposta", "7": "web"}


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m clio.cli", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="comando", required=True, metavar="passo")

    def con_kb(nome: str, aiuto: str) -> argparse.ArgumentParser:
        s = sub.add_parser(nome, help=aiuto)
        s.add_argument("--kb", required=True, help="nome della KB (cartella kb/<nome>/)")
        return s

    con_kb("parse", "1. estrazione testo da PDF/DOCX")
    con_kb("chunk", "2. chunking con il profilo della KB")
    con_kb("embed", "3. embedding dei chunk")
    con_kb("upload", "4. caricamento su Qdrant (collection ricreata)")
    s = con_kb("retrieval", "5. valutazione retrieval sul Golden Set")
    s.add_argument("--top-k", type=int, default=5)
    s = con_kb("risposta", "6. risposte LLM su Golden/Negative Set")
    s.add_argument("--set", dest="quale", choices=["golden", "negative", "entrambi"], default="entrambi")
    s.add_argument("--modello", default=None, help="modello Ollama (default: CLIO_LLM_MODEL)")
    s.add_argument("--prompt", default="A", choices=["A", "B", "a", "b"])
    s.add_argument("--top-k", type=int, default=3)
    s = con_kb("web", "7. interfaccia web")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--porta", type=int, default=8080)
    s.add_argument("--prompt", default="A", choices=["A", "B", "a", "b"])
    s.add_argument("--senza-autenticazione", action="store_true",
                   help="solo per prove locali su loopback")
    con_kb("tutto", "passi 1-4 in sequenza")
    sub.add_parser("kb", help="elenca le KB disponibili")
    s = con_kb("sessioni", "legge i log delle sessioni web")
    s.add_argument("--utente")
    s.add_argument("--giorno", help="AAAAMMGG")
    s.add_argument("--solo-errori", action="store_true")
    s.add_argument("--breve", action="store_true")
    s = sub.add_parser("utente", help="aggiunge/aggiorna un utente web (password richiesta a terminale)")
    s.add_argument("nome")
    return p


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ALIAS:
        argv[0] = ALIAS[argv[0]]
    args = _parser().parse_args(argv)

    try:
        if args.comando == "kb":
            elenco = config.elenco_kb()
            print("\n".join(f"{n}  (collection clio_{n.replace('-', '_')})" for n in elenco)
                  or f"Nessuna KB in {config.kb_root()}")
            return 0
        if args.comando == "utente":
            from .autenticazione import salva_utente
            percorso = config.file_utenti()
            pw = getpass.getpass(f"Password per '{args.nome}': ")
            if pw != getpass.getpass("Ripeti la password: "):
                print("Le password non coincidono.")
                return 1
            salva_utente(percorso, args.nome, pw)
            print(f"Utente '{args.nome}' salvato in {percorso}")
            return 0
        if args.comando == "sessioni":
            from . import sessioni
            config.get_kb(args.kb)
            return sessioni.esegui(args.kb, args.utente, args.giorno, args.solo_errori, args.breve)

        kb = config.get_kb(args.kb)
        if args.comando == "parse":
            from . import parser
            return parser.esegui(kb)
        if args.comando == "chunk":
            from . import chunking
            return chunking.esegui(kb)
        if args.comando == "embed":
            from . import embedding
            return embedding.esegui(kb)
        if args.comando == "upload":
            from . import qdrant
            return qdrant.esegui(kb)
        if args.comando == "retrieval":
            from . import retrieval
            return retrieval.esegui(kb, top_k=args.top_k)
        if args.comando == "risposta":
            from . import risposta
            return risposta.esegui(kb, args.quale, args.prompt.upper(), args.modello, args.top_k)
        if args.comando == "web":
            from . import web
            web.avvia(kb, args.host, args.porta, args.senza_autenticazione, args.prompt.upper())
            return 0
        if args.comando == "tutto":
            from . import chunking, embedding, parser, qdrant
            for nome, passo in (("1 parse", parser), ("2 chunk", chunking),
                                ("3 embed", embedding), ("4 upload", qdrant)):
                print(f"\n=== Passo {nome} ===")
                codice = passo.esegui(kb)
                if codice:
                    print(f"Passo {nome} terminato con codice {codice}: sequenza interrotta.")
                    return codice
            return 0
    except config.ErroreConfigurazione as exc:
        print(f"ERRORE: {exc}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    sys.exit(main())
