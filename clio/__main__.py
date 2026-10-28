"""Consente ``python -m clio <passo> --kb <nome>`` (equivalente a ``python -m clio.cli``)."""

import sys

from .cli import main

sys.exit(main())
