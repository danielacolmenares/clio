import csv

import pytest

from clio import config, risposta
from clio.risposta import FRASE_FUORI_DOMINIO


def ris(doc, sez, score, pagine=(), testo="testo", titolo="Titolo"):
    return {"score": score, "payload": {"documento": doc, "sezione": sez, "citazione": f"{doc}, {sez}",
                                        "titolo_sezione": titolo, "titolo_documento": f"Atto {doc}",
                                        "pagine": list(pagine), "testo": testo}}


def test_nessun_chunk_nessuna_chiamata_llm(base, servizi):
    servizi.risultati_ricerca = []
    esito = risposta.rispondi(config.get_kb("demo"), "Qual e' la capitale della Francia?")
    assert esito["risposta"] == FRASE_FUORI_DOMINIO
    assert esito["chiamata_llm"] is False and esito["fuori_dominio"] is True
    assert esito["fonti"] == []
    assert servizi.conteggio("/api/generate") == 0


def test_min_score_svuota_il_contesto(base, servizi):
    servizi.risultati_ricerca = [ris("GDPR", "art. 33", 0.31)]
    esito = risposta.rispondi(config.get_kb("demo"), "pizza?", min_score=0.5)
    assert esito["chiamata_llm"] is False
    assert servizi.conteggio("/api/generate") == 0


def test_prompt_numerato_e_separato():
    chunk = [ris("GDPR", "art. 33", 0.81, testo="Notifica entro 72 ore."),
             ris("NIS2", "art. 23", 0.74, testo="Obblighi di segnalazione.")]
    p = risposta.costruisci_prompt("Entro quando?", chunk, "A")
    assert "FONTE 1 – [GDPR, art. 33] Titolo" in p
    assert "FONTE 2 – [NIS2, art. 23] Titolo" in p
    assert p.index("FONTE 1") < p.index("Notifica entro 72 ore.") < p.index("---") < p.index("FONTE 2")
    assert "ESCLUSIVAMENTE" in p and FRASE_FUORI_DOMINIO in p
    assert "(rilevanza: 0.81)" in p
    assert p.rstrip().endswith("DOMANDA: Entro quando?\n\nRISPOSTA:")
    b = risposta.costruisci_prompt("Entro quando?", chunk, "B")
    assert "rilevanza" not in b and "[GDPR, art. 33]" in b
    with pytest.raises(ValueError):
        risposta.costruisci_prompt("x", chunk, "C")


def test_fonti_aggiunte_in_modo_deterministico(base, servizi):
    servizi.risultati_ricerca = [
        ris("GDPR", "art. 33", 0.8, [52], titolo="Articolo 33 – Notifica"),
        ris("GDPR", "art. 33", 0.7, [53], titolo="Articolo 33 – Notifica"),   # seconda parte dello stesso articolo
        ris("GDPR", "considerando 81-90", 0.6, [16, 17], titolo="Considerando (81)–(90)"),
    ]
    servizi.risposta_llm = "Entro 72 ore [GDPR, art. 33, par. 1]; si veda anche [GDPR, considerando 85]."
    esito = risposta.rispondi(config.get_kb("demo"), "Entro quando notificare?")
    assert esito["chiamata_llm"] is True and esito["fuori_dominio"] is False
    assert esito["risposta"] == (
        "Entro 72 ore [GDPR, art. 33, par. 1]; si veda anche [GDPR, considerando 85].\n\n"
        "Fonti:\n"
        "- [GDPR, art. 33] Articolo 33 – Notifica, pagg. 52-53\n"
        "- [GDPR, considerando 81-90] Considerando (81)–(90), pagg. 16-17"
    )
    assert esito["citazioni_non_recuperate"] == []


def test_citazione_inventata_segnalata(base, servizi):
    servizi.risultati_ricerca = [ris("GDPR", "art. 33", 0.8)]
    servizi.risposta_llm = "Vedi [GDPR, art. 34] e [NIS2, art. 23] e [GDPR, Articolo 33]."
    esito = risposta.rispondi(config.get_kb("demo"), "?")
    assert esito["citazioni_non_recuperate"] == ["GDPR, art. 34", "NIS2, art. 23"]


@pytest.mark.parametrize("testo", [
    FRASE_FUORI_DOMINIO,
    "non trovo questa informazione nei documenti",
    "Mi dispiace: Non trovo questa informazione nei documenti forniti.",
])
def test_riconoscimento_fuori_dominio(testo):
    assert risposta.dichiara_fuori_dominio(testo)


def test_fuori_dominio_dichiarato_dal_modello_senza_fonti(base, servizi):
    servizi.risultati_ricerca = [ris("GDPR", "art. 4", 0.42)]
    servizi.risposta_llm = FRASE_FUORI_DOMINIO
    esito = risposta.rispondi(config.get_kb("demo"), "Ricetta della pizza?")
    assert esito["chiamata_llm"] is True and esito["fuori_dominio"] is True
    assert esito["risposta"] == FRASE_FUORI_DOMINIO and esito["fonti"] == []


def test_formatta_pagine():
    assert risposta.formatta_pagine([]) == ""
    assert risposta.formatta_pagine([4]) == "pag. 4"
    assert risposta.formatta_pagine([4, 5, 6]) == "pagg. 4-6"
    assert risposta.formatta_pagine([4, 9]) == "pagg. 4, 9"


def test_esegui_negative_set_riepilogo(base, servizi, monkeypatch):
    kb = config.get_kb("demo")
    kb.negative_set.write_text("id,domanda,nota\nNEG1,Ricetta della pizza?,cucina\n"
                               "NEG2,Chi ha vinto il campionato?,sport\nNEG3,Meteo domani?,meteo\n",
                               encoding="utf-8")
    risposte = iter([FRASE_FUORI_DOMINIO, "Il campionato e' stato vinto da X [GDPR, art. 1]."])
    monkeypatch.setattr(risposta.clienti, "ollama_generate", lambda prompt, modello=None: next(risposte))

    def cerca(kb_, domanda, top_k):
        # NEG3: nessun chunk recuperato -> risposta deterministica senza LLM
        return ([] if domanda.startswith("Meteo") else [ris("GDPR", "art. 1", 0.35)]), 0.01, 0.01

    monkeypatch.setattr(risposta, "cerca", cerca)
    assert risposta.esegui(kb, "negative", "A", "modello:test") == 0

    report = next((base / "report").glob("risposta_report_demo_*.txt")).read_text(encoding="utf-8")
    assert "Dichiarate correttamente fuori dominio: 2/3 (66.7%)" in report
    assert "Da verificare (risposta data a domanda fuori dominio): NEG2" in report
    assert "SET: golden" not in report
    righe = list(csv.DictReader(open(next((base / "report").glob("risposta_results_negative_demo_*.csv")))))
    assert [r["chiamata_llm_effettuata"] for r in righe] == ["True", "True", "False"]
    assert righe[0]["correttezza_contenuto"] == ""                       # colonne per revisione umana
    assert righe[2]["risposta_generata"] == FRASE_FUORI_DOMINIO
    assert righe[1]["modello_llm"] == "modello:test" and righe[1]["versione_prompt"] == "A"


def test_esegui_set_mancante_saltato(base, servizi):
    kb = config.get_kb("demo")
    assert risposta.esegui(kb, "entrambi") == 0
    report = next((base / "report").glob("risposta_report_demo_*.txt")).read_text(encoding="utf-8")
    assert "SET: golden - SALTATO" in report and "SET: negative - SALTATO" in report
