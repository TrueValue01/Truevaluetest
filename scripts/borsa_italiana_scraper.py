#!/usr/bin/env python3
"""
Scraper condiviso per le pagine "scheda" di Borsa Italiana.

Scoperto e verificato in sessione (fetch live confermato su due ISIN reali):
- Obbligazioni MOT:      https://www.borsaitaliana.it/borsa/obbligazioni/mot/btp/scheda/{ISIN}.html
- Certificati SeDeX:     https://www.borsaitaliana.it/borsa/cw-e-certificates/scheda/{ISIN}-SEDX.html

Entrambe sono pagine HTML vere renderizzate server-side (non una SPA come
iShares) — tabelle "Etichetta: Valore" estraibili con un parser generico,
senza bisogno di conoscere la struttura CSS esatta della pagina.
