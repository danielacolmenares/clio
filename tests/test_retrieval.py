import csv
import hashlib

import pytest

from clio import config, retrieval
from clio.retrieval import chiave_sezione, sezione_corrisponde, valuta


def ris(doc, sez, score, file_origine=""):
    return {"score": score, "payload": {"documento": doc, "sezione": sez, "citazione": f"{doc}, {sez}",
                                        "file_origine": file_origine}}


@pytest.mark.parametrize("attesa, chunk, atteso", [
    ("art. 33", "art. 33", True),
    ("Articolo 33", "art. 33", True),
    ("33", "art. 33", True),
    ("art. 3", "art. 33", False),
    ("art. 5 bis", "art. 5 bis", True),
    ("allegato III", "allegato III", True),
    ("Allegato iii", "allegato III", True),
    ("allegato I", "allegato III", False),
    ("considerando 7", "considerando 1-10", True),
    ("considerando 11", "considerando 1-10", False),
    ("3.1", "3.1", True),
    ("sez. 3.1", "3.1", True),
    ("3.1", "3.2", False),
])
def test_sezione_corrisponde(attesa, chunk, atteso):
    assert sezione_corrisponde(attesa, chunk) is atteso


def test_chiave_sezione():
    assert chiave_sezione("Articolo  12") == ("art", "12")
    assert chiave_sezione("considerando 1–10") == ("considerando", 1, 10)
    assert chiave_sezione(None) == ("testo", "")


def test_esiti():
    riga = {"id": "GS1", "domanda": "?", "documento_atteso": "GDPR", "sezione_attesa": "art. 33"}
    ok = valuta(riga, [ris("GDPR", "art. 33", 0.8), ris("NIS2", "art. 23", 0.7)])
    assert ok["esito"] == "OK" and ok["rank_esatto"] == 1 and ok["top1_citazione"] == "GDPR, art. 33"

    parziale = valuta(riga, [ris("NIS2", "art. 23", 0.8), ris("GDPR", "art. 33", 0.7)])
    assert parziale["esito"] == "PARZIALE" and parziale["rank_esatto"] == 2

    solo_doc = valuta(riga, [ris("GDPR", "art. 34", 0.8)])
    assert solo_doc["esito"] == "PARZIALE" and solo_doc["rank_esatto"] is None and solo_doc["rank_documento"] == 1

    fallito = valuta(riga, [ris("AI Act", "art. 5", 0.8)])
    assert fallito["esito"] == "FALLITO"
    assert valuta(riga, [])["esito"] == "FALLITO"


def test_sezione_non_verificabile_e_file_origine():
    riga = {"id": "GS2", "domanda": "?", "documento_atteso": "reg_2016_679", "sezione_attesa": "(titolo)"}
    v = valuta(riga, [ris("GDPR", "art. 1", 0.5, "reg_2016_679.txt")])
    assert v["esito"] == "OK" and v["verifica_sezione"] is False


def test_hit_rate():
    vals = [{"rank_esatto": 1, "rank_documento": 1}, {"rank_esatto": 3, "rank_documento": 1},
            {"rank_esatto": None, "rank_documento": None}, {"rank_esatto": None, "rank_documento": 2}]
    assert retrieval.hit_rate(vals, 1) == 25.0
    assert retrieval.hit_rate(vals, 3) == 50.0
    assert retrieval.hit_rate(vals, 3, esatto=False) == 75.0
    assert retrieval.hit_rate([], 1) == 0.0


def test_filtro_min_score():
    r = [ris("A", "art. 1", 0.6), ris("A", "art. 2", 0.3)]
    assert retrieval.filtra_per_score(r, None) == r
    assert retrieval.filtra_per_score(r, 0.5) == r[:1]


def test_esegui_golden_set_mai_modificato(base, servizi):
    kb = config.get_kb("demo")
    with open(kb.golden_set, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(retrieval.COLONNE_GOLDEN)
        w.writerow(["GS001", "Entro quante ore va notificata una violazione?", "GDPR", "art. 33", "", "", "", ""])
        w.writerow(["GS002", "Cosa sono i sistemi vietati?", "AI Act", "art. 5", "", "", "", ""])
        w.writerow(["GS003", "", "", "", "", "", "", ""])                     # riga vuota ignorata
    impronta = hashlib.sha256(kb.golden_set.read_bytes()).hexdigest()
    servizi.risultati_ricerca = [ris("GDPR", "art. 33", 0.81), ris("GDPR", "art. 34", 0.7)]

    assert retrieval.esegui(kb, top_k=5) == 0
    assert hashlib.sha256(kb.golden_set.read_bytes()).hexdigest() == impronta

    risultati = list(csv.DictReader(open(next((base / "report").glob("retrieval_results_demo_*.csv")))))
    assert [r["esito"] for r in risultati] == ["OK", "FALLITO"]
    report = next((base / "report").glob("retrieval_report_demo_*.txt")).read_text(encoding="utf-8")
    assert "Top-1: 50.0%" in report and "Top-5: 50.0%" in report


def test_esegui_errori_chiari(base, servizi):
    kb = config.get_kb("demo")
    assert retrieval.esegui(kb) == 1                    # file mancante
    kb.golden_set.write_text("id,domanda\nGS1,ciao\n", encoding="utf-8")
    with pytest.raises(ValueError, match="documento_atteso"):
        retrieval.esegui(kb)
