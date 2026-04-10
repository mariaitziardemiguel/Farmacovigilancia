"""
Prueba de búsqueda en OpenFDA FAERS.
Entrada: fármaco en castellano + código MedDRA.
Traduce automáticamente a inglés via terminology.py antes de consultar FAERS.

Uso: python scripts/test_fda.py <farmaco_es> <cod_meddra>
Ejemplo: python scripts/test_fda.py amiodarona 10037383
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "pipeline"))

import requests
from terminology import meddra_code_to_name, meddra_pt_to_llts, rxnorm_to_inn

BASE_URL = "=" * 60


def consultar_faers(farmaco: str, reaccion: str) -> dict:
    query = (
        f'patient.drug.medicinalproduct:"{farmaco}" AND '
        f'patient.reaction.reactionmeddrapt:"{reaccion}"'
    )
    url = f"https://api.fda.gov/drug/event.json?search={requests.utils.quote(query)}&limit=10"
    try:
        resp = requests.get(
            "https://api.fda.gov/drug/event.json",
            params={"search": query, "limit": "3"},
            timeout=15,
        )
        if resp.ok:
            total = resp.json()["meta"]["results"]["total"]
            return {"total": total, "query": query, "url": url, "ok": True}
        return {"total": 0, "query": query, "url": url, "ok": False, "status": resp.status_code}
    except Exception as e:
        return {"total": 0, "query": query, "url": url, "ok": False, "error": str(e)}


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Uso: python scripts/test_fda.py <farmaco_es> <cod_meddra>")
        print("Ejemplo: python scripts/test_fda.py amiodarona 10037383")
        sys.exit(1)

    farmaco_es = sys.argv[1]
    cod_meddra = sys.argv[2]
    SEP = "=" * 60

    print(f"\n{SEP}")
    print("ENTRADA")
    print(SEP)
    print(f"  Fármaco (ES)  : {farmaco_es}")
    print(f"  Código MedDRA : {cod_meddra}")

    print(f"\n{SEP}")
    print("TRADUCCIÓN")
    print(SEP)
    inn_en, rxcui   = rxnorm_to_inn(farmaco_es)
    farmaco_en      = inn_en or farmaco_es
    reaccion_en     = meddra_code_to_name(cod_meddra, lang="en")
    reaccion_es     = meddra_code_to_name(cod_meddra, lang="es")
    print(f"  Fármaco (EN)  : {farmaco_en}  (rxcui={rxcui})")
    print(f"  RAM (EN)      : {reaccion_en}")
    print(f"  RAM (ES)      : {reaccion_es}")

    print(f"\n{SEP}")
    print("CONSULTA FAERS — fármaco EN + reacción EN")
    print(SEP)

    # PT (Preferred Term)
    print(f"\n  [PT] {reaccion_en}")
    res = consultar_faers(farmaco_en, reaccion_en)
    if res["ok"]:
        print(f"       Casos : {res['total']:,}")
        print(f"       URL   : {res['url']}")
    else:
        print(f"       Error : {res.get('status', res.get('error'))}")

    # LLTs (Lowest Level Terms / sinónimos)
    llts = meddra_pt_to_llts(cod_meddra)
    if llts:
        print(f"\n  LLTs encontrados: {len(llts)}")
        for llt in llts:
            if llt == reaccion_en:
                continue
            print(f"\n  [LLT] {llt}")
            res_llt = consultar_faers(farmaco_en, llt)
            if res_llt["ok"]:
                print(f"        Casos : {res_llt['total']:,}")
                print(f"        URL   : {res_llt['url']}")
            else:
                print(f"        Error : {res_llt.get('status', res_llt.get('error'))}")
    else:
        print("\n  (No se encontraron LLTs para este PT)")
