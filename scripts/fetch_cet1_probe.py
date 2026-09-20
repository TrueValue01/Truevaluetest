#!/usr/bin/env python3
"""
Sonda CET1 holding — fase 1 (probe, non pipeline).

Gira server-side (GitHub Actions), quindi niente CORS: qui possiamo davvero
vedere cosa risponde bankregreports, cosa che dal browser non abbiamo mai
visto (bloccato prima di partire).

Scope deliberatamente stretto: UN SOLO caso noto (JPM, RSSD 852218 —
l'unico documentato pubblicamente da bankregreports). Non indoviniamo RSSD
per le altre 12 banche finché non sappiamo come si presenta la risposta.

Filosofia del progetto: meglio "non so ancora" scritto chiaro che un numero
plausibile ma non verificato. Questo script non scrive MAI un cet1.json
finché il valore non è stato confrontato col ground truth a mano.

Output: cet1-probe-log.json nel repo — leggibile da telefono su GitHub,
senza dover aprire i log della Action.
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


def call(path: str) -> dict:
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


def main():
    if not API_KEY:
        print("ERRORE: secret BRR_API_KEY non impostato nel repo.", file=sys.stderr)
        sys.exit(1)

    results = {}
    for name, path in {**CONFIRMED_ENDPOINTS, **GUESS_ENDPOINTS}.items():
        print(f"→ chiamo {name}: {path}")
        results[name] = call(path)

    hints = []
    for name, res in results.items():
        j = res.get("json")
        if isinstance(j, dict):
            for k, v in _flatten(j):
                if "cet1" in k.lower() and isinstance(v, (int, float)):
                    close = abs(v - JPM_EXPECTED_HOLDING_CET1) <= 1.0
                    hints.append({
                        "endpoint": name, "field": k, "value": v,
                        "vicino_a_headline_atteso_14_8": close,
                    })

    log = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "probe fase 1 — solo JPM RSSD 852218, ground truth holding=14.8",
        "results": results,
        "cet1_field_hints": hints,
        "next_step": (
            "Leggi 'results' a mano: se un endpoint guess ha status 200 e "
            "un JSON con RSSD diverso da 852218 e/o un campo capital vicino "
            "a 14.8, quello è l'endpoint holding. Se tutti i guess falliscono "
            "(404), la risposta è probabilmente dentro 'bank_structure': "
            "cerca lì la catena/RSSD della holding e ripeti la sonda su quel "
            "RSSD con l'endpoint /banks/{rssd}/."
        ),
    }

    out_path = os.path.join(os.path.dirname(__file__), "..", "cet1-probe-log.json")
    with open(out_path, "w") as f:
        json.dump(log, f, indent=2, ensure_ascii=False)
    print(f"\nScritto {out_path}")
    print(json.dumps(hints, indent=2, ensure_ascii=False))


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


if __name__ == "__main__":
    main()
