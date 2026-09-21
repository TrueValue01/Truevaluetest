#!/usr/bin/env python3
"""
Sonda NPL/Allowance via SEC XBRL - fase 1.

A differenza del CET1 (concetto regolamentare, mai in GAAP), i prestiti
non-performing (nonaccrual) e l'allowance per perdite su crediti SONO voci
di bilancio GAAP standard, quasi certamente taggate in XBRL. Cerca concetti
per parola chiave (mai un nome esatto indovinato) su 4 banche: le due dove
FDIC ha dato dati sbagliati (WFC, USB) + due di controllo (JPM, BAC).

NPL% e Coverage% NON sono nel XBRL come rapporto - sono dollari. Li calcoliamo
noi: NPL% = nonaccrual / prestiti_totali * 100, Coverage% = allowance / nonaccrual * 100.
Se SEC non ha "prestiti totali" taggato in modo affidabile, lo segnaliamo,
non lo indoviniamo.
"""
import json
import os
import sys
from datetime import datetime, timezone

import requests

BASE = "https://data.sec.gov/api/xbrl/companyfacts/CIK{}.json"
UA = {"User-Agent": "TrueValue Francesco truevalue01@gmail.com", "Accept": "application/json"}

# CIK noti (verificati in sessioni precedenti / fonti pubbliche)
BANKS = {
    "JPM": {"cik": "0000019617", "expected_npl_pct": 0.87},   # ground truth Q3'25 (BATTERIA)
    "BAC": {"cik": "0000070858", "expected_npl_pct": 0.73},
    "WFC": {"cik": "0000072971", "expected_npl_pct": 0.87},   # FDIC ci aveva dato 1.04 (sbagliato)
    "USB": {"cik": "0000036104", "expected_npl_pct": 0.39},   # FDIC ci aveva dato 1.45 (sbagliato)
}

# Parole chiave per la ricerca - MAI un nome esatto indovinato, cerchiamo
# qualunque concetto (in qualunque tassonomia, us-gaap o custom) che le contenga.
KEYWORDS = {
    "nonaccrual": ["nonaccrual"],
    "allowance": ["allowanceforloanandlease", "allowanceforcreditloss", "financingreceivableallowance"],
    "total_loans": ["loansandleasesreceivablenetreportedamount", "financingreceivableafterallowanceforcreditloss",
                     "notesreceivablenet", "loansandleasereceivablenetofallowance"],
}


def fetch_companyfacts(cik):
    url = BASE.format(cik)
    r = requests.get(url, headers=UA, timeout=45)
    if not r.ok:
        return {"status": r.status_code, "ok": False, "body_snippet": r.text[:400]}
    return {"status": 200, "ok": True, "json": r.json()}


def find_concepts(facts, keyword_list):
    """Cerca in TUTTE le tassonomie (us-gaap + eventuali custom della banca)
    qualunque concetto il cui nome contenga una delle parole chiave."""
    found = []
    for taxonomy, concepts in facts.items():
        for concept, payload in concepts.items():
            c = concept.lower()
            if any(kw in c for kw in keyword_list):
                units = payload.get("units", {})
                latest = None
                for unit, arr in units.items():
                    if arr:
                        v = sorted(arr, key=lambda x: x.get("end", ""))[-1]
                        latest = {"unit": unit, "val": v.get("val"), "end": v.get("end"),
                                  "form": v.get("form"), "fy": v.get("fy"), "fp": v.get("fp")}
                found.append({
                    "taxonomy": taxonomy, "concept": concept,
                    "label": payload.get("label"), "latest": latest,
                })
    return found


def main():
    results = {}
    for ticker, info in BANKS.items():
        print(f"chiamo companyfacts per {ticker} (CIK {info['cik']})")
        cf = fetch_companyfacts(info["cik"])
        if not cf.get("ok"):
            results[ticker] = {"error": cf}
            continue
        facts = cf["json"].get("facts", {})
        entity_name = cf["json"].get("entityName")

        nonaccrual = find_concepts(facts, KEYWORDS["nonaccrual"])
        allowance = find_concepts(facts, KEYWORDS["allowance"])
        total_loans = find_concepts(facts, KEYWORDS["total_loans"])

        # Calcolo NPL% e Coverage% SOLO se abbiamo valori utilizzabili, in
        # dollari coerenti (stessa unit, stesso periodo di fine). Non mischio
        # trimestri diversi tra numeratore e denominatore.
        computed = {}
        na_val = next((c["latest"] for c in nonaccrual if c.get("latest")), None)
        tl_val = next((c["latest"] for c in total_loans if c.get("latest")), None)
        al_val = next((c["latest"] for c in allowance if c.get("latest")), None)
        if na_val and tl_val and na_val["unit"] == tl_val["unit"] and tl_val["val"]:
            computed["npl_pct"] = round(na_val["val"] / tl_val["val"] * 100, 3)
            computed["npl_periods_match"] = (na_val["end"] == tl_val["end"])
        if na_val and al_val and na_val["unit"] == al_val["unit"] and na_val["val"]:
            computed["coverage_pct"] = round(al_val["val"] / na_val["val"] * 100, 3)
            computed["coverage_periods_match"] = (na_val["end"] == al_val["end"])

        results[ticker] = {
            "entity_name": entity_name,
            "expected_npl_pct": info["expected_npl_pct"],
            "nonaccrual_concepts_found": nonaccrual[:5],
            "allowance_concepts_found": allowance[:5],
            "total_loans_concepts_found": total_loans[:5],
            "computed": computed,
        }
        print(f"  trovati: {len(nonaccrual)} nonaccrual, {len(allowance)} allowance, {len(total_loans)} total_loans")

    log = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "sonda NPL/Allowance via SEC XBRL - fase 1 (JPM/BAC controllo, WFC/USB dove FDIC ha sbagliato)",
        "results": results,
    }
    out_path = os.path.join(os.path.dirname(__file__), "..", "npl-probe-log.json")
    with open(out_path, "w") as f:
        json.dump(log, f, indent=2, ensure_ascii=False)
    print(f"Scritto {out_path}")


if __name__ == "__main__":
    main()
              
