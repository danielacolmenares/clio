"""Generatore di PDF minimali in puro Python, per i test del parser."""

from __future__ import annotations


def _escape(testo: str) -> str:
    return testo.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def crea_pdf(pagine: list[list[str]]) -> bytes:
    """Un PDF con una pagina per elemento di ``pagine``; ogni stringa e'
    una riga di testo (Helvetica, codifica WinAnsi)."""
    oggetti: list[bytes] = []
    n_pagine = len(pagine)
    id_pagine = [4 + 2 * i for i in range(n_pagine)]
    oggetti.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{i} 0 R" for i in id_pagine)
    oggetti.append(f"<< /Type /Pages /Kids [{kids}] /Count {n_pagine} >>".encode())
    oggetti.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    for i, righe in enumerate(pagine):
        id_contenuto = id_pagine[i] + 1
        oggetti.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {id_contenuto} 0 R >>".encode()
        )
        flusso = "BT /F1 11 Tf 14 TL 60 780 Td " + " ".join(
            f"({_escape(r)}) Tj T*" for r in righe) + " ET"
        dati = flusso.encode("latin-1")
        oggetti.append(b"<< /Length " + str(len(dati)).encode() + b" >>\nstream\n" + dati + b"\nendstream")

    uscita = bytearray(b"%PDF-1.4\n")
    offset = []
    for n, obj in enumerate(oggetti, start=1):
        offset.append(len(uscita))
        uscita += f"{n} 0 obj\n".encode() + obj + b"\nendobj\n"
    inizio_xref = len(uscita)
    uscita += f"xref\n0 {len(oggetti) + 1}\n0000000000 65535 f \n".encode()
    for o in offset:
        uscita += f"{o:010d} 00000 n \n".encode()
    uscita += (f"trailer\n<< /Size {len(oggetti) + 1} /Root 1 0 R >>\n"
               f"startxref\n{inizio_xref}\n%%EOF\n").encode()
    return bytes(uscita)
