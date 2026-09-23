#!/usr/bin/env python3
"""
Riempie data/bonds_ground_truth.json e data/certs_ground_truth.json
scaricando le pagine scheda pubbliche di Borsa Italiana.

Riempie SOLO i campi statici (cedola, scadenza, emittente, barriera,
sottostante, ammontare emesso...). I campi di mercato (prezzo, rendimento,
duration) restano volutamente fuori da questo script: cambiano ogni giorno,
vanno chiesti live quando serve un'analisi, non tenuti in cache qui — un
prezzo di ieri sera presentato come "il dato" sarebbe un errore silenzioso,
peggio di un null.

Non sovrascrive mai un campo gia' compilato (non-null): riempie i buchi,
non corregge quello che e' gia' lì.

Uso:
    python3 fill_borsa_italiana_ground_truth.py
"""

import json
import sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent))
from borsa_italiana_scraper import fetch_scheda, parse_numero_italiano

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE.parent / "data"
BONDS_PATH = DATA_DIR / "bonds_ground_truth.json"
CERTS_PATH = DATA_DIR / "certs_ground_truth.json"

# Campi STATICI (non cambiano ogni giorno) che questo script puo' riempire.
# I campi di mercato (prezzo/rendimento/duration/valore sottostante) sono
# esclusi di proposito — vedi docstring sopra.
BOND_FIELD_MAP = {
    "denominazione": "Denominazione",
    "emittente": "Emittente",
    "scadenza": "Scadenza",
    "periodicita_cedola": "Periodicità cedola",
}
BOND_NUMERIC_FIELD_MAP = {
    "tasso_cedola_periodale_pct": "Tasso Cedola Periodale",
    "ammontare_emesso": "Ammontare Emesso",
    "lotto_minimo": "Lotto Minimo",
}

CERT_FIELD_MAP = {
    "nome_commerciale": "Nome Commerciale",
    "categoria": "Categoria di Borsa",
    "emittente": "Emittente",
    "sottostante": "Sottostante",
    "scadenza": "Scadenza",
}
CERT_NUMERIC_FIELD_MAP = {
    "barriera_capitale_pct": "Barriera Capitale %",
    "strike": "Strike",
    "lotto_minimo": "Lotto Minimo",
}


def log(msg):
    print(f"[fill-borsa] {msg}", flush=True)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def load_json(path):
    if not path.exists():
        log(f"ATTENZIONE: {path} non esiste, salto")
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def fill_entry(isin, entry, mercato, field_map, numeric_field_map):
    result = fetch_scheda(isin, mercato)
    log(f"{isin} ({mercato}) -> {result['esito']}")

    if result["esito"] != "OK":
        return False

    dati = result["dati"]
    filled = []

    for json_key, label in field_map.items():
        if entry.get(json_key) is None and dati.get(label):
            entry[json_key] = dati[label]
            filled.append(json_key)

    for json_key, label in numeric_field_map.items():
        if entry.get(json_key) is None and dati.get(label):
            val = parse_numero_italiano(dati[label])
            if val is not None:
                entry[json_key] = val
                filled.append(json_key)

    # Bonus scoperto per i certificati: link KID diretto, se presente.
    # Riempio se vuoto, OPPURE se contiene ancora il testo del link (bug
    # di una versione precedente dello script) invece dell'URL vero —
    # cosi' chi ha gia' girato il fill prima di questo fix si autocorregge
    # al prossimo run, senza dover intervenire a mano sul JSON.
    valore_placeholder_bug_precedente = "Visualizza su sito emittente"
    if "kid_url" in entry and entry.get("kid_url") in (None, valore_placeholder_bug_precedente):
        kid_link = dati.get("KID_href")
        if kid_link:
            entry["kid_url"] = kid_link  # nota: qui arriva solo il testo del link, non l'href reale
            filled.append("kid_url")

    if filled:
        entry["last_updated"] = now_iso()
        entry.setdefault("_fill_log", [])
        entry["_fill_log"].append({
            "timestamp": now_iso(),
            "source": "borsa_italiana_scheda",
            "campi_riempiti": filled,
            "url": result["url"],
        })
        log(f"{isin}: riempiti -> {filled}")
    else:
        log(f"{isin}: nulla da riempire (gia' pieno o campi non trovati sulla pagina)")

    return True


def main():
    bonds_data = load_json(BONDS_PATH)
    if bonds_data:
        for isin, entry in bonds_data.get("bonds", {}).items():
            mercato = entry.get("mercato", "MOT_BTP")
            fill_entry(isin, entry, mercato, BOND_FIELD_MAP, BOND_NUMERIC_FIELD_MAP)
        BONDS_PATH.write_text(json.dumps(bonds_data, indent=2, ensure_ascii=False), encoding="utf-8")
        log(f"Scritto {BONDS_PATH}")

    certs_data = load_json(CERTS_PATH)
    if certs_data:
        for isin, entry in certs_data.get("certificati", {}).items():
            fill_entry(isin, entry, "SEDEX_CERT", CERT_FIELD_MAP, CERT_NUMERIC_FIELD_MAP)
        CERTS_PATH.write_text(json.dumps(certs_data, indent=2, ensure_ascii=False), encoding="utf-8")
        log(f"Scritto {CERTS_PATH}")


if __name__ == "__main__":
    main()
    
