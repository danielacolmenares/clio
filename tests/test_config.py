import json

import pytest

from clio import config


def test_default_da_ambiente(base):
    assert config.ollama_url() == "http://127.0.0.1:11434"
    assert config.qdrant_url() == "http://127.0.0.1:6333"
    assert config.modello_embedding() == "bge-m3"
    assert config.dimensione_embedding() == 1024
    assert config.modello_llm() == "gemma3:4b"
    assert config.min_score() is None
    assert config.base_dir() == base.resolve()
    assert config.file_utenti() == base.resolve() / "utenti.htpasswd"


def test_override_da_ambiente(base, monkeypatch):
    monkeypatch.setenv("OLLAMA_URL", "http://ollama:11434/")
    monkeypatch.setenv("CLIO_MIN_SCORE", "0.45")
    monkeypatch.setenv("CLIO_LLM_MODEL", "altro:1b")
    assert config.ollama_url() == "http://ollama:11434"
    assert config.min_score() == 0.45
    assert config.modello_llm() == "altro:1b"


def test_valori_non_validi(base, monkeypatch):
    monkeypatch.setenv("CLIO_MIN_SCORE", "alto")
    with pytest.raises(config.ErroreConfigurazione, match="CLIO_MIN_SCORE"):
        config.min_score()
    monkeypatch.setenv("CLIO_EMBED_DIM", "x")
    with pytest.raises(config.ErroreConfigurazione, match="CLIO_EMBED_DIM"):
        config.dimensione_embedding()


def test_get_kb_e_percorsi(base):
    kb = config.get_kb("demo")
    assert kb.collection == "clio_demo"
    assert kb.corpus == base / "kb" / "demo" / "corpus"
    assert kb.golden_set.name == "golden_set.csv"
    assert kb.opzioni["profilo_chunking"] == "legislativo_ue"
    assert kb.opzioni["considerando_per_chunk"] == 10          # default non sovrascritto
    assert kb.log_sessioni().parent == base / "sessioni"


def test_scoperta_automatica(base):
    (base / "kb" / "altra" / "corpus").mkdir(parents=True)
    (base / "kb" / "vuota").mkdir()                             # senza corpus ne' kb.json
    assert config.elenco_kb() == ["altra", "demo"]
    assert config.get_kb("altra").opzioni["profilo_chunking"] == "sezioni_numerate"
    assert config.get_kb("altra").collection == "clio_altra"


def test_kb_inesistente_messaggio_chiaro(base):
    with pytest.raises(config.ErroreConfigurazione) as exc:
        config.get_kb("inesistente")
    assert "non trovata" in str(exc.value)
    assert "demo" in str(exc.value)


@pytest.mark.parametrize("nome", ["", "Demo", "../demo", "a b"])
def test_nome_kb_non_valido(base, nome):
    with pytest.raises(config.ErroreConfigurazione, match="Nome KB non valido"):
        config.get_kb(nome)


def test_kb_json_errato(base):
    percorso = base / "kb" / "demo" / "kb.json"
    percorso.write_text("{non json", encoding="utf-8")
    with pytest.raises(config.ErroreConfigurazione, match="JSON non valido"):
        config.get_kb("demo")
    percorso.write_text(json.dumps({"profilo": "x"}), encoding="utf-8")
    with pytest.raises(config.ErroreConfigurazione, match="chiavi non riconosciute"):
        config.get_kb("demo")
    percorso.write_text(json.dumps({"profilo_chunking": "markdown"}), encoding="utf-8")
    with pytest.raises(config.ErroreConfigurazione, match="profilo di chunking"):
        config.get_kb("demo")


def test_set_disattivato(base):
    (base / "kb" / "demo" / "kb.json").write_text(json.dumps({"negative_set": None}), encoding="utf-8")
    assert config.get_kb("demo").negative_set is None
