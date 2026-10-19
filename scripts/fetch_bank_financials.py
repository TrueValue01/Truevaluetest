#!/usr/bin/env python3
"""
Fetch produzione — dati finanziari banche via BankRegReports.

Sostituisce fetch_cet1_probe.py (sonda diagnostica su un solo ticker,
JPM) con un loop di produzione su tutti i ticker tracciati in TrueValue,
che scrive un JSON pulito pronto per l'app invece di un log esplorativo.

Due chiamate per banca:
1) /banks/{rssd}/ — snapshot sussidiaria: cet1_ratio, npl_ratio,
   texas_ratio, efficiency_ratio, acl_to_loans (per Coverage calcolata).
   ATTENZIONE: questo CET1 e' a livello di BANCA SUSSIDIARIA, non della
   holding company quotata in borsa — puo' differire di qualche decimo
   di punto dal CET1 "headline" che si legge nei comunicati stampa
   (confermato su JPM: sussidiaria 15.03%, holding "attesa" 14.8%).
   E' comunque un dato reale e tracciabile (FFIEC Call Report Schedule
   RC-R), solo non identico al numero di prima pagina.
2) /banks/{rssd}/structure/full/ — per risolvere l'RSSD della holding
   (parents[0].rssd_id), poi /holding-cos/{holding_rssd}/ per
   loan_growth_rate — l'UNICO posto dove questo dato esiste nell'API,
   non disponibile a livello di sussidiaria.

Se un pezzo manca per una banca, il campo resta assente nel JSON finale
(null) — mai un numero indovinato o un proxy silenzioso.
"""
import json
import os
import sys
import time
from datetime import datetime, timezone

import requests

API_KEY = os.environ.get("BRR_API_KEY", "").strip()
BASE = "https://api.bankregreports.com/api/v1"

# RSSD sussidiaria per ciascun ticker — verificati su due fonti indipendenti
# (CERT FDIC pre-esistenti + directory ufficiale BankRegReports, 13/13
# combacianti, sessione 17/10/2026).
BANK_RSSD = {
    "JPM": 852218, "BAC": 480228, "C": 476810, "WFC": 451965, "USB": 504713,
    "PNC": 817824, "TFC": 852320, "FITB": 723112, "HBAN": 12311,
    "CFG": 3303298, "MTB": 501105, "KEY": 280110, "RF": 233031,
}


def call(path, timeout=30):
    url = BASE + path
    sep = "&" if "?" in path else "?"
    if API_KEY:
        url = f"{url}{sep}api_key={API_KEY}"
    try:
        r = requests.get(url, headers={"Accept": "application/json"}, timeout=timeout)
        j = None
        try:
            j = r.json()
        except Exception:
            pass
        return {"status": r.status_code, "ok": r.ok, "json": j}
    except Exception as e:
        return {"status": None, "ok": False, "error": str(e)}


def fetch_one_bank(ticker, rssd):
    out = {
        "ticker": ticker, "rssd": rssd,
        "cet1": None, "npl": None, "texas": None, "ci": None, "cov": None,
        "lgr": None, "report_date": None, "holding_rssd": None,
        "fdic_cert_check": None, "legal_name": None, "errors": [],
    }

    snap = call(f"/banks/{rssd}/")
    if snap.get("ok") and isinstance(snap.get("json"), dict):
        d = (snap["json"] or {}).get("data") or {}
        out["cet1"] = d.get("cet1_ratio")
        out["npl"] = d.get("npl_ratio")
        out["texas"] = d.get("texas_ratio")
        out["ci"] = d.get("efficiency_ratio")
        acl, npl = d.get("acl_to_loans"), d.get("npl_ratio")
        if isinstance(acl, (int, float)) and isinstance(npl, (int, float)) and npl > 0:
            out["cov"] = round(acl / npl * 100, 4)
        out["report_date"] = d.get("report_date")
        out["fdic_cert_check"] = d.get("fdic_cert")
        out["legal_name"] = d.get("legal_name")
    else:
        out["errors"].append({"step": "bank_snapshot", "status": snap.get("status"), "error": snap.get("error")})

    struct = call(f"/banks/{rssd}/structure/full/")
    holding_rssd = None
    if struct.get("ok") and isinstance(struct.get("json"), dict):
        parents = ((struct["json"] or {}).get("data") or {}).get("parents") or []
        if parents:
            holding_rssd = parents[0].get("rssd_id")
            out["holding_rssd"] = holding_rssd
    else:
        out["errors"].append({"step": "structure_full", "status": struct.get("status"), "error": struct.get("error")})

    if holding_rssd:
        hold = call(f"/holding-cos/{holding_rssd}/")
        if hold.get("ok") and isinstance(hold.get("json"), dict):
            hd = (hold["json"] or {}).get("data") or {}
            lgr = hd.get("loan_growth_rate")
            if lgr is not None:
                try:
                    out["lgr"] = float(lgr)
                except (TypeError, ValueError):
                    pass
        else:
            out["errors"].append({"step": "holding_snapshot", "status": hold.get("status"), "error": hold.get("error")})

    return out


def main():
    if not API_KEY:
        print("ERRORE: secret BRR_API_KEY non impostato nel repo.", file=sys.stderr)
        sys.exit(1)

    banks = {}
    for ticker, rssd in BANK_RSSD.items():
        print(f"fetch {ticker} (RSSD {rssd})...")
        banks[ticker] = fetch_one_bank(ticker, rssd)
        n_ok = sum(1 for k in ("cet1", "npl", "texas", "ci", "cov", "lgr") if banks[ticker].get(k) is not None)
        print(f"  {n_ok}/6 campi ottenuti" + (f" — errori: {banks[ticker]['errors']}" if banks[ticker]["errors"] else ""))
        time.sleep(0.5)  # cortesia verso l'API, nessun limite noto ma non serve raffica

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": ("CET1 a livello di banca sussidiaria (FFIEC Call Report), non holding company "
                 "quotata — puo' differire leggermente dal dato 'headline' nei comunicati stampa. "
                 "Crescita prestiti (lgr) a livello holding, unico dove disponibile nell'API."),
        "banks": banks,
    }

    out_path = os.path.join(os.path.dirname(__file__), "..", "data", "banks.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f"Scritto {out_path}")


if __name__ == "__main__":
    main()
