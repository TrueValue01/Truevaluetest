#!/usr/bin/env python3
"""
Fetch produzione — Combined Ratio assicurazioni via SEC XBRL.

Estende fetch_insurance_probe.py (sonda su 4 ticker: PGR/TRV/ALL/CB) con
AIG (CIK confermato via ricerca web, 18/10/2026: 0000005272) e scrive un
JSON pulito pronto per l'app invece del solo log diagnostico — la logica
di calcolo (ricerca concetti, priorita' al premio netto, matching per
data, range di plausibilita' 40-130%) e' invariata, e' quella gia'
verificata sui dati reali.

Combined Ratio = (Sinistri incorsi + Spese generali/amministrative +
Ammortamento costi di acquisizione polizze) / Premi guadagnati netti.
Se un pezzo manca o le date non combaciano, il campo resta null — mai
un numero parziale spacciato per completo senza dirlo.
"""
import json
import os
from datetime import datetime, timezone

import requests

BASE = "https://data.sec.gov/api/xbrl/companyfacts/CIK{}.json"
UA = {"User-Agent": "TrueValue Francesco truevalue01@gmail.com", "Accept": "application/json"}

INSURERS = {
    "PGR": {"cik": "0000080661"},
    "TRV": {"cik": "0000086312"},
    "ALL": {"cik": "0000899051"},
    "CB":  {"cik": "0000896159"},
    "AIG": {"cik": "0000005272"},  # confermato via ricerca web 18/10/2026
}

KEYWORDS = {
    "premiums_earned": ["premiumsearnednet", "premiumsearned"],
    "losses_incurred": ["policyholderbenefitsandclaimsincurred", "incurredclaimsandclaims",
                         "lossesandlossadjustmentexpense", "benefitsandclaimsincurrednet"],
    "ga_expense": ["generalandadministrativeexpense"],
    "dac_amortization": ["deferredpolicyacquisitioncostsamortizationexpense", "amortizationofdeferredpolicyacquisitioncosts"],
}

NOISE_PATTERNS = ["priorperiod", "cumulative", "discontinued", "paymentsfor", "reservefor"]


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
                    by_end = {}
                    for v in arr:
                        end = v.get("end")
                        if end:
                            by_end[end] = v
                    for end, v in by_end.items():
                        found.append({
                            "taxonomy": taxonomy, "concept": concept, "label": payload.get("label"),
                            "unit": unit, "val": v.get("val"), "end": end,
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


def compute_combined_ratio(facts):
    premiums = rank_candidates(find_concepts(facts, KEYWORDS["premiums_earned"]))
    losses = rank_candidates(find_concepts(facts, KEYWORDS["losses_incurred"]))
    ga = rank_candidates(find_concepts(facts, KEYWORDS["ga_expense"]))
    dac = rank_candidates(find_concepts(facts, KEYWORDS["dac_amortization"]))

    premiums_net = [p for p in premiums if p["concept"] == "PremiumsEarnedNet"]
    premiums_other = [p for p in premiums if p["concept"] != "PremiumsEarnedNet"]
    premiums_candidates = (premiums_net + premiums_other)[:8]

    complete_match = None
    for pe_candidate in premiums_candidates:
        target = pe_candidate["end"]
        l_m, ga_m, dac_m = find_at_date(losses, target), find_at_date(ga, target), find_at_date(dac, target)
        if l_m and ga_m and dac_m:
            complete_match = (pe_candidate, l_m, ga_m, dac_m)
            break

    if complete_match:
        pe, l_match, ga_match, dac_match = complete_match
        numerator = l_match["val"] + ga_match["val"] + dac_match["val"]
        cr = round(numerator / pe["val"] * 100, 2)
        plausible = 40 <= cr <= 130
        return {
            "combined_ratio_pct": cr if plausible else None,
            "period": pe["end"],
            "completo": True,
            "plausibile": plausible,
            "nota": None if plausible else ("Fuori dal range plausibile 40-130% — probabile sovrapposizione "
                "metodo G&A/DAC. Scartato, non scritto come valore valido."),
        }
    elif premiums_candidates:
        pe = premiums_candidates[0]
        target = pe["end"]
        l_match, ga_match, dac_match = find_at_date(losses, target), find_at_date(ga, target), find_at_date(dac, target)
        parts_found = [p for p in [l_match, ga_match, dac_match] if p]
        if l_match and pe["val"]:
            numerator = sum(p["val"] for p in parts_found)
            cr = round(numerator / pe["val"] * 100, 2)
            return {
                "combined_ratio_pct": None,  # parziale: non abbastanza affidabile per scrivere in automatico
                "period": target, "completo": False, "plausibile": None,
                "nota": "parziale: nessun periodo con tutti e 4 i pezzi — non scritto, serve verifica manuale",
            }
        return {"combined_ratio_pct": None, "completo": False,
                "nota": f"sinistri incorsi assenti al {target}"}
    return {"combined_ratio_pct": None, "completo": False,
            "nota": "nessun concetto 'premiums earned' trovato"}


def main():
    insurance = {}
    for ticker, info in INSURERS.items():
        print(f"fetch {ticker} (CIK {info['cik']})...")
        cf = fetch_companyfacts(info["cik"])
        if not cf.get("ok"):
            insurance[ticker] = {"ticker": ticker, "combined_ratio_pct": None,
                                  "errore": {"status": cf.get("status")}}
            continue
        facts = cf["json"].get("facts", {})
        entity_name = cf["json"].get("entityName")
        computed = compute_combined_ratio(facts)
        insurance[ticker] = {"ticker": ticker, "entity_name": entity_name, **computed}
        print(f"  Combined Ratio: {computed.get('combined_ratio_pct')} ({computed.get('period')})")

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": ("Combined Ratio calcolato da SEC XBRL (premi netti/sinistri/G&A/DAC allineati per data). "
                 "combined_ratio_pct resta null se il calcolo non e' completo o fuori range plausibile — "
                 "mai un numero parziale o sospetto scritto come buono."),
        "insurance": insurance,
    }

    out_path = os.path.join(os.path.dirname(__file__), "..", "data", "insurance.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f"Scritto {out_path}")


if __name__ == "__main__":
    main()
