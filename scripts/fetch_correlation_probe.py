#!/usr/bin/env python3
"""
Sonda diagnostica — copertura dati storici Stooq per le 6 posizioni reali di
"TrueValue Alfa" (CB, CI, GNTX, CMCSA, L, VWCE).

Scopo: verificare PRIMA di scrivere qualsiasi calcolo di correlazione se e come
Stooq serve lo storico prezzi per ciascun ticker — stesso principio del progetto
(mai costruire su una fonte non verificata dal vivo). Non scrive nessun file
dati, stampa solo un report leggibile nel log dell'Action.

Endpoint testato: https://stooq.com/q/d/l/?s=<simbolo>&i=d (CSV giornaliero,
stesso pattern già usato da varie librerie note — es. pandas-datareader,
Finance::Quote::Stooq — per singolo ticker, diverso dall'export bulk per
borsa che su Stooq richiede CAPTCHA).

Per le 5 azioni US il simbolo Stooq è quasi sempre <ticker>.us (minuscolo).
Per VWCE (ETF UCITS multi-quotato) non c'e' un simbolo ovvio: provo piu'
candidati sulle piazze dove e' effettivamente quotato (Borsa Italiana, Xetra,
Euronext) — li elenco tutti con la risposta grezza, NESSUNA scelta
automatica: decidiamo noi due guardando quale risponde con dati veri.
"""
import urllib.request
import urllib.error

STOCK_CANDIDATES = {
    "CB":    ["cb.us"],
    "CI":    ["ci.us"],
    "GNTX":  ["gntx.us"],
    "CMCSA": ["cmcsa.us"],
    "L":     ["l.us"],
}

# VWCE: ISIN IE00BK5BQT80, quotato su Borsa Italiana (XETR/MIL), Xetra (DE),
# Euronext Amsterdam. Simboli Stooq candidati da verificare dal vivo.
ETF_CANDIDATES = {
    "VWCE": ["vwce.de", "vwce.mi", "vwce.uk", "vwce.nl"],
}

UA = {"User-Agent": "Mozilla/5.0 (compatible; TrueValue-probe/1.0; Francesco personal use)"}


def fetch(symbol):
    url = f"https://stooq.com/q/d/l/?s={symbol}&i=d"
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            status = r.status
            body = r.read().decode("utf-8", errors="replace")
            return status, body
    except urllib.error.HTTPError as e:
        return e.code, (e.read().decode("utf-8", errors="replace") if e.fp else str(e))
    except Exception as e:
        return None, str(e)


def report(label, symbol, status, body):
    print(f"\n=== {label} — simbolo testato: {symbol} ===")
    print(f"HTTP status: {status}")
    lines = body.strip().splitlines() if body else []
    if not lines:
        print("Risposta vuota.")
        return
    header = lines[0]
    print(f"Prima riga (header atteso 'Date,Open,High,Low,Close,Volume'): {header}")
    looks_like_csv = header.lower().startswith("date,") or "," in header and len(lines) > 2
    if "exceeded" in body.lower() or "captcha" in body.lower() or "<html" in body.lower()[:200]:
        print("ATTENZIONE: la risposta sembra una pagina HTML/blocco, non un CSV — simbolo probabilmente sbagliato o accesso negato.")
    elif looks_like_csv:
        print(f"Righe totali: {len(lines)} (incluso header)")
        print(f"Prima riga dati: {lines[1] if len(lines) > 1 else '(nessuna)'}")
        print(f"Ultima riga dati: {lines[-1]}")
    else:
        print("Formato non riconosciuto, contenuto grezzo (primi 300 caratteri):")
        print(body[:300])


def main():
    print("### SONDA COPERTURA STOOQ — TrueValue Alfa ###")
    for ticker, symbols in STOCK_CANDIDATES.items():
        for sym in symbols:
            status, body = fetch(sym)
            report(ticker, sym, status, body)
    for ticker, symbols in ETF_CANDIDATES.items():
        for sym in symbols:
            status, body = fetch(sym)
            report(ticker, sym, status, body)
    print("\n### FINE SONDA — incolla tutto questo log in chat ###")


if __name__ == "__main__":
    main()
