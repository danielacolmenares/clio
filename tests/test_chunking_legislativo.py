import json

import pytest

from clio import chunking, config
from clio.profili import legislativo_ue as L
from clio.profili.base import componi_testo

OPZ = {"considerando_per_chunk": 10, "max_caratteri_chunk": 6000}


def _per_sezione(risultato):
    out = {}
    for s in risultato.sezioni:
        out.setdefault(s.sezione, []).append(s)
    return out


def _testo(s):
    return componi_testo(s.corpo)


@pytest.mark.parametrize("riga", [
    "4.5.2016 IT Gazzetta ufficiale dell'Unione europea L 119/37",
    "L 119/38 IT Gazzetta ufficiale dell’Unione europea 4.5.2016",
    "27.12.2022 IT Gazzetta ufficiale dell'Unione europea L 333/81",
    "Gazzetta ufficiale dell'Unione europea IT Serie L",
    "IT",
    "Serie L",
    "2024/1689 12.7.2024",
    "ELI: http://data.europa.eu/eli/reg/2024/1689/oj 12/144",
    "12/144",
    "GU L del 12.7.2024",
    "IT GU L del 12.7.2024",
    "IT dell'Unione europea",
    "(1) GU C 229 del 31.7.2012, pag. 90.",
    "( 12 ) GU L 281 del 23.11.1995, pag. 31.",
])
def test_righe_di_rumore(riga):
    assert L.e_rumore(riga)


@pytest.mark.parametrize("riga", [
    "Articolo 33",
    "1. Il titolare del trattamento notifica la violazione.",
    "pubblicato nella Gazzetta ufficiale dell'Unione europea entro il 2025, secondo la procedura.",
    "(1) La protezione delle persone fisiche e' un diritto fondamentale.",
])
def test_righe_di_contenuto(riga):
    assert not L.e_rumore(riga)


def test_metadati_documento(testo_eurlex):
    r = L.segmenta(testo_eurlex, OPZ)
    assert r.metadati["numero_atto"] == "2099/123"
    assert r.metadati["titolo"].startswith("REGOLAMENTO (UE) 2099/123 DEL PARLAMENTO EUROPEO")
    assert r.metadati["titolo"].endswith("di tali dati")
    assert r.statistiche["articoli"] == 4
    assert r.statistiche["allegati"] == 2
    assert r.statistiche["considerando"] == 12
    assert r.avvisi == []


def test_rumore_mai_nel_testo(testo_eurlex):
    r = L.segmenta(testo_eurlex, OPZ)
    for s in r.sezioni:
        t = _testo(s)
        assert "Gazzetta ufficiale" not in t
        assert "L 119/" not in t
        assert "GU C 229" not in t and "GU L 281" not in t
        assert "[PAGINA" not in t
    assert r.statistiche["righe_rumore_rimosse"] == 5


def test_considerando_raggruppati_e_note_scartate(testo_eurlex):
    sez = _per_sezione(L.segmenta(testo_eurlex, OPZ))
    primo = sez["considerando 1-10"][0]
    assert primo.extra["considerando"] == [1, 10]
    assert primo.pagine == [1, 2]
    testo = _testo(primo)
    assert testo.startswith("(1) La protezione dei dati di prova")
    assert "(10) Al fine di assicurare" in testo
    assert "Direttiva 95/46/CE" not in "".join(_testo(s) for s in sez["considerando 11-12"])
    assert sez["considerando 11-12"][0].extra["considerando"] == [11, 12]


def test_articoli_titolo_path_e_pagine(testo_eurlex):
    sez = _per_sezione(L.segmenta(testo_eurlex, OPZ))
    art1 = sez["art. 1"][0]
    assert art1.titolo == "Articolo 1 – Oggetto e finalita'"
    assert art1.path == ["Capo I – Disposizioni generali"]
    assert art1.etichetta == "art. 1"
    assert art1.extra["articolo"] == "1"

    art3 = sez["art. 3"][0]
    # Sezione con titolo sillabato su due righe, ricomposto
    assert art3.path == ["Capo II – Principi", "Sezione 1 – Principi generali del trattamento"]
    # Articolo a cavallo di un cambio pagina: entrambe le pagine, niente rumore
    assert art3.pagine == [3, 4]
    t = _testo(art3)
    assert "b) raccolti per finalita' determinate;\nc) adeguati" in t

    art4 = sez["art. 4"][0]
    assert art4.titolo == ("Articolo 4 – Notifica di una violazione dei dati di prova "
                           "all'autorita' di controllo")          # titolo su due righe
    assert art4.path[-1] == "Sezione 2 – Sicurezza"


def test_formula_finale_esclusa(testo_eurlex):
    sez = _per_sezione(L.segmenta(testo_eurlex, OPZ))
    t = _testo(sez["art. 4"][0])
    assert t.endswith("applicabile in ciascuno degli Stati membri.")
    assert "Fatto a Bruxelles" not in t and "Il presidente" not in t


def test_allegati(testo_eurlex):
    sez = _per_sezione(L.segmenta(testo_eurlex, OPZ))
    a1 = sez["allegato I"][0]
    assert a1.titolo == "Allegato I – Elenco delle categorie di dati di prova"
    assert a1.pagine == [5]
    assert _testo(a1) == "1. Dati identificativi.\n2. Dati di contatto."
    # "Articolo 1" dentro un allegato e' contenuto, non un nuovo articolo
    assert "Articolo 1 Articolo 1" in _testo(sez["allegato II"][0])


def test_articolo_lungo_diviso_per_paragrafi(testo_eurlex):
    r = L.segmenta(testo_eurlex, {"max_caratteri_chunk": 300})
    parti = _per_sezione(r)["art. 4"]
    assert len(parti) == 3
    assert [p.extra["paragrafi"] for p in parti] == [[1, 1], [2, 2], [3, 3]]
    assert [p.extra["parte"] for p in parti] == [[1, 3], [2, 3], [3, 3]]
    for p in parti:
        assert p.titolo.startswith("Articolo 4 – Notifica")
        assert p.intestazione[-2].startswith("Articolo 4 – Notifica")      # intestazione ripetuta
    assert parti[1].intestazione[-1] == "(parte 2/3, paragrafi 2)"
    assert _testo(parti[1]).startswith("2. Il responsabile del trattamento")
    assert r.statistiche["sezioni_suddivise"] >= 1


def test_divisione_per_punti_e_lettere():
    definizioni = [("Ai fini del presente regolamento si intende per:", 7)]
    for n in range(1, 7):
        definizioni.append((f"{n}) «termine {n}»: " + "definizione molto lunga " * 6, 7 + n // 4))
    pezzi = L.dividi(definizioni, 400)
    assert len(pezzi) >= 3
    assert pezzi[0][0][0][0].startswith("Ai fini")           # prologo nel primo pezzo
    assert pezzi[0][1]["punti"][0] == 1
    assert pezzi[-1][1]["punti"][1] == 6
    # tutte le righe conservate, nell'ordine
    assert [r for p, _ in pezzi for r in p] == definizioni

    lettere = [("1. Il titolare:", 1)] + [(f"{c}) " + "x" * 150, 1) for c in "abcdef"] + [("2. Altro.", 1)]
    pezzi = L.dividi(lettere, 400)
    assert pezzi[0][1]["paragrafi"] == [1, 1] and "lettere" in pezzi[0][1]
    assert pezzi[-1][1] == {"paragrafi": [2, 2]}


def test_titolo_fuori_sequenza_resta_testo():
    testo = "\n".join([
        "[PAGINA 1]", "CAPO I", "Disposizioni", "Articolo 1", "Oggetto",
        "1. Si veda il seguente rinvio:", "Articolo 9", "che resta testo.",
        "Articolo 2", "Definizioni", "Testo.",
    ])
    r = L.segmenta(testo, OPZ)
    assert [s.sezione for s in r.sezioni] == ["art. 1", "art. 2"]
    assert "Articolo 9" in _testo(r.sezioni[0])
    assert any("fuori sequenza" in a for a in r.avvisi)


def test_articolo_senza_titolo_e_testo_senza_considerando():
    testo = "\n".join(["Articolo 1", "1. Primo paragrafo.", "Articolo 2", "Titolo due", "Testo due."])
    r = L.segmenta(testo, OPZ)
    assert [s.titolo for s in r.sezioni] == ["Articolo 1", "Articolo 2 – Titolo due"]
    assert r.sezioni[0].pagine == []                          # testo senza marcatori di pagina
    assert any("senza titolo" in a for a in r.avvisi)


def test_nessun_articolo_avviso():
    r = L.segmenta("[PAGINA 1]\nTesto qualsiasi senza struttura.", OPZ)
    assert r.sezioni == []
    assert any("nessun articolo" in a for a in r.avvisi)


def test_articolo_con_titolo_sulla_stessa_riga():
    testo = "\n".join(["[PAGINA 1]", "Articolo 1", "Oggetto", "Testo uno.",
                       "Articolo 2 Definizioni ai fini del presente", "regolamento", "Testo due.",
                       "Articolo 7 Riferimento fuori sequenza", "Testo tre."])
    r = L.segmenta(testo, OPZ)
    assert [s.titolo for s in r.sezioni] == [
        "Articolo 1 – Oggetto", "Articolo 2 – Definizioni ai fini del presente regolamento"]
    assert "Articolo 7 Riferimento fuori sequenza" in _testo(r.sezioni[1])


def test_formato_atto_per_atto():
    testo = "\n".join([
        "[PAGINA 1]", "Gazzetta ufficiale", "IT dell'Unione europea", "Serie L", "2024/1689 12.7.2024",
        "REGOLAMENTO (UE) 2024/1689 DEL PARLAMENTO EUROPEO E DEL CONSIGLIO", "del 13 giugno 2024",
        "che stabilisce regole armonizzate", "considerando quanto segue:", "(1) Primo considerando.",
        "( 1 ) GU C 517 del 22.12.2021, pag. 56.",
        "ELI: http://data.europa.eu/eli/reg/2024/1689/oj 1/144",
        "[PAGINA 2]", "IT GU L del 12.7.2024", "(2) Secondo considerando.",
        "HANNO ADOTTATO IL PRESENTE REGOLAMENTO:", "CAPO I", "DISPOSIZIONI GENERALI",
        "Articolo 1", "Oggetto", "1. Testo.", "ELI: http://data.europa.eu/eli/reg/2024/1689/oj 2/144",
    ])
    r = L.segmenta(testo, OPZ)
    assert r.metadati["numero_atto"] == "2024/1689"
    assert [s.sezione for s in r.sezioni] == ["considerando 1-2", "art. 1"]
    assert r.sezioni[1].path == ["Capo I – Disposizioni generali"]
    assert _testo(r.sezioni[0]) == "(1) Primo considerando.\n(2) Secondo considerando."
    assert _testo(r.sezioni[1]) == "1. Testo."


def test_romani():
    assert [L.romano_a_intero(x) for x in ("I", "IV", "IX", "XIII", "XL")] == [1, 4, 9, 13, 40]


# ----------------------------------------------------------------------
# Passo 2 completo sul profilo legislativo
# ----------------------------------------------------------------------

def test_esegui_chunk_record_e_citazioni(base, testo_eurlex):
    kb = config.get_kb("demo")
    kb.testo_estratto.mkdir(parents=True)
    (kb.testo_estratto / "reg_prova.txt").write_text(testo_eurlex, encoding="utf-8")
    assert chunking.esegui(kb) == 0

    file_chunk = sorted((kb.chunk / "reg_prova").glob("chunk_*.json"))
    record = [json.loads(f.read_text(encoding="utf-8")) for f in file_chunk]
    per_sez = {r["sezione"]: r for r in record}
    art4 = per_sez["art. 4"]
    assert art4["documento"] == "GDPR"                         # da "atti" in kb.json
    assert art4["citazione"] == "GDPR, art. 4"
    assert art4["titolo_documento"].startswith("REGOLAMENTO (UE) 2099/123")
    assert art4["path"] == ["Capo II – Principi", "Sezione 2 – Sicurezza"]
    assert art4["pagine"] == [4]
    assert art4["profilo"] == "legislativo_ue"
    assert art4["testo"].startswith(
        "GDPR – Capo II – Principi › Sezione 2 – Sicurezza\nArticolo 4 – Notifica")
    assert "72 ore" in art4["testo"]
    assert len(art4["hash"]) == 64
    assert per_sez["considerando 1-10"]["considerando"] == [1, 10]
    assert [r["id"] for r in record] == [f"CH{i:06d}" for i in range(1, len(record) + 1)]
    for r in record:
        assert "Gazzetta" not in r["testo"]
        assert r["caratteri"] == len(r["testo"])

    report = next((base / "report").glob("chunking_report_demo_*.txt")).read_text(encoding="utf-8")
    assert "[GDPR, art. 4]" in report

    # Determinismo: una seconda esecuzione produce gli stessi file
    contenuti = [f.read_text(encoding="utf-8") for f in file_chunk]
    assert chunking.esegui(kb) == 0
    assert [f.read_text(encoding="utf-8") for f in sorted((kb.chunk / "reg_prova").glob("*.json"))] == contenuti


def test_id_documento_da_kb_json_e_duplicati(base, testo_eurlex):
    percorso = base / "kb" / "demo" / "kb.json"
    dati = json.loads(percorso.read_text())
    dati["documenti"] = {"copia": {"id": "COPIA", "titolo": "Copia di prova"}}
    percorso.write_text(json.dumps(dati))
    kb = config.get_kb("demo")
    kb.testo_estratto.mkdir(parents=True)
    (kb.testo_estratto / "copia.txt").write_text(testo_eurlex, encoding="utf-8")
    (kb.testo_estratto / "originale.txt").write_text(testo_eurlex, encoding="utf-8")
    chunking.esegui(kb)
    primo = json.loads((kb.chunk / "copia" / "chunk_0001.json").read_text())
    assert primo["documento"] == "COPIA" and primo["titolo_documento"] == "Copia di prova"
    # Il secondo documento ha id diverso, quindi testi (e hash) diversi: nessun duplicato
    assert len(list((kb.chunk / "originale").glob("*.json"))) == len(list((kb.chunk / "copia").glob("*.json")))


def test_dedup_hash_identici(base, testo_eurlex):
    kb = config.get_kb("demo")
    kb.testo_estratto.mkdir(parents=True)
    (kb.testo_estratto / "a.txt").write_text(testo_eurlex, encoding="utf-8")
    (kb.testo_estratto / "b.txt").write_text(testo_eurlex, encoding="utf-8")
    chunking.esegui(kb)       # entrambi risolti come "GDPR": testi identici
    assert list((kb.chunk / "b").glob("*.json")) == []
    report = next((base / "report").glob("chunking_report_demo_*.txt")).read_text(encoding="utf-8")
    assert "Chunk duplicati saltati: 8" in report


def test_titoli_maiuscoli_conservano_sigle_e_nomi_propri():
    from clio.profili.legislativo_ue import _cap
    assert _cap("PRATICHE DI IA VIETATE") == "Pratiche di IA vietate"
    assert _cap("BANCA DATI DELL'UE PER I SISTEMI DI IA") == "Banca dati dell'UE per i sistemi di IA"
    assert _cap("COOPERAZIONE A LIVELLO DELL'UNIONE") == "Cooperazione a livello dell'Unione"
    assert _cap("Titolo già scritto") == "Titolo già scritto"
