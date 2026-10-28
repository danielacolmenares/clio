import json

from clio import chunking, config
from clio.profili import sezioni_numerate as S

DOCUMENTO = """[PAGINA 1]
Codice: PR-001
Titolo: Procedura di gestione degli incidenti
Versione: 2
Sommario
1 Scopo ........................ 1
2 Campo di applicazione ........ 1
3 Gestione ..................... 2
3.1 Rilevamento ................ 2
Pag. 1 a 3
[PAGINA 2]
1 Scopo
Definire come gestire gli incidenti.
2 Campo di applicazione
Si applica a tutti i sistemi.
[TABELLA]
3 Sistemi critici | Responsabile
[/TABELLA]
3 Gestione
Le fasi sono le seguenti:
1. Isolamento del sistema.
2. Analisi.
Pag. 2 a 3
[PAGINA 3]
3.1 Rilevamento
Gli incidenti sono rilevati dal monitoraggio.
1 Riga di tabella che non e' una sezione
Pag. 3 a 3
"""


def test_segmenta_con_sommario():
    r = S.segmenta(DOCUMENTO)
    assert [s.sezione for s in r.sezioni] == ["1", "2", "3", "3.1"]
    assert r.metadati == {"codice": "PR-001", "titolo": "Procedura di gestione degli incidenti",
                          "versione": "2"}
    s31 = r.sezioni[-1]
    assert s31.livello == 2 and s31.parent == "3" and s31.path == ["3", "3.1"]
    assert s31.etichetta == "sez. 3.1"
    assert s31.pagine == [3]
    testi = {s.sezione: "\n".join(t for t, _ in s.corpo) for s in r.sezioni}
    assert "3 Sistemi critici" in testi["2"]                   # riga di tabella non e' un titolo
    assert "1. Isolamento" in testi["3"]                       # passo numerato non e' un titolo
    assert "1 Riga di tabella" in testi["3.1"]                 # numero inferiore: contenuto
    assert all("Pag." not in t for t in testi.values())        # footer rimosso


def test_senza_sommario_euristica_e_avviso():
    r = S.segmenta("[PAGINA 1]\n1 Introduzione\nTesto.\n2 Requisiti\nAltro testo.\n1 Nota a margine")
    assert [s.sezione for s in r.sezioni] == ["1", "2"]
    assert any("Nessun sommario" in a for a in r.avvisi)


def test_zero_sezioni_avviso_esplicito():
    r = S.segmenta("[PAGINA 1]\nModulo da compilare\nNome: ____")
    assert r.sezioni == []
    assert any("zero chunk" in a for a in r.avvisi)


def test_titoli_simili():
    assert S.titoli_simili("Campo di applicazione", "Campo di applicazione")
    assert not S.titoli_simili("Sistemi critici", "Gestione")


def test_chunk_record_profilo_numerato(base):
    (base / "kb" / "proc" / "corpus").mkdir(parents=True)
    (base / "kb" / "proc" / "testo_estratto").mkdir()
    (base / "kb" / "proc" / "testo_estratto" / "pr001.txt").write_text(DOCUMENTO, encoding="utf-8")
    kb = config.get_kb("proc")
    assert kb.opzioni["profilo_chunking"] == "sezioni_numerate"
    chunking.esegui(kb)
    rec = json.loads((kb.chunk / "pr001" / "chunk_0004.json").read_text(encoding="utf-8"))
    assert rec["sezione"] == "3.1"
    assert rec["citazione"] == "pr001, sez. 3.1"
    assert rec["codice"] == "PR-001" and rec["versione"] == "2"
    assert rec["titolo_documento"] == "Procedura di gestione degli incidenti"
    assert rec["testo"].startswith("3.1 Rilevamento\n\nGli incidenti")
    assert rec["pagine"] == [3]
