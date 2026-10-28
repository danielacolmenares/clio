"""CLI e flusso completo 1-6 con servizi simulati (nessuna rete)."""

import csv
import json

from clio import cli, config, retrieval, sessioni
from tests.pdf_minimo import crea_pdf


def test_cli_kb_inesistente(base, capsys):
    assert cli.main(["chunk", "--kb", "inesistente"]) == 1
    assert "non trovata" in capsys.readouterr().err


def test_cli_elenco_kb(base, capsys):
    assert cli.main(["kb"]) == 0
    assert "demo  (collection clio_demo)" in capsys.readouterr().out


def test_flusso_completo(base, servizi, capsys):
    kb = config.get_kb("demo")
    (kb.corpus / "regolamento.pdf").write_bytes(crea_pdf([
        ["2.1.2099 IT Gazzetta ufficiale dell'Unione europea L 1/1",
         "REGOLAMENTO (UE) 2099/123 DEL PARLAMENTO EUROPEO E DEL CONSIGLIO",
         "relativo ai dati di prova", "considerando quanto segue:",
         "(1) I dati di prova meritano protezione.", "HANNO ADOTTATO IL PRESENTE REGOLAMENTO:",
         "CAPO I", "Disposizioni generali", "Articolo 1", "Oggetto",
         "1. Il presente regolamento tutela i dati di prova."],
        ["L 1/2 IT Gazzetta ufficiale dell'Unione europea 2.1.2099",
         "Articolo 2", "Notifica delle violazioni",
         "1. La violazione dei dati di prova e' notificata entro 72 ore.",
         "Fatto a Bruxelles, il 2 gennaio 2099."],
    ]))
    # Passi 1-4 con il comando "tutto" (numeri e nomi equivalenti)
    assert cli.main(["tutto", "--kb", "demo"]) == 0
    punti = servizi.collezioni["clio_demo"]["punti"]
    citazioni = sorted(p["payload"]["citazione"] for p in punti.values())
    assert citazioni == ["GDPR, art. 1", "GDPR, art. 2", "GDPR, considerando 1"]
    art2 = next(p for p in punti.values() if p["payload"]["citazione"] == "GDPR, art. 2")
    assert art2["payload"]["pagine"] == [2]
    assert "Gazzetta" not in art2["payload"]["testo"]

    # Passo 5
    with open(kb.golden_set, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(retrieval.COLONNE_GOLDEN)
        w.writerow(["GS001", "violazione dei dati di prova notificata entro 72 ore", "GDPR", "art. 2",
                    "Entro 72 ore", "fattuale", "facile", "breve"])
    assert cli.main(["5", "--kb", "demo", "--top-k", "3"]) == 0
    risultati = list(csv.DictReader(open(next((base / "report").glob("retrieval_results_demo_*.csv")))))
    assert risultati[0]["esito"] == "OK"

    # Passo 6
    servizi.risposta_llm = "Entro 72 ore [GDPR, art. 2]."
    kb.negative_set.write_text("id,domanda,nota\nNEG001,ricetta pizza,cucina\n", encoding="utf-8")
    assert cli.main(["risposta", "--kb", "demo", "--set", "golden", "--prompt", "b"]) == 0
    righe = list(csv.DictReader(open(next((base / "report").glob("risposta_results_golden_demo_*.csv")))))
    assert righe[0]["fonti"].startswith("[GDPR, art. 2]")
    assert righe[0]["citazioni_non_recuperate"] == ""
    assert "FONTE 1 – [GDPR, art. 2]" in servizi.prompt[-1]


def test_sessioni(base, capsys):
    cartella = base / "sessioni"
    cartella.mkdir()
    (cartella / "sessioni_demo_20990102.jsonl").write_text(
        json.dumps({"timestamp": "2099-01-02T10:00:00", "utente": "anna", "domanda": "B", "errore": "x"}) + "\n",
        encoding="utf-8")
    (cartella / "sessioni_demo_20990101.jsonl").write_text(
        json.dumps({"timestamp": "2099-01-01T10:00:00", "utente": "bruno", "domanda": "A",
                    "risposta": "R", "fuori_dominio": True}) + "\nnon json\n", encoding="utf-8")
    righe = sessioni.leggi(sessioni.trova_file("demo"))
    assert [r["domanda"] for r in righe] == ["A", "B"]
    assert [r["domanda"] for r in sessioni.filtra(righe, solo_errori=True)] == ["B"]
    assert [r["domanda"] for r in sessioni.filtra(righe, utente="bruno")] == ["A"]
    assert cli.main(["sessioni", "--kb", "demo", "--breve", "--giorno", "20990101"]) == 0
    out = capsys.readouterr().out
    assert "bruno" in out and "[fuori dominio]" in out and "anna" not in out
