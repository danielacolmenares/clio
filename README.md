# Clio – assistente che risponde sui documenti e cita sempre le fonti

Fai una domanda in linguaggio naturale, Clio cerca le parti pertinenti dei documenti e risponde **solo sulla base di quelle**, indicando da dove viene ogni informazione: *[GDPR, art. 33]*. Se la risposta nei documenti non c'è, lo dice: **"Non trovo questa informazione nei documenti."** Non inventa.

Tutto gira **in locale**, con modelli open source: i documenti non lasciano mai la macchina.

La Knowledge Base dimostrativa contiene tre testi europei su protezione dei dati, sicurezza informatica e intelligenza artificiale: **GDPR**, **NIS2** e **AI Act**.

*Clio is a local retrieval-augmented generation (RAG) pipeline: it answers questions only from your documents, always cites article and page, and explicitly refuses when the answer is not there. Demo knowledge base: GDPR, NIS2 and the EU AI Act (Italian texts).*

---

## Come funziona: 7 passi, uno per responsabilità

```mermaid
flowchart LR
    D[PDF / DOCX] --> P1[1 · Estrazione<br/>testo e pagine]
    P1 --> P2[2 · Divisione<br/>per articoli e sezioni]
    P2 --> P3[3 · Embedding<br/>bge-m3]
    P3 --> P4[(4 · Qdrant)]
    Q[Domanda] --> P5[5 · Recupero<br/>dei passaggi]
    P4 --> P5
    P5 --> P6[6 · Risposta<br/>Gemma 3 · solo dal contesto]
    P6 --> P7[7 · Chat web<br/>con le fonti]
```

| Passo | Cosa fa |
|---|---|
| 1 · Estrazione | testo da PDF e DOCX, con il numero di pagina conservato |
| 2 · Divisione | un pezzo per articolo, allegato o gruppo di considerando; i titoli e il percorso (Capo › Sezione) diventano metadati; intestazioni e note della Gazzetta ufficiale vengono tolte |
| 3 · Embedding | vettori con `bge-m3` via Ollama, con controllo della dimensione |
| 4 · Archivio | una collezione Qdrant per ogni Knowledge Base, ricreata da zero a ogni caricamento |
| 5 · Recupero | i passaggi più vicini alla domanda, e la misura della qualità sul Golden Set |
| 6 · Risposta | il modello riceve solo i passaggi recuperati, numerati, e deve citarli |
| 7 · Chat web | interfaccia con accesso protetto e fonti in evidenza, con le pagine |

Ogni passo produce un **report leggibile** e un **log** con nome della Knowledge Base e orario, così ogni esecuzione si può ricostruire e confrontare con le altre.

## Perché ci si può fidare delle risposte

- **Le fonti non le scrive il modello.** Sotto ogni risposta Clio aggiunge l'elenco delle fonti costruito dai metadati dei passaggi recuperati (documento, articolo, pagine). Le citazioni scritte dal modello nel testo vengono confrontate con quelle reali, e ogni discrepanza viene segnalata.
- **Se non trova nulla, non chiama il modello.** Senza passaggi recuperati la risposta è fissa e deterministica.
- **Il rifiuto si misura.** Oltre al Golden Set (domande con risposta nota) c'è un **Negative Set**: domande fuori dominio, alcune volutamente vicine al tema, per cui l'unica risposta corretta è "non lo so".
- **Il testo che diventa vettore è pulito.** Numeri di pagina, intestazioni e note a piè di pagina non entrano nell'embedding: restano metadati.
- **Nessuna modifica senza misura.** Parser, divisione, modello e prompt si cambiano solo dopo aver rifatto Golden Set, Negative Set e verifica di determinismo.

## Divisione dei testi normativi

I testi della Gazzetta ufficiale dell'Unione europea non sono semplici da dividere bene. Il profilo `legislativo_ue` riconosce:

- considerando, articoli, allegati, capi e sezioni, controllando che la numerazione sia coerente
- i titoli degli articoli, anche quando vanno a capo
- le intestazioni della Gazzetta nei due formati (fino al 2023 e dal 2023) e le note a piè di pagina, che vengono tolte
- gli articoli troppo lunghi, che vengono divisi per paragrafo, punto o lettera ripetendo l'intestazione in ogni parte

Sui tre testi della demo i conteggi tornano esatti:

| Atto | Considerando | Articoli | Allegati | Pezzi |
|---|---|---|---|---|
| GDPR – Reg. (UE) 2016/679 | 173 | 99 | – | 143 |
| NIS2 – Dir. (UE) 2022/2555 | 144 | 46 | 3 | 88 |
| AI Act – Reg. (UE) 2024/1689 | 180 | 113 | 13 | 200 |

Per documenti aziendali (procedure, policy, manuali) c'è il profilo `sezioni_numerate`, che divide per titoli numerati come "3.1 Gestione degli accessi".

## Validazione

Il metodo: un **Golden Set** di 17 domande con documento e articolo attesi e una risposta di riferimento, e un **Negative Set** di 5 domande fuori dominio, alcune volutamente vicine al tema (norme ISO, Cyber Resilience Act), per cui l'unica risposta giusta è "non lo so". Ogni risposta viene letta e valutata a mano.

### Risultati sulla demo (GDPR, NIS2, AI Act)

Misurati il 28 e 29 ottobre 2026 su un PC portatile, **solo processore**, con `bge-m3` e `gemma3:4b`. Tutti i file sono in [`docs/risultati-demo/`](docs/risultati-demo/).

**Recupero (passo 5)**: il documento giusto arriva sempre per primo; l'articolo giusto arriva

| primo | entro i primi 3 | entro i primi 5 |
|---|---|---|
| **88%** (15/17) | 94% | **100%** |

Nei 2 casi restanti l'articolo giusto è al secondo posto (AI Act art. 50) e al quarto (NIS2 art. 21), e al primo c'è un considerando che spiega proprio quell'articolo.

**Risposte (passo 6)**, prompt C, versione definitiva:

| | Risultato |
|---|---|
| Domande fuori dominio rifiutate con la frase esatta | **5 su 5** |
| Risposte inventate | **0** |
| Risposte corrette | **12 su 17** |
| Risposte corrette ma incomplete | 5 su 17 |
| Risposte sbagliate | **0** |
| Citazioni di fonti non fornite | **0** |
| Tempo per risposta (solo CPU) | mediana circa 3,8 minuti |

Le 5 risposte incomplete dicono cose giuste ma non tutto: per esempio, sulla NIS2 riporta l'obbligo di notifica ma non le scadenze di 24 ore, 72 ore e un mese. Il dettaglio domanda per domanda è in [`docs/risultati-demo/README.md`](docs/risultati-demo/README.md).

### Come ci si è arrivati: un difetto invisibile

Il primo giro aveva dato risultati deludenti. Una risposta fuori dominio inventava la durata della certificazione ISO 27001 con una citazione inesistente, un'altra aggiungeva l'aliquota IVA presa "da fuori", e l'importo delle sanzioni dell'AI Act era sbagliato. Un prompt più rigido (C) aveva tolto le invenzioni, ma il modello era diventato così prudente da negare informazioni che aveva davanti.

Leggendo le risposte una per una sono emerse frasi interrotte a metà. La causa: Clio non dichiarava a Ollama la **finestra di contesto**, che restava a 4.096 token, mentre un prompt con tre articoli lunghi arriva a circa 6.000. In quel caso Ollama **taglia l'inizio del prompt, cioè proprio le regole** (nei log: 32 avvisi `truncating input prompt`, per esempio da 4.165 a 2.051 token). Nessun errore, risposte all'apparenza normali: il modello semplicemente non vedeva le istruzioni.

Con la finestra impostata a 8.192 token (`CLIO_NUM_CTX`) e zero tagli nei log, lo stesso modello è passato da 4 risposte sbagliate e 2 rifiuti esatti su 5 a **0 sbagliate e 5 rifiuti su 5**.

| | Prompt A, contesto tagliato | Prompt C, contesto tagliato | **Prompt C, contesto corretto** |
|---|---|---|---|
| Rifiuti esatti fuori dominio | 1/5 | 2/5 | **5/5** |
| Risposte inventate | 1 | 0 | **0** |
| Corrette / incomplete / sbagliate | ~12 / 3 / 1 + 1 tempo scaduto | 9 / 4 / 4 | **12 / 5 / 0** |
| Determinismo (due esecuzioni) | – | 22/22 identiche | – |

I risultati con il contesto tagliato restano pubblicati in `docs/risultati-demo/contesto-tagliato/`, perché mostrano quanto conta misurare: senza leggere ogni risposta, il difetto non sarebbe mai emerso.

**Progetto originale.** Clio nasce da un assistente che ho sviluppato su 62 documenti di un sistema di gestione aziendale (sicurezza delle informazioni, privacy, qualità), che non sono pubblicati. Lì:

- il modello è stato scelto con un **benchmark su 7 modelli** open source
- il recupero ha raggiunto il **90% di risposte giuste al primo posto** sulla Knowledge Base di prova
- sulla Knowledge Base completa: **15 domande, 10 OK, 5 parziali spiegate, 0 fallite**
- **nessuna risposta inventata** sulle domande fuori dominio
- l'interfaccia è stata provata da un gruppo di colleghi

**Una cosa imparata nel progetto originale:** il punteggio di somiglianza non basta a riconoscere le domande fuori dominio, perché i punteggi delle domande valide e di quelle fuori tema si sovrappongono. I rifiuti corretti li ottiene l'istruzione data al modello, non una soglia. Per questo la soglia `CLIO_MIN_SCORE` esiste ma è spenta di default.

## Avvio in locale

Servono Python 3.10+ e Docker.

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

docker compose up -d                                  # Qdrant e Ollama, solo su 127.0.0.1
docker compose exec ollama ollama pull bge-m3
docker compose exec ollama ollama pull gemma3:4b

python -m clio.cli tutto --kb demo                    # passi 1-4
python -m clio.cli retrieval --kb demo --top-k 5      # passo 5 sul Golden Set
python -m clio.cli risposta --kb demo --set entrambi  # passo 6 su Golden e Negative Set (prompt C)

python -m clio.cli utente <nome>                      # crea un utente per la chat
python -m clio.cli web --kb demo                      # http://127.0.0.1:8080
```

Configurazione con variabili d'ambiente: `OLLAMA_URL`, `QDRANT_URL`, `CLIO_EMBED_MODEL`, `CLIO_LLM_MODEL`, `CLIO_NUM_CTX` (finestra di contesto, predefinita 8192), `CLIO_TIMEOUT_LLM`, `CLIO_MIN_SCORE`, `CLIO_UTENTI_FILE`.

**Una nuova Knowledge Base** è una cartella `kb/<nome>/` con i documenti in `corpus/` e un `kb.json` che indica il profilo di divisione. Il codice non cambia.

## Sicurezza

- Servizi e chat ascoltano solo su `127.0.0.1`; per l'uso in rete va messo davanti un proxy HTTPS
- Accesso alla chat con password salvate come hash PBKDF2, in un file fuori dal repository
- Nessun dato inviato a servizi esterni: modelli e database sono locali
- Registro delle sessioni minimo e documentato

## Test

**125 test automatici**, che non toccano la rete (un controllo nei test la blocca): estrazione da PDF e DOCX veri generati nei test, divisione per articoli con testo nello stile di EUR-Lex (note, intestazioni, articoli a cavallo di pagina), client Ollama e Qdrant simulati, valutazione, risposta e rifiuto, autenticazione della chat, flusso completo da riga di comando.

```bash
python -m pytest -q
```

## Struttura

```
clio/
  parser.py  chunking.py  embedding.py  qdrant.py  retrieval.py  risposta.py  web.py
  profili/   legislativo_ue.py  sezioni_numerate.py
  cli.py  config.py  clienti.py  autenticazione.py  sessioni.py
kb/demo/     corpus/ (PDF), kb.json, golden_set.csv, negative_set.csv, NOTE.txt
tests/
compose.yaml
```

## Fonti dei documenti della demo

Testi ufficiali in lingua italiana da [EUR-Lex](https://eur-lex.europa.eu), © Unione europea. Il riutilizzo è consentito con indicazione della fonte (decisione 2011/833/UE). Fanno fede solo i testi pubblicati nella Gazzetta ufficiale dell'Unione europea.

---

Progettato e sviluppato da **Daniela P Colmenares** · [danielacolmenares.it](https://www.danielacolmenares.it)
