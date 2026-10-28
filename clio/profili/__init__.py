"""Profili di riconoscimento dei titoli per il passo 2 (chunking)."""

from . import legislativo_ue, sezioni_numerate

PROFILI = {
    "sezioni_numerate": sezioni_numerate.segmenta,
    "legislativo_ue": legislativo_ue.segmenta,
}
