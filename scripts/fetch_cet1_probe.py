#!/usr/bin/env python3
"""
Sonda CET1 holding - fase 3.

Fase 2 ha confermato: bank_snapshot per RSSD 852218 (JPM subsidiary) da
cet1_ratio 16.084 (troppo alto, e' il subsidiary). structure/full ha dato
l'RSSD della holding: 1039502 (JPMORGAN CHASE & CO.). Indovinare il path
dell'endpoint holding (holding-companies/, holding_companies/) ha dato 404
entrambe le volte.

Fase 3: invece di indovinare un terzo path, leggo il catalogo /datasets/
(che elenca ogni endpoint con il suo path REALE) e cerco da sola le voci
che parlano di "holding" - poi le chiamo con l'RSSD trovato sopra. Non
logghero' l'intero catalogo (e' enorme, ha troncato il file la volta
scorsa): solo le voci holding trovate.
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


def call(path, timeout=20):
    url = BASE + path
    try:
        r = requests.get(
            url,
            headers={"Authorization": f"Bearer {API_KEY}", "Accept": "application/json"},
            timeout=timeout,
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
            "raw_snippet": body_text[:800] if body_json is None else None,
        }
    except Exception as e:
        return {"url": url, "status": None, "ok": False, "error": str(e)}


def _find_holding_datasets(catalog_json):
    """Cerca ricorsivamente, nel catalogo, le voci con 'holding' nel nome/titolo/categoria.
    Ogni voce dataset ha almeno 'key' e 'path' — quelle sono le uniche che tengo."""
    found = []

    def walk(node):
        if isinstance(node, dict):
            key = str(node.get("key", "")).lower()
            title = str(node.get("title", "")).lower()
            category = str(node.get("category", "")).lower()
            if "path" in node and ("holding" in key or "holding" in title or "holding" in category):
                found.append({
                    "key": node.get("key"),
                    "title": node.get("title"),
                    "path": node.get("path"),
                    "params": node.get("params"),
                })
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(catalog_json)
    return found


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

    print("chiamo datasets_catalog: /datasets/ (non loggato per intero, solo le voci holding)")
    catalog_res = call("/datasets/")
    holding_entries = _find_holding_datasets(catalog_res.get("json"))
    results["datasets_catalog"] = {
        "url": catalog_res.get("url"),
        "status": catalog_res.get("status"),
        "ok": catalog_res.get("ok"),
        "holding_entries_found": holding_entries,
    }
    print(f"trovate {len(holding_entries)} voci holding nel catalogo: {[e.get('key') for e in holding_entries]}")

    if holding_rssd and holding_entries:
        snapshot_entry = next((e for e in holding_entries if e.get("key") == "holding_company"), None)
        if snapshot_entry:
            tmpl = snapshot_entry.get("path") or ""
            real_path = tmpl.replace("{rssd_id}", str(holding_rssd))
            if real_path.startswith("/api/v1"):
                real_path = real_path.replace("/api/v1", "", 1)
            print(f"chiamo holding_company_snapshot: {real_path} (timeout 45s)")
            res = call(real_path, timeout=45)
            if not res.get("ok") and res.get("error"):
                print(f"primo tentativo fallito ({res.get('error')}), riprovo una volta...")
                res = call(real_path, timeout=45)
            results["holding_company_snapshot"] = res
    elif holding_rssd:
        print("nessuna voce holding nel catalogo — provo comunque /banks/ con l'RSSD della holding")
        results["banks_endpoint_on_holding_rssd"] = call(f"/banks/{holding_rssd}/", timeout=45)

    if holding_rssd:
        print(f"chiamo bank_snapshot_su_holding_rssd: /banks/{holding_rssd}/ (timeout 45s)")
        results["bank_snapshot_su_holding_rssd"] = call(f"/banks/{holding_rssd}/", timeout=45)

    hints = []
    for name, res in results.items():
        j = res.get("json") if isinstance(res, dict) else None
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
        "scope": "probe fase 3 — endpoint holding trovato dal catalogo, non indovinato",
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
