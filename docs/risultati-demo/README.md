# Risultati sulla Knowledge Base demo

GDPR (Reg. UE 2016/679), NIS2 (Dir. UE 2022/2555), AI Act (Reg. UE 2024/1689), testi italiani da EUR-Lex.

Ambiente: PC portatile, solo processore, Docker in WSL. Ollama 0.40.12, Qdrant 1.13.4, embedding `bge-m3`, generazione `gemma3:4b` (temperatura 0, seed fisso), 3 passaggi per domanda.

## File

| File | Contenuto |
|---|---|
| `passo2_divisione_report.txt` | divisione dei tre atti: considerando, articoli, allegati, pezzi |
| `passo3_embedding_report.txt` | 431 embedding, circa 4,7 s ciascuno su CPU |
| `passo5_recupero.csv`, `passo5_recupero_report.txt` | posizione del passaggio giusto per ogni domanda del Golden Set |
| `passo6_promptC_golden.csv`, `passo6_promptC_negative.csv`, `passo6_promptC_report.txt` | risposte definitive (prompt C, finestra di contesto 8.192 token) |
| `contesto-tagliato/` | i giri precedenti, falsati dal taglio del prompt (vedi il README principale) |

## Valutazione domanda per domanda

Revisione manuale delle risposte definitive. **OK** = corretta e completa; **PARZIALE** = corretta ma incompleta; **ERRATA** = contiene un errore.

| Domanda | Atteso | Esito | Nota |
|---|---|---|---|
| GS001 notifica di una violazione | GDPR art. 33 | OK | 72 ore, senza ingiustificato ritardo |
| GS002 quando designare il DPO | GDPR art. 37 | OK | i tre casi |
| GS003 principi del trattamento | GDPR art. 5 | OK | |
| GS004 diritto all'oblio | GDPR art. 17 | PARZIALE | risponde dal considerando sull'estensione online; l'art. 17 era tra le fonti ma non viene usato |
| GS005 valutazione d'impatto | GDPR art. 35 | OK | |
| GS006 sanzioni massime GDPR | GDPR art. 83 | OK | 10 mln/2% e 20 mln/4% |
| GS007 registro dei trattamenti | GDPR art. 30 | OK | |
| GS008 scadenze di segnalazione | NIS2 art. 23 | PARZIALE | riporta l'obbligo di notifica ma non 24 h / 72 h / un mese |
| GS009 misure di gestione del rischio | NIS2 art. 21 | PARZIALE | l'art. 21 non è tra i primi 3 passaggi: risponde dai considerando |
| GS010 organi di gestione | NIS2 art. 20 | PARZIALE | manca l'obbligo di formazione |
| GS011 sanzioni soggetti essenziali | NIS2 art. 34 | OK | 10 mln o 2% |
| GS012 pratiche di IA vietate | AI Act art. 5 | OK | riporta il testo dell'articolo |
| GS013 alfabetizzazione in materia di IA | AI Act art. 4 | OK | |
| GS014 sistemi ad alto rischio | AI Act art. 6 | OK | allegato I e allegato III |
| GS015 obblighi dei deployer | AI Act art. 26 | OK | |
| GS016 trasparenza, chatbot, deep fake | AI Act art. 50 | PARZIALE | marcatura e deep fake; manca l'obbligo di informare chi parla con un chatbot |
| GS017 sanzioni pratiche vietate | AI Act art. 99 | OK | 35 mln o 7% |

**Golden Set: 12 OK, 5 PARZIALE, 0 ERRATA.**

| Domanda fuori dominio | Esito |
|---|---|
| NEG001 aliquota IVA | rifiuto esatto |
| NEG002 costo e durata della certificazione ISO 27001 | rifiuto esatto |
| NEG003 Cyber Resilience Act | rifiuto esatto |
| NEG004 pena per accesso abusivo | rifiuto esatto |
| NEG005 presidente del Garante | rifiuto esatto |

**Negative Set: 5 rifiuti esatti su 5, 0 risposte inventate.**

## Cosa migliorare

- **Risposte incomplete**: il modello da 4 miliardi di parametri tende a fermarsi al primo paragrafo pertinente. Da provare: un modello più grande (vedi [Dike](https://github.com/danielacolmenares/dike)) o un'istruzione che chieda di coprire tutte le parti della domanda.
- **GS009**: problema di recupero, non di generazione. L'articolo giusto è al quarto posto: da provare 4-5 passaggi invece di 3, ora che la finestra di contesto lo permette.
- **Tempi**: circa 4 minuti per risposta su un portatile senza GPU. Va bene per una demo, non per l'uso quotidiano.
