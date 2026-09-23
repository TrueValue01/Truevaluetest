#!/usr/bin/env python3
"""
Scraper condiviso per le pagine "scheda" di Borsa Italiana.

Scoperto e verificato in sessione (fetch live confermato su due ISIN reali):
- Obbligazioni MOT:      https://www.borsaitaliana.it/borsa/obbligazioni/mot/btp/scheda/{ISIN}.html
- Certificati SeDeX:     https://www.borsaitaliana.it/borsa/cw-e-certificates/scheda/{ISIN}-SEDX.html

Entrambe sono pagine HTML vere renderizzate server-side (non una SPA come
iShares) — tabelle "Etichetta: Valore" estraibili con un parser generico,
senza bisogno di conoscere la struttura CSS esatta della pagina.

REGOLA NON NEGOZIABILE: si estrae solo cio' che e' scritto sulla pagina.
Nessun campo dedotto o calcolato qui — quello lo fa il motore dopo, con
la stessa cautela di sempre su cosa e' certo e cosa no.

Sanity check incluso: dopo il parsing, verifica che il "Codice Isin" letto
sulla pagina corrisponda davvero all'ISIN richiesto — altrimenti la pagina
potrebbe essere un redirect a un errore o a uno strumento diverso, e il
risultato viene scartato invece di essere scritto come fosse buono.
"""

import re
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
TIMEOUT_SECONDS = 20

URL_TEMPLATES = {
    "MOT_BTP": "https://www.borsaitaliana.it/borsa/obbligazioni/mot/btp/scheda/{isin}.html",
    "MOT_OBBLIGAZIONE": "https://www.borsaitaliana.it/borsa/obbligazioni/mot/obbligazioni-in-euro/scheda/{isin}.html",
    "SEDEX_CERT": "https://www.borsaitaliana.it/borsa/cw-e-certificates/scheda/{isin}-SEDX.html",
}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def extract_label_value_pairs(html):
    """
    Estrae tutte le coppie etichetta/valore dalle tabelle della pagina.
    Generico apposta: non dipende da classi CSS (che possono cambiare a
    ogni redesign, come visto con iShares) — solo dalla struttura a righe
    <tr><th o primo td>etichetta</th/td><td>valore</td></tr>.
    """
    soup = BeautifulSoup(html, "html.parser")
    pairs = {}

    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            cells = row.find_all(["th", "td"])
            if len(cells) < 2:
                continue
            label = cells[0].get_text(strip=True)
            value = cells[1].get_text(strip=True)
            if label and value:
                pairs[label] = value
                # Se la cella valore contiene un link (es. "KID"), salvo
                # anche l'URL vero sotto '<Etichetta>_href' — il testo
                # visibile ("Visualizza su sito emittente") non e' usabile.
                link = cells[1].find("a", href=True)
                if link:
                    pairs[f"{label}_href"] = link["href"]

    return pairs


def fetch_scheda(isin, mercato):
    """
    Scarica e parsa la pagina scheda per un ISIN su un mercato dato.
    mercato deve essere una chiave di URL_TEMPLATES.

    Ritorna un dict con:
      esito: "OK" | "HTTP_xxx" | "ERRORE_RETE" | "ISIN_NON_CORRISPONDE"
      dati: dict etichetta->valore (vuoto se esito != OK)
      url, timestamp
    """
    if mercato not in URL_TEMPLATES:
        raise ValueError(f"mercato sconosciuto: {mercato}. Valori validi: {list(URL_TEMPLATES)}")

    url = URL_TEMPLATES[mercato].format(isin=isin)

    try:
        resp = requests.get(url, timeout=TIMEOUT_SECONDS, headers={"User-Agent": USER_AGENT})
    except requests.RequestException as e:
        return {"esito": "ERRORE_RETE", "dettaglio": str(e), "dati": {}, "url": url, "timestamp": now_iso()}

    if resp.status_code != 200:
        return {"esito": f"HTTP_{resp.status_code}", "dati": {}, "url": url, "timestamp": now_iso()}

    pairs = extract_label_value_pairs(resp.text)

    # Sanity check: la pagina deve confermare lo stesso ISIN richiesto.
    # Il sito a volte ha piu' quotazioni (es. -MOTX) per lo stesso ISIN
    # base, quindi confrontiamo case-insensitive e senza spazi.
    isin_pagina = pairs.get("Codice Isin", "").strip().upper()
    if isin_pagina != isin.strip().upper():
        return {
            "esito": "ISIN_NON_CORRISPONDE",
            "dettaglio": f"richiesto {isin}, pagina mostra '{isin_pagina}'",
            "dati": pairs,
            "url": url,
            "timestamp": now_iso(),
        }

    return {"esito": "OK", "dati": pairs, "url": url, "timestamp": now_iso()}


def parse_numero_italiano(testo):
    """'1.028,90' -> 1028.90 . Ritorna None se non parsabile — mai un numero a caso."""
    if not testo:
        return None
    pulito = testo.replace(".", "").replace(",", ".").replace("%", "").replace("+", "").strip()
    try:
        return float(pulito)
    except ValueError:
        return None
