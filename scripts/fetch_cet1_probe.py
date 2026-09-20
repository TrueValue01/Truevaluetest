#!/usr/bin/env python3
"""
Sonda CET1 holding — fase 2.
"""
import json
import os
import sys
from datetime import datetime, timezone

import requests

API_KEY = os.environ.get("BRR_API_KEY", "").strip()
BASE = "https://api.bankregreports.com/api/v1"

JPM_RSSD = 852218
JPM_EXPECTED_HOLDING_CET1 = 14.8

CONFIRMED_ENDPOINTS = {
    "bank_snapshot": f"/banks/{JPM_RSSD}/",
    "bank_structure": f"/banks/{JPM_RSSD}/structure/",
    "bank_structure_full": f"/banks/{JPM_RSSD}/structure/full/",
}

GUESS_ENDPOINTS = {
    "holding_guess_1": f"/holding-companies/{JPM_RSSD}/",
    "holding_guess_2": f"/holding_companies/{JPM_RSSD}/",
    "datasets_catalog": "/datasets/",
}


def call(path):
    url = BASE + path
    try:
        r = requests.get(
            url,
            headers={"Authorization": f"Bearer {API_KEY}", "Accept": "application/json"},
            timeout=20,
        )
        body_text = r.text
        body_json = None
        try:
            body_json = r.json()
        except Exception:
            pass
        return {
            "url": url,
            "status": r.status_code,
            "ok": r.ok,
            "json": body_json,
            "raw_snippet": body_text[:1500] if body_json is None else None,
        }
    except Exception as e:
        return {"url": url, "status": None, "ok": False, "error": str(e)}


def _flatten(d, prefix=""):
    items = []
    if isinstance(d, dict):
        for k, v in d.items():
            key = f"{prefix}.{k}" if prefix else k
            if isinstance(v, (dict, list)):
                items.extend(_flatten(v, key))
            else:
                items.append((key, v))
    elif isinstance(d, list):
        for i, v in enumerate(d):
            items.extend(_flatten(v, f"{prefix}[{i}]"))
    return items


def main():
    if not API_KEY:
        print("ERRORE: secret BRR_API_KEY non impostato nel repo.", file=sys.stderr)
        sys.exit(1)

    results = {}
    for name, path in CONFIRMED_ENDPOINTS.items():
        print(f"chiamo {name}: {path}")
        results[name] = call(path)

    holding_rssd = None
    struct_full = results.get("bank_structure_full", {}).get("json")
    if isinstance(struct_full, dict):
        parents = (struct_full.get("data") or {}).get("parents") or []
        if parents:
            holding_rssd = parents[0].get("rssd_id")
            print(f"trovata holding RSSD: {holding_rssd} ({parents[0].get('name')})")

    if isinstance(results.get("bank_structure_full", {}).get("json"), dict):
        d = results["bank_structure_full"]["json"].get("data") or {}
        results["bank_structure_full"]["json"] = {
            "data": {
                "rssd_id": d.get("rssd_id"),
                "parents": d.get("parents"),
                "n_subsidiaries": len(d.get("subsidiaries") or []),
            }
        }

    guess_paths = dict(GUESS_ENDPOINTS)
    if holding_rssd:
        guess_paths["banks_endpoint_on_holding_rssd"] = f"/banks/{holding_rssd}/"
        guess_paths["holding_guess_1"] = f"/holding-companies/{holding_rssd}/"
        guess_paths["holding_guess_2"] = f"/holding_companies/{holding_rssd}/"
    for name, path in guess_paths.items():
        print(f"chiamo {name}: {path}")
        results[name] = call(path)

    hints = []
    for name, res in results.items():
        j = res.get("json")
        if isinstance(j, dict):
            for k, v in _flatten(j):
                if "cet1" in k.lower() and isinstance(v, (int, float)):
                    close = abs(v - JPM_EXPECTED_HOLDING_CET1) <= 1.0
                    hints.append({
                        "endpoint": name,
                        "field": k,
                        "value": v,
                        "vicino_a_headline_atteso_14_8": close,
                    })

    log = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "probe fase 2",
        "holding_rssd_found": holding_rssd,
        "results": results,
        "cet1_field_hints": hints,
    }

    out_path = os.path.join(os.path.dirname(__file__), "..", "cet1-probe-log.json")
    with open(out_path, "w") as f:
        json.dump(log, f, indent=2, ensure_ascii=False)
    print(f"Scritto {out_path}")
    print(json.dumps(hints, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
