#!/usr/bin/env python3
"""
Sonda Combined Ratio via SEC XBRL - fase 1.

Stessa logica gia' collaudata oggi per NPL banche (fetch_npl_probe.py):
cerca concetti per parola chiave, classifica per pertinenza+recency, calcola
solo se le date combaciano davvero. Riuso diretto delle funzioni, cambiano
solo le parole chiave e le 4 assicurazioni (CIK gia' noti dal test FMP).

Combined Ratio = (Sinistri incorsi + Spese generali/amministrative +
Ammortamento costi di acquisizione polizze) / Premi guadagnati netti.
Se un pezzo manca o le date non combaciano, il calcolo NON parte - meglio
"non lo so ancora" che un rapporto costruito su trimestri diversi.
"""
import json
import os
from datetime import datetime, timezone

import requests

BASE = "https://data.sec.gov/api/xbrl/companyfacts/CIK{}.json"
UA = {"User-Agent": "TrueValue Francesco truevalue01@gmail.com", "Accept": "application/json"}

# CIK presi dal test FMP di oggi (gia' confermati, stesso ticker)
INSURERS = {
    "PGR": {"cik": "0000732717"},
    "TRV": {"cik": "0000086312"},
    "ALL": {"cik": "0000899051"},
    "CB":  {"cik": "0000896159"},
}

KEYWORDS = {
    "premiums_earned": ["premiumsearnednet", "premiumsearned"],
    "losses_incurred": ["policyholderbenefitsandclaimsincurred", "incurredclaimsandclaims",
                         "lossesandlossadjustmentexpense", "benefitsandclaimsincurrednet"],
    "ga_expense": ["generalandadministrativeexpense"],
    "dac_amortization": ["deferredpolicyacquisitioncostsamortizationexpense", "amortizationofdeferredpolicyacquisitioncosts"],
}

NOISE_PATTERNS = ["priorperiod", "cumulative", "discontinued"]


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


def rank_candidates(candidates, unit_filter="USD"):
    filtered = [c for c in candidates if c["unit"] == unit_filter
                and not any(n in c["concept"].lower() for n in NOISE_PATTERNS)]
    filtered.sort(key=lambda c: (c.get("end") or "", abs(c.get("val") or 0)), reverse=True)
    return filtered


def find_at_date(ranked_list, target_date):
    for c in ranked_list:
        if c.get("end") == target_date:
            return c
    return None


def main():
    results = {}
    for ticker, info in INSURERS.items():
        print(f"chiamo companyfacts per {ticker} (CIK {info['cik']})")
        cf = fetch_companyfacts(info["cik"])
        if not cf.get("ok"):
            results[ticker] = {"error": cf}
            continue
        facts = cf["json"].get("facts", {})
        entity_name = cf["json"].get("entityName")

        premiums = rank_candidates(find_concepts(facts, KEYWORDS["premiums_earned"]))
        losses = rank_candidates(find_concepts(facts, KEYWORDS["losses_incurred"]))
        ga = rank_candidates(find_concepts(facts, KEYWORDS["ga_expense"]))
        dac = rank_candidates(find_concepts(facts, KEYWORDS["dac_amortization"]))

        computed = {}
        pe = premiums[0] if premiums else None
        if pe:
            target = pe["end"]
            l_match = find_at_date(losses, target)
            ga_match = find_at_date(ga, target)
            dac_match = find_at_date(dac, target)
            parts_found = [p for p in [l_match, ga_match, dac_match] if p]
            if l_match and pe["val"]:
                numerator = sum(p["val"] for p in parts_found)
                computed["combined_ratio_pct"] = round(numerator / pe["val"] * 100, 2)
                computed["period"] = target
                computed["numeratore_da"] = [p["concept"] for p in parts_found]
                computed["denominatore_da"] = pe["concept"]
                computed["nota"] = ("G&A e/o ammortamento DAC assenti a questa data: rapporto"
                                     " calcolato solo su sinistri/premi, sottostima il vero Combined Ratio"
                                     if not (ga_match and dac_match) else None)
            else:
                computed["combined_ratio_pct"] = None
                computed["nota"] = f"sinistri incorsi assenti al {target} (premi trovati, sinistri no)"
        else:
            computed["combined_ratio_pct"] = None
            computed["nota"] = "nessun concetto 'premiums earned' trovato per questa compagnia"

        results[ticker] = {
            "entity_name": entity_name,
            "premiums_earned_top3": premiums[:3],
            "losses_incurred_top3": losses[:3],
            "ga_expense_top3": ga[:3],
            "dac_amortization_top3": dac[:3],
            "computed": computed,
        }
        print(f"  Combined Ratio: {computed.get('combined_ratio_pct')}")

    log = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "sonda Combined Ratio via SEC XBRL - fase 1 (PGR/TRV/ALL/CB, gli stessi 4 gia' testati su FMP)",
        "results": results,
    }
    out_path = os.path.join(os.path.dirname(__file__), "..", "insurance-probe-log.json")
    with open(out_path, "w") as f:
        json.dump(log, f, indent=2, ensure_ascii=False)
    print(f"Scritto {out_path}")


if __name__ == "__main__":
    main()
