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
    "PGR": {"cik": "0000080661"},   # FMP dava 0000732717 (AT&T!) nel campo cik della risposta - verificato da 4+ fonti indipendenti che il vero e' 80661
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

NOISE_PATTERNS = ["priorperiod", "cumulative", "discontinued", "paymentsfor", "reservefor"]


def fetch_companyfacts(cik):
    url = BASE.format(cik)
    r = requests.get(url, headers=UA, timeout=45)
    if not r.ok:
        return {"status": r.status_code, "ok": False, "body_snippet": r.text[:400]}
    return {"status": 200, "ok": True, "json": r.json()}


def find_concepts(facts, keyword_list):
    """Cerca in TUTTE le tassonomie qualunque concetto che contenga una delle
    parole chiave. IMPORTANTE: tiene un valore per OGNI data di fine distinta,
    non solo l'ultimo in assoluto - altrimenti un concetto con storia sia
    trimestrale che annuale perde le date annuali (dove serve allinearsi col
    DAC, taggato solo una volta l'anno). Questo era il bug reale della fase 1:
    sembrava che i dati mancassero, in realta' li scartavo io in fase di lettura.
    """
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
                            by_end[end] = v  # l'ultimo depositato per quella data vince (rettifiche)
                    for end, v in by_end.items():
                        found.append({
                            "taxonomy": taxonomy, "concept": concept, "label": payload.get("label"),
                            "unit": unit, "val": v.get("val"), "end": end,
                            "form": v.get("form"), "fy": v.get("fy"), "fp": v.get("fp"),
                        })
    return found


def dedupe_for_display(ranked_list, n=3):
    """Solo per il JSON di log: un concetto per riga, non lo stesso concetto
    ripetuto a 10 date diverse. find_at_date lavora sulla lista COMPLETA,
    non su questa versione ridotta per la leggibilita'."""
    seen = set()
    out = []
    for c in ranked_list:
        if c["concept"] not in seen:
            seen.add(c["concept"])
            out.append(c)
        if len(out) >= n:
            break
    return out


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
        # PRIORITA' ESPLICITA al premio NETTO (dopo riassicurazione ceduta): sinistri
        # e spese sono gia' al netto della riassicurazione, quindi il denominatore deve
        # esserlo anche lui - altrimenti si mischiano lordo/netto e il rapporto viene
        # sottostimato (visto sui dati reali: CB con "Direct" dava 67.66%, implausibile
        # per un'azienda che storicamente sta 85-90%). "Net" va sempre prima di "Direct"
        # quando entrambi esistono alla stessa data, non solo per valore piu' alto.
        premiums_net = [p for p in premiums if p["concept"] == "PremiumsEarnedNet"]
        premiums_other = [p for p in premiums if p["concept"] != "PremiumsEarnedNet"]
        premiums_candidates = (premiums_net + premiums_other)[:8]  # non solo il piu' recente: l'ammortamento DAC
        # e' taggato solo ANNUALMENTE (Schedule 12-16/12-18) - un trimestre recente
        # spesso non ha tutti e 4 i pezzi. Cerco il primo periodo (partendo dal piu'
        # recente, e dando priorita' al Netto) dove premi+sinistri+G&A+DAC coincidono
        # TUTTI, prima di ripiegare su un calcolo parziale.
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
            computed["combined_ratio_pct"] = cr
            computed["period"] = pe["end"]
            computed["numeratore_da"] = [l_match["concept"], ga_match["concept"], dac_match["concept"]]
            computed["denominatore_da"] = pe["concept"]
            computed["completo"] = True
            # Un P&C sano sta storicamente 85-105%, anche un anno pessimo raramente
            # supera 115-120%. Fuori da 40-130% e' quasi certamente un problema di
            # metodo (es. G&A e ammortamento DAC che si sovrappongono, non si sommano)
            # - meglio dirlo chiaro che spacciare il numero per pulito.
            if not (40 <= cr <= 130):
                computed["sospetto_metodo"] = ("Fuori dal range plausibile 40-130% — probabile "
                    "che G&A e ammortamento DAC si sovrappongano parzialmente invece di sommarsi "
                    "puliti. NON usare questo numero finche' non si verifica il metodo.")
        elif premiums_candidates:
            pe = premiums_candidates[0]
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
                computed["completo"] = False
                computed["nota"] = "parziale: nessun periodo con tutti e 4 i pezzi trovato, mostro il piu' recente disponibile (sottostima)"
            else:
                computed["combined_ratio_pct"] = None
                computed["nota"] = f"sinistri incorsi assenti al {target} (premi trovati, sinistri no)"
        else:
            computed["combined_ratio_pct"] = None
            computed["nota"] = "nessun concetto 'premiums earned' trovato per questa compagnia"

        results[ticker] = {
            "entity_name": entity_name,
            "premiums_earned_top3": dedupe_for_display(premiums),
            "losses_incurred_top3": dedupe_for_display(losses),
            "ga_expense_top3": dedupe_for_display(ga),
            "dac_amortization_top3": dedupe_for_display(dac),
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
            
