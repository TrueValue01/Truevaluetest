#!/usr/bin/env python3
"""
Sonda NPL/Allowance via SEC XBRL - fase 2.

Fase 1 ha confermato che i concetti ESISTONO (es. WFC ha "Financing
Receivable, Nonaccrual" $5.99B, USB ne ha uno da $972M) ma la sonda
prendeva il PRIMO risultato trovato invece del piu' pertinente — per WFC
aveva preso "Debt Securities Held-to-maturity Nonaccrual" (titoli, non
prestiti, valore quasi zero) solo perche' compariva prima nel dizionario.
Questa fase classifica i candidati (esclude pattern noti irrilevanti,
ordina per data piu' recente) invece di prendere il primo a caso.

Nota anche: CIK Bank of America (70858) e' confermato corretto da piu'
indici di deposito SEC ufficiali; il campo "entityName" della risposta
compare come "BofA Finance LLC" (probabile controllata co-depositante),
ma i valori (prestiti ~980 miliardi) sono coerenti con BAC vera, non con
una piccola controllata — la CIFRA e' giusta, l'ETICHETTA e' fuorviante.
Segnalato nel log, non "corretto" perche' non c'e' nulla da correggere
nella richiesta.
"""
import json
import os
import re
import sys
from datetime import datetime, timezone

import requests

BASE = "https://data.sec.gov/api/xbrl/companyfacts/CIK{}.json"
UA = {"User-Agent": "TrueValue Francesco truevalue01@gmail.com", "Accept": "application/json"}

BANKS = {
    "JPM": {"cik": "0000019617", "expected_npl_pct": 0.87},
    "BAC": {"cik": "0000070858", "expected_npl_pct": 0.73},
    "WFC": {"cik": "0000072971", "expected_npl_pct": 0.87},
    "USB": {"cik": "0000036104", "expected_npl_pct": 0.39},
}

KEYWORDS = {
    "nonaccrual": ["nonaccrual"],
    "allowance": ["allowanceforloanandlease", "allowanceforcreditloss", "financingreceivableallowance"],
    "total_loans": ["loansandleasesreceivablenetreportedamount", "financingreceivableafterallowanceforcreditloss",
                     "notesreceivablenet", "loansandleasereceivablenetofallowance"],
}

# Pattern nei NOMI dei concetti che sappiamo essere rumore per questo scopo -
# trovati concretamente in fase 1 (non ipotizzati). Esclusi dalla classifica,
# non dalla ricerca: restano visibili altrove se servono per altro.
NOISE_PATTERNS = [
    "interestincome", "heldtomaturity", "fairvalueoption",
    "coveredandnotcovered", "acquiredintransfer", "lossgross",
    "lossnet", "writeoff", "recoveriesofbaddebts", "adjustmentsnet",
    "loansacquired", "duetosubsequentimpairment",
]


def fetch_companyfacts(cik):
    url = BASE.format(cik)
    r = requests.get(url, headers=UA, timeout=45)
    if not r.ok:
        return {"status": r.status_code, "ok": False, "body_snippet": r.text[:400]}
    return {"status": 200, "ok": True, "json": r.json()}


def find_concepts(facts, keyword_list):
    """Cerca in TUTTE le tassonomie qualunque concetto che contenga una delle
    parole chiave. Non tronca e non ordina qui - lo fa rank_candidates dopo."""
    found = []
    for taxonomy, concepts in facts.items():
        for concept, payload in concepts.items():
            c = concept.lower()
            if any(kw in c for kw in keyword_list):
                units = payload.get("units", {})
                for unit, arr in units.items():
                    if not arr:
                        continue
                    v = sorted(arr, key=lambda x: x.get("end", ""))[-1]
                    found.append({
                        "taxonomy": taxonomy, "concept": concept, "label": payload.get("label"),
                        "unit": unit, "val": v.get("val"), "end": v.get("end"),
                        "form": v.get("form"), "fy": v.get("fy"), "fp": v.get("fp"),
                    })
    return found


def rank_candidates(candidates, unit_filter):
    """Filtra per unita' (USD o pure), esclude i pattern rumore noti,
    ordina per data piu' recente. Il migliore candidato e' il primo dopo
    questo ordinamento, non il primo trovato nel dizionario originale."""
    filtered = [c for c in candidates if c["unit"] == unit_filter
                and not any(n in c["concept"].lower() for n in NOISE_PATTERNS)]
    filtered.sort(key=lambda c: (c.get("end") or "", abs(c.get("val") or 0)), reverse=True)
    return filtered


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

        raw_nonaccrual = find_concepts(facts, KEYWORDS["nonaccrual"])
        raw_allowance = find_concepts(facts, KEYWORDS["allowance"])
        raw_total_loans = find_concepts(facts, KEYWORDS["total_loans"])

        # Concetti gia' in forma di rapporto (unit 'pure') - potrebbero ESSERE
        # gia' l'NPL% cercato, senza bisogno di calcolo nostro.
        nonaccrual_ratio_candidates = rank_candidates(raw_nonaccrual, "pure")[:3]
        allowance_ratio_candidates = rank_candidates(raw_allowance, "pure")[:3]

        # Concetti in dollari, classificati (rumore escluso, piu' recente primo)
        nonaccrual_usd = rank_candidates(raw_nonaccrual, "USD")[:3]
        allowance_usd = rank_candidates(raw_allowance, "USD")[:3]
        total_loans_usd = rank_candidates(raw_total_loans, "USD")[:3]

        computed = {}
        na = nonaccrual_usd[0] if nonaccrual_usd else None
        tl = total_loans_usd[0] if total_loans_usd else None
        al = allowance_usd[0] if allowance_usd else None
        # Calcolo SOLO se i periodi combaciano davvero - altrimenti il numero
        # e' fuorviante anche se aritmeticamente "calcolabile".
        if na and tl and na["end"] == tl["end"] and tl["val"]:
            computed["npl_pct"] = round(na["val"] / tl["val"] * 100, 3)
            computed["npl_period"] = na["end"]
        else:
            computed["npl_pct"] = None
            computed["npl_note"] = (
                f"periodi non allineati: nonaccrual={na['end'] if na else 'assente'} "
                f"vs prestiti totali={tl['end'] if tl else 'assente'}"
            )
        if na and al and na["end"] == al["end"] and na["val"]:
            computed["coverage_pct"] = round(al["val"] / na["val"] * 100, 3)
            computed["coverage_period"] = na["end"]
        else:
            computed["coverage_pct"] = None
            computed["coverage_note"] = (
                f"periodi non allineati: nonaccrual={na['end'] if na else 'assente'} "
                f"vs allowance={al['end'] if al else 'assente'}"
            )

        results[ticker] = {
            "entity_name": entity_name,
            "expected_npl_pct": info["expected_npl_pct"],
            "nonaccrual_ratio_candidates_gia_pronti": nonaccrual_ratio_candidates,
            "nonaccrual_usd_top3": nonaccrual_usd,
            "allowance_usd_top3": allowance_usd,
            "total_loans_usd_top3": total_loans_usd,
            "computed": computed,
        }
        print(f"  top nonaccrual: {nonaccrual_usd[0]['concept'] if nonaccrual_usd else 'nessuno'}")

    log = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "sonda NPL/Allowance via SEC XBRL - fase 2 (classifica per pertinenza+recency, non piu' il primo trovato)",
        "results": results,
    }
    out_path = os.path.join(os.path.dirname(__file__), "..", "npl-probe-log.json")
    with open(out_path, "w") as f:
        json.dump(log, f, indent=2, ensure_ascii=False)
    print(f"Scritto {out_path}")


if __name__ == "__main__":
    main()

