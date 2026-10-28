import base64
import json
import threading

import pytest

from clio import autenticazione, config, web
from clio.risposta import FRASE_FUORI_DOMINIO


def basic(nome, pw):
    return {"Authorization": "Basic " + base64.b64encode(f"{nome}:{pw}".encode()).decode()}


def esito_finto(kb, domanda, top_k, versione, modello, min_score):
    return {"risposta": "Entro 72 ore [GDPR, art. 33].\n\nFonti:\n- [GDPR, art. 33]",
            "risposta_modello": "Entro 72 ore [GDPR, art. 33].", "chiamata_llm": True,
            "fuori_dominio": False, "tempo_totale": 1.2,
            "fonti": [{"citazione": "GDPR, art. 33", "titolo_sezione": "Articolo 33 – Notifica",
                       "pagine": [52, 53], "score": 0.8}]}


@pytest.fixture
def app(base):
    utenti = {"anna": autenticazione.hash_password("segreta", iterazioni=1000)}
    return web.App(config.get_kb("demo"), utenti, rispondi=esito_finto, modello="m:1")


def test_hash_e_verifica_password():
    voce = autenticazione.hash_password("pw", iterazioni=1000)
    assert voce.startswith("pbkdf2_sha256$1000$") and "pw" not in voce.split("$")[2:]
    assert autenticazione.verifica_password("pw", voce)
    assert not autenticazione.verifica_password("PW", voce)
    assert not autenticazione.verifica_password("pw", "md5$x")


def test_file_utenti(tmp_path):
    f = tmp_path / "utenti.htpasswd"
    autenticazione.salva_utente(f, "anna", "uno")
    autenticazione.salva_utente(f, "bruno", "due")
    autenticazione.salva_utente(f, "anna", "tre")                # aggiornamento
    utenti = autenticazione.carica_utenti(f)
    assert sorted(utenti) == ["anna", "bruno"]
    assert autenticazione.verifica_password("tre", utenti["anna"])
    assert "tre" not in f.read_text()
    f.write_text("# commento\n\nanna:testo-in-chiaro\n")
    with pytest.raises(autenticazione.ErroreUtenti, match="riga 3"):
        autenticazione.carica_utenti(f)
    with pytest.raises(autenticazione.ErroreUtenti):
        autenticazione.salva_utente(f, "nome con spazi", "x")


def test_autenticazione_richiesta(app):
    stato, intest, _ = app.gestisci("GET", "/", {})
    assert stato == 401 and intest["WWW-Authenticate"].startswith("Basic")
    assert app.gestisci("GET", "/", basic("anna", "sbagliata"))[0] == 401
    assert app.gestisci("GET", "/", basic("ignoto", "segreta"))[0] == 401
    assert app.gestisci("GET", "/", {"Authorization": "Basic !!!"})[0] == 401
    stato, intest, corpo = app.gestisci("GET", "/", basic("anna", "segreta"))
    assert stato == 200 and intest["Content-Type"].startswith("text/html")
    html = corpo.decode()
    assert "Demo." in html and "<title>Clio</title>" in html
    assert app.gestisci("GET", "/salute", {})[0] == 200                       # senza credenziali
    assert app.gestisci("GET", "/x", {"authorization": basic("anna", "segreta")["Authorization"]})[0] == 404


def test_chiedi_restituisce_citazioni_e_registra_sessione(app, base):
    stato, _, corpo = app.gestisci("POST", "/api/chiedi", basic("anna", "segreta"),
                                   json.dumps({"domanda": "Entro quando notificare?"}).encode())
    dati = json.loads(corpo)
    assert stato == 200
    assert dati["testo"] == "Entro 72 ore [GDPR, art. 33]."
    assert dati["fonti"][0]["citazione"] == "GDPR, art. 33" and dati["fonti"][0]["pagine"] == [52, 53]
    log = config.get_kb("demo").log_sessioni()
    riga = json.loads(log.read_text(encoding="utf-8").strip())
    assert riga["utente"] == "anna" and riga["kb"] == "demo"
    assert riga["fonti"] == ["GDPR, art. 33"] and riga["errore"] is None
    assert set(riga) == {"timestamp", "kb", "utente", "domanda", "risposta", "fonti",
                         "fuori_dominio", "tempo_totale", "errore"}


@pytest.mark.parametrize("corpo, atteso", [
    (b"", 400), (b"{non json", 400), (json.dumps({"domanda": "  "}).encode(), 400),
    (json.dumps({"domanda": "x" * 2001}).encode(), 400), (b"x" * (web.MAX_CORPO + 1), 413),
])
def test_chiedi_input_non_valido(app, corpo, atteso):
    assert app.gestisci("POST", "/api/chiedi", basic("anna", "segreta"), corpo)[0] == atteso


def test_sistema_occupato(base):
    entrato, rilascia = threading.Event(), threading.Event()

    def lento(*args):
        entrato.set()
        rilascia.wait(5)
        return esito_finto(*args)

    app = web.App(config.get_kb("demo"), None, rispondi=lento, modello="m:1")
    corpo = json.dumps({"domanda": "prima"}).encode()
    t = threading.Thread(target=app.gestisci, args=("POST", "/api/chiedi", {}, corpo))
    t.start()
    entrato.wait(5)
    stato, _, risposta = app.gestisci("POST", "/api/chiedi", {}, json.dumps({"domanda": "seconda"}).encode())
    rilascia.set()
    t.join(5)
    assert stato == 429 and json.loads(risposta)["occupato"] is True


def test_errore_registrato(base):
    def guasto(*args):
        raise RuntimeError("servizio non raggiungibile")

    app = web.App(config.get_kb("demo"), None, rispondi=guasto, modello="m:1")
    stato, _, corpo = app.gestisci("POST", "/api/chiedi", {}, json.dumps({"domanda": "x"}).encode())
    assert stato == 500 and "non raggiungibile" in json.loads(corpo)["errore"]
    riga = json.loads(config.get_kb("demo").log_sessioni().read_text().strip())
    assert riga["utente"] == "locale" and "non raggiungibile" in riga["errore"]


def test_fuori_dominio_passa_dal_flusso_reale(base, servizi):
    servizi.risultati_ricerca = []
    app = web.App(config.get_kb("demo"), None, modello="m:1")
    stato, _, corpo = app.gestisci("POST", "/api/chiedi", {}, json.dumps({"domanda": "pizza?"}).encode())
    dati = json.loads(corpo)
    assert stato == 200 and dati["testo"] == FRASE_FUORI_DOMINIO and dati["fonti"] == []
    assert dati["fuori_dominio"] is True


def test_avvio_rifiutato_senza_utenti_o_su_rete(base):
    kb = config.get_kb("demo")
    with pytest.raises(SystemExit, match="loopback"):
        web.avvia(kb, host="0.0.0.0", senza_autenticazione=True)
    with pytest.raises(SystemExit, match="File utenti non trovato"):
        web.avvia(kb)
    assert web.e_loopback("127.0.0.1") and web.e_loopback("::1") and web.e_loopback("localhost")
    assert not web.e_loopback("0.0.0.0")


def test_html_escape(base):
    assert "&lt;b&gt;" in web.pagina_html(config.get_kb("demo"), "<b>")
