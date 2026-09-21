#!/usr/bin/env python3
"""
Sonda NPL/Allowance via SEC XBRL - fase 3.

Fase 2 aveva due errori di categorizzazione, trovati sui dati REALI, non ipotizzati:
1) "FinancingReceivableExcludingAccruedInterestBeforeAllowanceForCreditLoss" (prestiti
   LORDI, prima dell'accantonamento) contiene la sottostringa "allowanceforcreditloss"
   e finiva scambiato per l'accantonamento stesso -> Coverage assurdo (21.172% per BAC).
2) Il denominatore "prestiti totali" cercava solo tag vecchi (NotesReceivableNet,
   LoansAndLeasesReceivableNetReportedAmount) fermi al 2016-2022 per molte banche.
   Le banche sono passate a una nuova famiglia di tag XBRL 2023+: "...ExcludingAccrued
   Interest[Before/After]AllowanceForCreditLoss" - quella e' la vera fonte aggiornata.

Fase 3: cerca esplicitamente Before e After (lordo e netto), esclude titoli di
debito e medie (non punti-nel-tempo), e calcola l'accantonamento come LORDO-NETTO
invece di cercare un terzo tag - e' aritmeticamente esatto, non un'approssimazione.
"""
import json
import os
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
    "gross_loans": ["beforeallowanceforcreditloss"],   # prestiti LORDI (denominatore NPL)
    "net_loans": ["afterallowanceforcreditloss"],       # prestiti NETTI (per derivare l'accantonamento)
    # Vecchi tag, tenuti come riserva se Before/After non esistono per una banca
    "total_loans_legacy": ["loansandleasesreceivablenetreportedamount", "notesreceivablenet"],
}

# Pattern nei NOMI dei concetti - rumore noto trovato sui dati reali, non ipotizzato:
# titoli di debito (non prestiti), medie (non punti-nel-tempo), voci di conto economico.
NOISE_PATTERNS = [
    "interestincome", "heldtomaturity", "fairvalueoption",
    "coveredandnotcovered", "acquiredintransfer", "lossgross",
    "lossnet", "writeoff", "recoveriesofbaddebts", "adjustmentsnet",
    "loansacquired", "duetosubsequentimpairment",
    "debtsecurities", "averageamountoutstanding", "noallowance",
]


def fetch_companyfacts(cik):
    url = BASE.format(cik)
    r = requests.get(url, headers=UA, timeout=45)
    if not r.ok:
        return {"status": r.status_code, "ok": False, "body_snippet": r.text[:400]}
    return {"status": 200, "ok": True, "json": r.json()}


def find_concepts(facts, keyword_list):
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
    filtered = [c for c in candidates if c["unit"] == unit_filter
                and not any(n in c["concept"].lower() for n in NOISE_PATTERNS)]
    filtered.sort(key=lambda c: (c.get("end") or "", abs(c.get("val") or 0)), reverse=True)
    return filtered


def find_at_date(ranked_list, target_date):
    """Cerca, in TUTTA la lista classificata (non solo il primo), un valore
    con 'end' esattamente uguale a target_date - il numeratore e il
    denominatore devono essere dello stesso trimestre, altrimenti il
    rapporto e' fuorviante anche se aritmeticamente calcolabile."""
    for c in ranked_list:
        if c.get("end") == target_date:
            return c
    return None


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

        nonaccrual_ranked = rank_candidates(find_concepts(facts, KEYWORDS["nonaccrual"]), "USD")
        gross_ranked = rank_candidates(find_concepts(facts, KEYWORDS["gross_loans"]), "USD")
        net_ranked = rank_candidates(find_concepts(facts, KEYWORDS["net_loans"]), "USD")
        legacy_ranked = rank_candidates(find_concepts(facts, KEYWORDS["total_loans_legacy"]), "USD")

        na = nonaccrual_ranked[0] if nonaccrual_ranked else None
        computed = {}
        if na:
            target = na["end"]
            gross_match = find_at_date(gross_ranked, target)
            net_match = find_at_date(net_ranked, target)
            legacy_match = find_at_date(legacy_ranked, target)

            denom = gross_match or legacy_match
            if denom and denom["val"]:
                computed["npl_pct"] = round(na["val"] / denom["val"] * 100, 3)
                computed["npl_period"] = target
                computed["npl_denominator_used"] = denom["concept"]
            else:
                computed["npl_pct"] = None
                computed["npl_note"] = f"nessun prestiti-totali trovato al {target} (ne' Before ne' legacy)"

            if gross_match and net_match:
                allowance_derived = gross_match["val"] - net_match["val"]
                computed["allowance_derivato_lordo_meno_netto"] = allowance_derived
                if allowance_derived and na["val"]:
                    computed["coverage_pct"] = round(allowance_derived / na["val"] * 100, 2)
                    computed["coverage_period"] = target
            else:
                computed["coverage_pct"] = None
                computed["coverage_note"] = f"servono Before E After allo stesso {target} per derivare l'accantonamento"
        else:
            computed["npl_pct"] = None
            computed["npl_note"] = "nessun concetto 'nonaccrual' in dollari trovato per questa banca"

        results[ticker] = {
            "entity_name": entity_name,
            "expected_npl_pct": info["expected_npl_pct"],
            "nonaccrual_top3": nonaccrual_ranked[:3],
            "gross_loans_top3": gross_ranked[:3],
            "net_loans_top3": net_ranked[:3],
            "legacy_total_loans_top2": legacy_ranked[:2],
            "computed": computed,
        }
        print(f"  NPL calcolato: {computed.get('npl_pct')} (atteso {info['expected_npl_pct']})")

    log = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "sonda NPL/Allowance via SEC XBRL - fase 3 (Before/After separati, accantonamento derivato per differenza, match esatto di data su tutta la lista non solo il primo)",
        "results": results,
    }
    out_path = os.path.join(os.path.dirname(__file__), "..", "npl-probe-log.json")
    with open(out_path, "w") as f:
        json.dump(log, f, indent=2, ensure_ascii=False)
    print(f"Scritto {out_path}")


if __name__ == "__main__":
    main()
                            
