import io
import json
import urllib.error
import urllib.request

import pytest

from clio import clienti, config, embedding, qdrant


class RispostaFinta(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_guardia_rete_attiva():
    import socket
    with pytest.raises(RuntimeError, match="vietato"):
        socket.create_connection(("localhost", 80))
    with pytest.raises(RuntimeError, match="vietato"):
        urllib.request.urlopen("http://servizio.test/", timeout=1)


def test_http_json_retry_poi_successo(monkeypatch):
    tentativi = []

    def urlopen(req, timeout):
        tentativi.append(req.full_url)
        if len(tentativi) < 3:
            raise urllib.error.URLError("rifiutata")
        return RispostaFinta(b'{"ok": true}')

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    assert clienti.http_json("POST", "http://servizio.test/x", {"a": 1}) == {"ok": True}
    assert len(tentativi) == 3


def test_http_json_404_e_4xx(monkeypatch):
    chiamate = []

    def urlopen(req, timeout):
        chiamate.append(1)
        raise urllib.error.HTTPError(req.full_url, int(req.full_url[-3:]), "x", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    assert clienti.http_json("GET", "http://servizio.test/404", consenti_404=True) == clienti.NON_TROVATO
    with pytest.raises(clienti.ErroreServizio):
        clienti.http_json("GET", "http://servizio.test/400")
    assert len(chiamate) == 2           # 400: nessun nuovo tentativo


def test_http_json_esaurisce_tentativi(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout: (_ for _ in ()).throw(TimeoutError("lento")))
    with pytest.raises(clienti.ErroreServizio, match="lento"):
        clienti.http_json("GET", "http://servizio.test/")


def test_ollama_generate_deterministico(servizi):
    assert clienti.ollama_generate("ciao", "m:1") == "Risposta [GDPR, art. 4]."
    _, url, corpo = servizi.chiamate[-1]
    assert url == "http://127.0.0.1:11434/api/generate"
    assert corpo["options"] == {"temperature": 0, "seed": 42, "num_ctx": 8192}
    assert corpo["stream"] is False and corpo["model"] == "m:1"


def test_ollama_embed_risposta_non_valida(monkeypatch):
    monkeypatch.setattr(clienti, "http_json", lambda *a, **k: {"embeddings": []})
    with pytest.raises(clienti.ErroreServizio):
        clienti.ollama_embed("x")


def test_controllo_dimensione(servizi):
    servizi.dim = 512
    with pytest.raises(embedding.DimensioneErrata, match="512"):
        embedding.vettorizza("testo", "bge-m3", 1024)
    servizi.dim = 1024
    rec = embedding.elabora_record({"id": "CH000001", "testo": "articolo"}, "bge-m3", 1024)
    assert rec["embedding_dimension"] == 1024 and rec["embedding_model"] == "bge-m3"
    with pytest.raises(ValueError, match="testo"):
        embedding.elabora_record({"id": "CH000002", "testo": "  "}, "bge-m3", 1024)


def test_costruisci_punto():
    rec = {"id": "CH000042", "documento": "GDPR", "citazione": "GDPR, art. 33",
           "pagine": [52], "testo": "t", "embedding": [0.1] * 4}
    punto = qdrant.costruisci_punto(rec, 4)
    assert punto["id"] == 42
    assert "embedding" not in punto["payload"]
    assert punto["payload"]["citazione"] == "GDPR, art. 33" and punto["payload"]["pagine"] == [52]
    with pytest.raises(ValueError, match="dimensione"):
        qdrant.costruisci_punto(rec, 1024)
    with pytest.raises(ValueError):
        qdrant.id_numerico("senza-cifre")


def test_ricrea_collection(servizi):
    qdrant.ricrea_collection("clio_demo", 1024)
    metodi = [(m, u.split("6333")[1]) for m, u, _ in servizi.chiamate]
    assert metodi == [("GET", "/collections/clio_demo"), ("PUT", "/collections/clio_demo")]
    assert servizi.chiamate[-1][2] == {"vectors": {"size": 1024, "distance": "Cosine"}}

    servizi.collezioni["clio_demo"]["punti"][1] = {"id": 1}
    servizi.chiamate.clear()
    qdrant.ricrea_collection("clio_demo", 1024)
    assert [m for m, _, _ in servizi.chiamate] == ["GET", "DELETE", "PUT"]
    assert servizi.collezioni["clio_demo"]["punti"] == {}          # collection pulita


def _prepara_chunk(kb, n=3):
    cartella = kb.chunk / "doc"
    cartella.mkdir(parents=True)
    for i in range(1, n + 1):
        (cartella / f"chunk_{i:04d}.json").write_text(json.dumps({
            "id": f"CH{i:06d}", "documento": "GDPR", "sezione": f"art. {i}",
            "citazione": f"GDPR, art. {i}", "testo": f"articolo numero {i} testo"}), encoding="utf-8")


def test_passi_3_e_4(base, servizi, monkeypatch):
    kb = config.get_kb("demo")
    _prepara_chunk(kb, 70)
    assert embedding.esegui(kb) == 0
    salvati = sorted(kb.embedding.rglob("*.json"))
    assert len(salvati) == 70
    assert len(json.loads(salvati[0].read_text())["embedding"]) == 1024

    assert qdrant.esegui(kb) == 0
    punti = servizi.collezioni["clio_demo"]["punti"]
    assert sorted(punti) == list(range(1, 71))
    assert servizi.conteggio("points?wait=true") == 2               # batch da 64
    assert punti[5]["payload"]["citazione"] == "GDPR, art. 5"
    report = next((base / "report").glob("qdrant_report_demo_*.txt")).read_text(encoding="utf-8")
    assert "Punti caricati: 70" in report


def test_passo_3_dimensione_errata_esce_con_errore(base, servizi, monkeypatch):
    monkeypatch.setenv("CLIO_EMBED_DIM", "768")
    kb = config.get_kb("demo")
    _prepara_chunk(kb, 2)
    assert embedding.esegui(kb) == 2
    assert list(kb.embedding.rglob("*.json")) == []


def test_passo_4_modelli_misti(base, servizi):
    kb = config.get_kb("demo")
    (kb.embedding / "d").mkdir(parents=True)
    for i, modello in enumerate(["bge-m3", "altro"], start=1):
        (kb.embedding / "d" / f"chunk_{i}.json").write_text(json.dumps(
            {"id": f"CH{i:06d}", "embedding_model": modello, "embedding": [0.0] * 1024}))
    assert qdrant.esegui(kb) == 1
    assert servizi.collezioni == {}                                # nessuna modifica a Qdrant
