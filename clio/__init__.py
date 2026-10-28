"""Clio – assistente che risponde sui documenti e cita sempre le fonti.

Pipeline RAG in sette passi, uno per modulo:

    1. parser      (clio.parser)     PDF/DOCX -> testo con marcatori [PAGINA N]
    2. chunking    (clio.chunking)   testo -> chunk JSON con metadati separati
    3. embedding   (clio.embedding)  chunk -> vettori (Ollama, bge-m3, dim 1024)
    4. upload      (clio.qdrant)     vettori -> collection Qdrant ricreata per KB
    5. retrieval   (clio.retrieval)  valutazione sul Golden Set (OK/PARZIALE/FALLITO)
    6. risposta    (clio.risposta)   risposta LLM solo dal contesto, con citazioni
    7. web         (clio.web)        chat web minimale con autenticazione

Punto di ingresso unico: ``python -m clio.cli <passo> --kb <nome>``.
"""

__version__ = "0.1.0"
