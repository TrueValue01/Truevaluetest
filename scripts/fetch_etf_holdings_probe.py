#!/usr/bin/env python3
"""
Sonda ETF Holdings — verifica se gli emittenti (SPDR, iShares, Vanguard...)
pubblicano CSV/XLSX di holdings scaricabili gratis, senza API key.

Stesso pattern delle sonde CET1/NPL/insurance: gira server-side (GitHub
Actions), self-discovery reale contro l'endpoint vero, logga un JSON
leggibile nel repo — niente da fidarsi "a occhio" della pagina marketing
di un emittente, esattamente come per FMP/FDIC.

Filosofia del progetto (non derogabile): se un dato non si riesce a
verificare pulito, si dichiara N/A — mai un numero plausibile-ma-sbagliato.
Questa sonda quindi non si accontenta di un HTTP 200: prova a PARSARE il
contenuto e conta quante righe/holding reali trova, altrimenti segnala
"200 ma contenuto non valido" (es. pagina di errore travestita da 200).

Uso:
    python3 fetch_etf_holdings_probe.py
Legge:  etf_holdings_sources.json  (nella stessa cartella)
Scrive: etf_holdings_probe_result.json  (nella stessa cartella)
"""

import json
import sys
import io
import csv
from pathlib import Path
from datetime import datetime, timezone

import requests

try:
    import openpyxl
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False

HERE = Path(__file__).resolve().parent
SOURCES_PATH = HERE / "etf_holdings_sources.json"
RESULT_PATH = HERE / "etf_holdings_probe_result.json"

TIMEOUT_SECONDS = 20
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

WEIGHT_COLUMN_HINTS = ["weight", "% net assets", "% of fund", "% of net assets", "peso"]
NAME_COLUMN_HINTS = ["name", "holding", "security", "description", "nome"]


def log(msg):
    print(f"[probe] {msg}", flush=True)


def fetch_raw(url):
    """Scarica l'URL grezzo. Ritorna (status_code, content_type, bytes) o (None, None, error_str)."""
    try:
        resp = requests.get(
            url,
            timeout=TIMEOUT_SECONDS,
            headers={"User-Agent": USER_AGENT},
        )
        return resp.status_code, resp.headers.get("Content-Type", ""), resp.content
    except requests.RequestException as e:
        return None, None, str(e)


def try_parse_xlsx(content_bytes):
    """Tenta di leggere il contenuto come XLSX. Ritorna una lista di liste (righe) o None."""
    if not HAS_OPENPYXL:
        return None
    try:
        wb = openpyxl.load_workbook(io.BytesIO(content_bytes), read_only=True, data_only=True)
        ws = wb.active
        rows = []
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            rows.append(list(row))
            if i > 2000:  # cuscinetto di sicurezza, non serve leggere oltre
                break
        return rows
    except Exception:
        return None


def try_parse_csv(content_bytes):
    """Tenta di leggere il contenuto come CSV testuale. Ritorna una lista di liste o None."""
    try:
        text = content_bytes.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError:
        try:
            text = content_bytes.decode("latin-1")
        except Exception:
            return None
    try:
        reader = csv.reader(io.StringIO(text))
        rows = [r for r in reader if r]
        if len(rows) < 2:
            return None
        return rows
    except Exception:
        return None


def find_header_row(rows):
    """Cerca la riga che sembra un header con colonne peso/nome. Ritorna (idx, header) o (None, None)."""
    for idx, row in enumerate(rows[:60]):  # gli issuer mettono spesso righe di note prima della tabella vera
        cells = [str(c).strip().lower() if c is not None else "" for c in row]
        has_weight = any(any(hint in c for hint in WEIGHT_COLUMN_HINTS) for c in cells)
        has_name = any(any(hint in c for hint in NAME_COLUMN_HINTS) for c in cells)
        if has_weight and has_name:
            return idx, cells
    return None, None


def analyze_holdings(rows):
    """
    Dato un set di righe con un header riconosciuto, prova a calcolare
    holdings_count, top1_weight, top10_weight. Ritorna un dict diagnostico
    onesto: se qualcosa non torna, lo dice, non inventa un numero.
    """
    header_idx, header = find_header_row(rows)
    if header_idx is None:
        return {
            "parsed": False,
            "reason": "nessuna riga con colonne peso+nome riconoscibili nei primi 60 record",
        }

    weight_col = next(
        (i for i, c in enumerate(header) if any(h in c for h in WEIGHT_COLUMN_HINTS)), None
    )
    if weight_col is None:
        return {"parsed": False, "reason": "colonna peso non trovata dopo aver identificato l'header"}

    weights = []
    for row in rows[header_idx + 1:]:
        if weight_col >= len(row):
            continue
        raw = row[weight_col]
        if raw is None:
            continue
        try:
            val = float(str(raw).replace("%", "").replace(",", "").strip())
        except ValueError:
            continue
        weights.append(val)

    if not weights:
        return {"parsed": False, "reason": "colonna peso trovata ma nessun valore numerico estratto"}

    weights.sort(reverse=True)
    # Se i pesi sono espressi 0-1 invece di 0-100, li porto in percentuale per coerenza col motore
    if max(weights) <= 1.5:
        weights = [w * 100 for w in weights]

    return {
        "parsed": True,
        "holdings_count_estratto": len(weights),
        "top1_weight_pct": round(weights[0], 3),
        "top10_weight_pct": round(sum(weights[:10]), 3),
        "somma_pesi_totale_pct": round(sum(weights), 2),  # se lontano da 100, e' un campanello d'allarme
    }


def probe_one(ticker, issuer, url):
    log(f"{ticker} ({issuer}) -> {url}")
    status, content_type, content = fetch_raw(url)

    if status is None:
        return {
            "ticker": ticker,
            "issuer": issuer,
            "url": url,
            "esito": "ERRORE_RETE",
            "dettaglio": content,  # qui 'content' e' la stringa d'errore
        }

    if status != 200:
        return {
            "ticker": ticker,
            "issuer": issuer,
            "url": url,
            "esito": f"HTTP_{status}",
            "content_type": content_type,
            "primi_200_char": content[:200].decode("utf-8", errors="replace") if content else "",
        }

    # 200 ricevuto: ora verifico che sia un contenuto vero, non una pagina di errore travestita
    is_xlsx_hint = "spreadsheet" in (content_type or "") or url.lower().endswith(".xlsx")
    rows = None
    parsed_as = None

    if is_xlsx_hint:
        rows = try_parse_xlsx(content)
        parsed_as = "xlsx" if rows else None

    if rows is None:
        rows = try_parse_csv(content)
        parsed_as = "csv" if rows else parsed_as

    if rows is None:
        return {
            "ticker": ticker,
            "issuer": issuer,
            "url": url,
            "esito": "200_MA_NON_PARSABILE",
            "content_type": content_type,
            "dimensione_bytes": len(content),
            "primi_200_char": content[:200].decode("utf-8", errors="replace"),
        }

    analysis = analyze_holdings(rows)
    esito = "OK_HOLDINGS_ESTRATTI" if analysis.get("parsed") else "200_PARSATO_MA_STRUTTURA_INATTESA"

    # Se non ho trovato holdings veri, salvo le prime righe cosi' si capisce
    # SUBITO se e' una pagina di errore/consenso/redirect invece di rilanciare
    # la sonda una seconda volta per scoprirlo.
    anteprima_righe = None
    if not analysis.get("parsed"):
        anteprima_righe = [
            [str(c) if c is not None else "" for c in row][:8]  # max 8 colonne per riga, per leggibilita'
            for row in rows[:8]
        ]

    return {
        "ticker": ticker,
        "issuer": issuer,
        "url": url,
        "esito": esito,
        "formato_rilevato": parsed_as,
        "righe_totali": len(rows),
        "analisi": analysis,
        "anteprima_prime_righe": anteprima_righe,
    }


def main():
    if not SOURCES_PATH.exists():
        log(f"ERRORE: manca {SOURCES_PATH}")
        sys.exit(1)

    config = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
    results = []

    for target in config.get("targets", []):
        ticker = target["ticker"]
        issuer = target["issuer"]
        candidates = target.get("candidates") or []

        if not candidates:
            results.append({
                "ticker": ticker,
                "issuer": issuer,
                "esito": "SALTATO_NESSUN_CANDIDATO",
                "dettaglio": target.get("notes", ""),
            })
            continue

        tentativi = []
        for url in candidates:
            tentativi.append(probe_one(ticker, issuer, url))

        # tengo tutti i tentativi visibili (utile se il primo fallisce ma il secondo va)
        results.append({
            "ticker": ticker,
            "issuer": issuer,
            "tentativi": tentativi,
        })

    output = {
        "generato_il_utc": datetime.now(timezone.utc).isoformat(),
        "nota": "Sonda esplorativa. OK_HOLDINGS_ESTRATTI = dato reale utilizzabile. "
                "Qualsiasi altro esito = questa fonte NON va usata cosi' com'e', "
                "va indagata o scartata (mai forzata nel motore).",
        "risultati": results,
    }

    RESULT_PATH.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"Scritto {RESULT_PATH}")

    # riepilogo leggibile a console/log Actions
    for r in results:
        if "tentativi" in r:
            esiti = [t["esito"] for t in r["tentativi"]]
            log(f"RIEPILOGO {r['ticker']}: {esiti}")
        else:
            log(f"RIEPILOGO {r['ticker']}: {r['esito']}")


if __name__ == "__main__":
    main()
                                    
