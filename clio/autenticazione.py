"""Utenti dell'interfaccia web (autenticazione HTTP Basic).

Gli utenti stanno in un file di testo fuori dal repository (default
``<base>/utenti.htpasswd``, oppure ``CLIO_UTENTI_FILE``), una riga per
utente::

    nome:pbkdf2_sha256$<iterazioni>$<sale base64>$<hash base64>

Le righe vuote e quelle che iniziano con '#' sono ignorate. Le password non
sono mai salvate in chiaro. Per aggiungere o aggiornare un utente:

    python -m clio.cli utente <nome>
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
from pathlib import Path

ALGORITMO = "pbkdf2_sha256"
ITERAZIONI = 310_000
RE_NOME = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class ErroreUtenti(ValueError):
    pass


def hash_password(password: str, sale: bytes | None = None, iterazioni: int = ITERAZIONI) -> str:
    if not password:
        raise ErroreUtenti("Password vuota.")
    sale = sale or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), sale, iterazioni)
    return "$".join([ALGORITMO, str(iterazioni),
                     base64.b64encode(sale).decode(), base64.b64encode(digest).decode()])


def verifica_password(password: str, voce: str) -> bool:
    try:
        algoritmo, iterazioni, sale, atteso = voce.split("$")
        if algoritmo != ALGORITMO:
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                     base64.b64decode(sale), int(iterazioni))
        return hmac.compare_digest(digest, base64.b64decode(atteso))
    except (ValueError, TypeError):
        return False


def carica_utenti(percorso: Path) -> dict[str, str]:
    utenti: dict[str, str] = {}
    for n, riga in enumerate(percorso.read_text(encoding="utf-8").splitlines(), start=1):
        riga = riga.strip()
        if not riga or riga.startswith("#"):
            continue
        nome, sep, voce = riga.partition(":")
        if not sep or not RE_NOME.match(nome) or not voce.startswith(ALGORITMO + "$"):
            raise ErroreUtenti(f"{percorso}, riga {n}: formato non valido (atteso nome:{ALGORITMO}$...).")
        utenti[nome] = voce
    return utenti


def salva_utente(percorso: Path, nome: str, password: str) -> None:
    if not RE_NOME.match(nome):
        raise ErroreUtenti("Nome utente non valido: usare lettere, cifre, '.', '_' o '-'.")
    utenti = carica_utenti(percorso) if percorso.exists() else {}
    utenti[nome] = hash_password(password)
    percorso.parent.mkdir(parents=True, exist_ok=True)
    percorso.write_text("".join(f"{u}:{v}\n" for u, v in sorted(utenti.items())), encoding="utf-8")
    try:
        os.chmod(percorso, 0o600)
    except OSError:
        pass


def utente_da_header(authorization: str | None, utenti: dict[str, str]) -> str | None:
    """Nome dell'utente autenticato dall'header Authorization, o None."""
    if not authorization or not authorization.startswith("Basic "):
        return None
    try:
        decodificato = base64.b64decode(authorization[6:].strip(), validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    nome, sep, password = decodificato.partition(":")
    voce = utenti.get(nome)
    if not sep or voce is None:
        return None
    return nome if verifica_password(password, voce) else None
