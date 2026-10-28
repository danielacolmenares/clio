"""Passo 7 di 7 – Interfaccia web (chat) minimale.

Riusa la stessa logica del passo 6 (stesso prompt, stessi parametri di
determinismo): nessuna logica RAG aggiuntiva. Solo libreria standard
(``http.server``).

Sicurezza:
  - in ascolto su 127.0.0.1 per default; per esporla usare un reverse
    proxy con HTTPS davanti;
  - autenticazione HTTP Basic con gli utenti del file indicato da
    ``CLIO_UTENTI_FILE`` (vedi ``clio.autenticazione``); senza file utenti
    il server non parte, salvo ``--senza-autenticazione`` e solo su
    indirizzo di loopback;
  - una domanda alla volta: se il sistema e' occupato risponde subito 429
    con un messaggio chiaro, invece di lasciare l'utente in attesa.

Nessuna memoria conversazionale: ogni domanda e' indipendente; la
cronologia nella pagina serve solo alla lettura.

Log delle sessioni (minimo): un file JSONL al giorno per KB in
``sessioni/sessioni_<kb>_<AAAAMMGG>.jsonl``, una riga per domanda con
timestamp, kb, utente, domanda, risposta, fonti (citazioni), fuori_dominio,
tempo_totale, errore. Lettura: ``python -m clio.cli sessioni --kb <nome>``.
"""

from __future__ import annotations

import ipaddress
import json
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config, risposta
from .autenticazione import carica_utenti, utente_da_header
from .config import KB

MAX_CORPO = 16 * 1024
MAX_DOMANDA = 2000

INTESTAZIONI_SICUREZZA = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'",
    "Cache-Control": "no-store",
}


class App:
    """Logica HTTP indipendente dal server: testabile senza socket."""

    def __init__(self, kb: KB, utenti: dict[str, str] | None, rispondi=None,
                 top_k: int = risposta.TOP_K, versione_prompt: str = "A", modello: str | None = None):
        self.kb = kb
        self.utenti = utenti            # None = autenticazione disattivata
        self.rispondi = rispondi or risposta.rispondi
        self.top_k = top_k
        self.versione_prompt = versione_prompt
        self.modello = modello or config.modello_llm()
        self._lock_elaborazione = threading.Lock()
        self._lock_log = threading.Lock()
        self._occupato = {"utente": None, "da": None}

    # -- risposte ---------------------------------------------------------
    @staticmethod
    def _json(stato: int, dati: dict, extra: dict | None = None):
        corpo = json.dumps(dati, ensure_ascii=False).encode("utf-8")
        return stato, {"Content-Type": "application/json; charset=utf-8", **(extra or {})}, corpo

    def _non_autorizzato(self):
        return self._json(401, {"errore": "Autenticazione richiesta."},
                          {"WWW-Authenticate": 'Basic realm="Clio", charset="UTF-8"'})

    # -- instradamento ----------------------------------------------------
    def gestisci(self, metodo: str, path: str, headers: dict, corpo: bytes = b""):
        path = path.split("?", 1)[0]
        if metodo == "GET" and path == "/salute":
            return self._json(200, {"stato": "ok"})

        if self.utenti is None:
            utente = "locale"
        else:
            minuscole = {k.lower(): v for k, v in headers.items()}
            utente = utente_da_header(minuscole.get("authorization"), self.utenti)
            if utente is None:
                return self._non_autorizzato()

        if metodo == "GET" and path == "/":
            return 200, {"Content-Type": "text/html; charset=utf-8"}, pagina_html(self.kb, self.modello).encode("utf-8")
        if metodo == "POST" and path == "/api/chiedi":
            return self._chiedi(utente, corpo)
        return self._json(404, {"errore": "Risorsa non trovata."})

    def _chiedi(self, utente: str, corpo: bytes):
        if len(corpo) > MAX_CORPO:
            return self._json(413, {"errore": "Richiesta troppo grande."})
        try:
            dati = json.loads(corpo.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self._json(400, {"errore": "JSON non valido."})
        domanda = (dati.get("domanda") or "").strip() if isinstance(dati, dict) else ""
        if not domanda:
            return self._json(400, {"errore": "Domanda vuota."})
        if len(domanda) > MAX_DOMANDA:
            return self._json(400, {"errore": f"Domanda troppo lunga (massimo {MAX_DOMANDA} caratteri)."})

        if not self._lock_elaborazione.acquire(blocking=False):
            da = self._occupato["da"]
            msg = "Il sistema sta gia' elaborando un'altra domanda"
            if da:
                msg += f" (in corso da {int(time.time() - da)}s)"
            return self._json(429, {"errore": msg + ". Riprova tra poco.", "occupato": True})
        self._occupato.update(utente=utente, da=time.time())
        try:
            esito = self.rispondi(self.kb, domanda, self.top_k, self.versione_prompt, self.modello,
                                  config.min_score())
            uscita = {
                "risposta": esito["risposta"],
                "testo": esito["risposta_modello"] if esito["chiamata_llm"] and not esito["fuori_dominio"]
                else esito["risposta"],
                "fonti": [{"citazione": f["citazione"], "titolo_sezione": f.get("titolo_sezione"),
                           "pagine": f.get("pagine", []), "score": f.get("score")} for f in esito["fonti"]],
                "fuori_dominio": esito["fuori_dominio"],
                "tempo_totale": esito["tempo_totale"],
            }
            self.registra(utente, domanda, uscita)
            return self._json(200, uscita)
        except Exception as exc:
            self.registra(utente, domanda, None, errore=str(exc))
            return self._json(500, {"errore": f"Errore durante l'elaborazione: {exc}"})
        finally:
            self._occupato.update(utente=None, da=None)
            self._lock_elaborazione.release()

    # -- log sessioni -----------------------------------------------------
    def registra(self, utente: str, domanda: str, uscita: dict | None, errore: str | None = None) -> None:
        riga = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "kb": self.kb.nome,
            "utente": utente,
            "domanda": domanda,
            "risposta": uscita.get("risposta") if uscita else None,
            "fonti": [f["citazione"] for f in uscita["fonti"]] if uscita else None,
            "fuori_dominio": uscita.get("fuori_dominio") if uscita else None,
            "tempo_totale": uscita.get("tempo_totale") if uscita else None,
            "errore": errore,
        }
        percorso = self.kb.log_sessioni()
        with self._lock_log:
            percorso.parent.mkdir(parents=True, exist_ok=True)
            with open(percorso, "a", encoding="utf-8") as f:
                f.write(json.dumps(riga, ensure_ascii=False) + "\n")


def crea_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = "Clio"
        sys_version = ""

        def _rispondi(self, metodo: str):
            lunghezza = int(self.headers.get("Content-Length") or 0)
            corpo = self.rfile.read(min(lunghezza, MAX_CORPO + 1)) if lunghezza else b""
            stato, intestazioni, contenuto = app.gestisci(metodo, self.path, dict(self.headers), corpo)
            self.send_response(stato)
            for k, v in {**INTESTAZIONI_SICUREZZA, **intestazioni}.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(contenuto)))
            self.end_headers()
            self.wfile.write(contenuto)

        def do_GET(self):
            self._rispondi("GET")

        def do_POST(self):
            self._rispondi("POST")

        def log_message(self, formato, *args):  # niente domande o credenziali su stderr
            pass

    return Handler


def e_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def avvia(kb: KB, host: str = "127.0.0.1", porta: int = 8080, senza_autenticazione: bool = False,
          versione_prompt: str = "A") -> None:
    if senza_autenticazione:
        if not e_loopback(host):
            raise SystemExit("--senza-autenticazione e' ammesso solo su un indirizzo di loopback.")
        utenti = None
    else:
        percorso = config.file_utenti()
        if not percorso.exists():
            raise SystemExit(f"File utenti non trovato: {percorso}. Crearlo con "
                             f"'python -m clio.cli utente <nome>' oppure impostare CLIO_UTENTI_FILE.")
        utenti = carica_utenti(percorso)
        if not utenti:
            raise SystemExit(f"Nessun utente definito in {percorso}.")
    app = App(kb, utenti, versione_prompt=versione_prompt)
    server = ThreadingHTTPServer((host, porta), crea_handler(app))
    print(f"Clio (demo) – KB '{kb.nome}', collection {kb.collection}, modello {app.modello}")
    print(f"In ascolto su http://{host}:{porta}/  (autenticazione: {'no' if utenti is None else 'si'})")
    print(f"Log sessioni: {kb.log_sessioni()}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def pagina_html(kb: KB, modello: str) -> str:
    return PAGINA.replace("{{KB}}", _esc(kb.nome)).replace("{{MODELLO}}", _esc(modello))


def _esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;").replace("'", "&#39;"))


PAGINA = """<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Clio</title>
<style>
  :root { --primario:#2f4b6e; --sfondo:#f5f6f8; --bordo:#d9dee3; --testo:#1c1c1c; --bolla:#ffffff; }
  @media (prefers-color-scheme: dark) {
    :root { --primario:#7fa3cf; --sfondo:#15181c; --bordo:#2c3238; --testo:#e6e6e6; --bolla:#1f2429; }
  }
  * { box-sizing: border-box; }
  body { margin:0; font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
         background:var(--sfondo); color:var(--testo); height:100vh; display:flex; flex-direction:column; }
  header { padding:14px 20px; border-bottom:1px solid var(--bordo); display:flex; flex-wrap:wrap;
           gap:4px 16px; align-items:baseline; justify-content:space-between; }
  header h1 { font-size:18px; margin:0; }
  header span { font-size:12px; opacity:.75; }
  #banner { background:#fff4d6; color:#5c4400; padding:8px 20px; font-size:13px; text-align:center; }
  #chat { flex:1; overflow-y:auto; padding:20px 16px; max-width:840px; width:100%; margin:0 auto; }
  .msg { margin-bottom:18px; display:flex; flex-direction:column; }
  .utente { align-items:flex-end; } .assistente { align-items:flex-start; }
  .bolla { max-width:85%; padding:10px 14px; border-radius:12px; line-height:1.5; white-space:pre-wrap;
           background:var(--bolla); border:1px solid var(--bordo); }
  .utente .bolla { background:var(--primario); color:#fff; border-color:var(--primario); }
  .fonti { max-width:85%; margin-top:6px; font-size:12px; }
  .fonte { display:inline-block; border:1px solid var(--bordo); border-radius:6px; padding:2px 8px; margin:2px 4px 2px 0; }
  .meta { font-size:11px; opacity:.6; margin-top:4px; }
  .errore { border-color:#d9534f; color:#b52b27; }
  form { border-top:1px solid var(--bordo); padding:12px 16px; display:flex; justify-content:center; }
  #riga { max-width:840px; width:100%; display:flex; gap:8px; }
  input { flex:1; padding:10px 12px; border:1px solid var(--bordo); border-radius:8px; font-size:15px;
          background:var(--bolla); color:var(--testo); min-width:0; }
  button { background:var(--primario); color:#fff; border:0; border-radius:8px; padding:0 18px; font-size:15px; cursor:pointer; }
  button:disabled { opacity:.5; cursor:not-allowed; }
</style>
</head>
<body>
<header>
  <h1>Clio</h1>
  <span>KB: {{KB}} · {{MODELLO}} · risponde solo dai documenti indicizzati e cita le fonti</span>
</header>
<div id="banner"><strong>Demo.</strong> Le risposte possono essere lente o imprecise: verifica sempre le fonti citate.</div>
<div id="chat"></div>
<form id="form">
  <div id="riga">
    <input id="domanda" type="text" placeholder="Fai una domanda sui documenti..." autocomplete="off" autofocus maxlength="2000">
    <button id="invia" type="submit">Invia</button>
  </div>
</form>
<script>
const chat = document.getElementById('chat');
const input = document.getElementById('domanda');
const bottone = document.getElementById('invia');

function aggiungi(classe, testo, extraClasse) {
  const div = document.createElement('div');
  div.className = 'msg ' + classe;
  const b = document.createElement('div');
  b.className = 'bolla' + (extraClasse ? ' ' + extraClasse : '');
  b.textContent = testo;
  div.appendChild(b);
  chat.appendChild(div);
  chat.scrollTop = chat.scrollHeight;
  return div;
}

function pagine(p) {
  if (!p || !p.length) return '';
  return p.length === 1 ? ' · pag. ' + p[0] : ' · pagg. ' + p[0] + '-' + p[p.length - 1];
}

document.getElementById('form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const domanda = input.value.trim();
  if (!domanda) return;
  aggiungi('utente', domanda);
  input.value = '';
  bottone.disabled = true;
  const attesa = aggiungi('assistente', 'Sto cercando nei documenti...');
  try {
    const resp = await fetch('/api/chiedi', {method: 'POST', headers: {'Content-Type': 'application/json'},
                                             body: JSON.stringify({domanda})});
    const dati = await resp.json();
    attesa.remove();
    if (dati.errore) { aggiungi('assistente', 'Errore: ' + dati.errore, 'errore'); return; }
    const div = aggiungi('assistente', dati.testo);
    if (dati.fonti && dati.fonti.length) {
      const f = document.createElement('div');
      f.className = 'fonti';
      f.appendChild(document.createTextNode('Fonti: '));
      dati.fonti.forEach(x => {
        const s = document.createElement('span');
        s.className = 'fonte';
        s.title = x.titolo_sezione || '';
        s.textContent = '[' + x.citazione + ']' + pagine(x.pagine);
        f.appendChild(s);
      });
      div.appendChild(f);
    }
    const m = document.createElement('div');
    m.className = 'meta';
    m.textContent = 'Tempo di risposta: ' + dati.tempo_totale + 's';
    div.appendChild(m);
  } catch (err) {
    attesa.remove();
    aggiungi('assistente', 'Impossibile contattare il server.', 'errore');
  } finally {
    bottone.disabled = false;
    input.focus();
  }
});
</script>
</body>
</html>
"""
