"""
Test de las 4 búsquedas sin llamadas a AWS.
Muestra los resultados RAW de: CIMA, PubMed, FDA, Reactome.
La traducción al inglés de la reacción se obtiene via UMLS con el código MedDRA.
"""
import json
import requests
import pdfplumber
import io
import re

# ── PARÁMETROS — cambiar aquí ────────────────────────────────
FARMACO      = "Alprazolam"
REACCION     = "Ataque isquémico transitorio"
COD_MEDDRA   = "10044390"   # código MedDRA PT de la reacción
UMLS_API_KEY = "edef02c2-c71e-4208-8031-53ac10bc8c0f"
# ────────────────────────────────────────────────────────────


def traducir_meddra_a_ingles(cod_meddra: str) -> str | None:
    """Obtiene el PT MedDRA en inglés desde UMLS usando el código MedDRA."""
    try:
        resp = requests.get(
            f"https://uts-ws.nlm.nih.gov/rest/content/current/source/MDR/{cod_meddra}",
            params={"apiKey": UMLS_API_KEY},
            timeout=10
        )
        if resp.ok:
            nombre = resp.json()["result"]["name"]
            print(f"[UMLS] Código {cod_meddra} → '{nombre}'")
            return nombre
        print(f"[UMLS] WARNING código {cod_meddra} no encontrado ({resp.status_code})")
        return None
    except Exception as e:
        print(f"[UMLS] WARNING error: {e}")
        return None


SEP = "=" * 65


# ─────────────────────────────────────────────────────────────
# 1. CIMA (AEMPS)
# ─────────────────────────────────────────────────────────────

def test_cima(farmaco: str):
    print(f"\n{SEP}")
    print(f"1. CIMA — {farmaco}")
    print(SEP)

    url = "https://cima.aemps.es/cima/rest/medicamentos"
    params = {"nombre": farmaco, "practiv1": farmaco, "comerc": "1", "autorizados": "1"}
    resp = requests.get(url, params=params, timeout=10)
    print(f"Status: {resp.status_code} | URL: {resp.url}")

    data = resp.json()
    total = data.get("totalFilas", 0)
    print(f"Resultados encontrados: {total}")

    if total == 0:
        print("Sin resultados con nombre+practiv1, probando solo practiv1...")
        params = {"practiv1": farmaco, "comerc": "1", "autorizados": "1"}
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        total = data.get("totalFilas", 0)
        print(f"Resultados (solo practiv1): {total}")

    resultados = data.get("resultados", [])
    print(f"\nPrimeros {min(5, len(resultados))} resultados:")
    for i, r in enumerate(resultados[:5]):
        docs = r.get("docs", [])
        ft_url = docs[0].get("url", "sin url") if docs else "sin url"
        nr = r.get("nregistro", "")
        cima_web = f"https://cima.aemps.es/cima/publico/detalle.html?nregistro={nr}" if nr else "N/A"
        print(f"  [{i}] {r.get('nombre')} | dosis: {r.get('dosis', '')}")
        print(f"       Ficha técnica PDF : {ft_url}")
        print(f"       Página CIMA       : {cima_web}")

    if resultados:
        med = resultados[0]
        docs = med.get("docs", [])
        if docs:
            ft_url = docs[0]["url"]
            print(f"\nDescargando ficha técnica de: {ft_url}")
            try:
                r2 = requests.get(ft_url, timeout=30)
                with pdfplumber.open(io.BytesIO(r2.content)) as pdf:
                    texto = " ".join(p.extract_text() or "" for p in pdf.pages)
                texto = re.sub(r"\r?\n+", " ", texto)
                texto = re.sub(r"\s{2,}", " ", texto).strip()
                print(f"PDF extraído: {len(texto)} caracteres")
                print(f"\nPrimeros 1500 caracteres:\n{texto[:1500]}")
            except Exception as e:
                print(f"Error descargando PDF: {e}")


# ─────────────────────────────────────────────────────────────
# 2. PubMed (NCBI E-utilities)
# ─────────────────────────────────────────────────────────────

def test_pubmed(farmaco: str, reaccion_en: str):
    print(f"\n{SEP}")
    print(f"2. PubMed — {farmaco} + {reaccion_en}")
    print(SEP)

    term = f'{farmaco} AND {reaccion_en} AND ("adverse reaction" OR "adverse event" OR "toxicity")'
    print(f"Término de búsqueda: {term}\n")

    # esearch
    resp = requests.get(
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
        params={"db": "pubmed", "term": term, "retmode": "json", "retmax": "3"},
        timeout=10
    )
    print(f"esearch status: {resp.status_code}")
    result = resp.json().get("esearchresult", {})
    ids = result.get("idlist", [])
    total = result.get("count", 0)
    term_encoded = term.replace(" ", "+")
    busqueda_web = f"https://pubmed.ncbi.nlm.nih.gov/?term={term_encoded}"
    print(f"Total artículos encontrados: {total}")
    print(f"Ver en PubMed web: {busqueda_web}")
    print(f"IDs descargados (max 3): {ids}")
    for pmid in ids:
        print(f"  https://pubmed.ncbi.nlm.nih.gov/{pmid}/")

    if not ids:
        print("Sin resultados.")
        return

    # efetch
    resp2 = requests.get(
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
        params={"db": "pubmed", "id": ",".join(ids), "rettype": "abstract", "retmode": "text"},
        timeout=15
    )
    print(f"\nefetch status: {resp2.status_code} | {len(resp2.text)} caracteres")
    print(f"\n--- ABSTRACTS ---\n{resp2.text[:3000]}")
    if len(resp2.text) > 3000:
        print(f"... ({len(resp2.text) - 3000} caracteres más)")


# ─────────────────────────────────────────────────────────────
# 3. OpenFDA (FAERS)
# ─────────────────────────────────────────────────────────────

def test_fda(farmaco: str, reaccion_en: str):
    print(f"\n{SEP}")
    print(f"3. OpenFDA FAERS — {farmaco} + {reaccion_en}")
    print(SEP)

    url = "https://api.fda.gov/drug/event.json"
    params = {
        "search": f'patient.drug.medicinalproduct:"{farmaco}" AND patient.reaction.reactionmeddrapt:"{reaccion_en}"',
        "limit": "3"
    }
    resp = requests.get(url, params=params, timeout=15)
    print(f"Status: {resp.status_code} | URL: {resp.url}")

    # Enlace a FAERS web
    faers_web = f"https://www.fda.gov/drugs/questions-and-answers-fdas-adverse-event-reporting-system-faers/fda-adverse-event-reporting-system-faers-public-dashboard"
    print(f"FAERS dashboard: {faers_web}")

    if resp.status_code == 404:
        print("Sin resultados (404) — combinación no encontrada en FAERS")
        print("Tip: prueba variantes (Transient Ischaemic Attack, etc.)")
    elif resp.ok:
        data = resp.json()
        total = data["meta"]["results"]["total"]
        print(f"\nTotal casos en FAERS: {total}")
        for i, caso in enumerate(data.get("results", [])[:3]):
            print(f"\n--- Caso {i+1} ---")
            print(f"  País: {caso.get('occurcountry', 'N/A')}")
            reacciones = [r["reactionmeddrapt"] for r in caso.get("patient", {}).get("reaction", [])]
            print(f"  Reacciones: {reacciones}")
            farmacos = [d.get("medicinalproduct") for d in caso.get("patient", {}).get("drug", [])]
            print(f"  Fármacos: {farmacos}")
    else:
        print(f"Error: {resp.status_code} — {resp.text[:300]}")

    # Top 10 reacciones del fármaco
    print(f"\n--- Top 10 reacciones para '{farmaco}' en FAERS ---")
    resp2 = requests.get(url, params={
        "search": f'patient.drug.medicinalproduct:"{farmaco}"',
        "count": "patient.reaction.reactionmeddrapt.exact",
        "limit": "10"
    }, timeout=15)
    if resp2.ok:
        for r in resp2.json().get("results", []):
            print(f"  {r['term']}: {r['count']}")
    else:
        print(f"  Error: {resp2.status_code}")


# ─────────────────────────────────────────────────────────────
# 4. Reactome
# ─────────────────────────────────────────────────────────────

def test_reactome(farmaco: str, reaccion_en: str):
    print(f"\n{SEP}")
    print(f"4. Reactome — {farmaco} + {reaccion_en}")
    print(SEP)

    resp = requests.get(
        "https://reactome.org/ContentService/search/query",
        params={"query": f"{farmaco} {reaccion_en}", "species": "Homo sapiens", "cluster": "true"},
        headers={"Accept": "application/json"},
        timeout=10
    )
    print(f"Status: {resp.status_code} | URL: {resp.url}")

    if not resp.ok:
        print(f"Error: {resp.text[:300]}")
        return

    data = resp.json()
    print(f"Respuesta total: {len(json.dumps(data))} caracteres")

    groups = data.get("results", [])
    print(f"Grupos de resultados: {len(groups)}")
    for g in groups[:4]:
        print(f"\n  Tipo: {g.get('typeName')} ({len(g.get('entries', []))} entradas)")
        for entry in g.get("entries", [])[:3]:
            st_id = entry.get("stId", "")
            nombre = entry.get("name")
            reactome_url = f"https://reactome.org/content/detail/{st_id}" if st_id else "N/A"
            print(f"    - [{st_id}] {nombre}")
            print(f"      {reactome_url}")
            if entry.get("summation"):
                resumen = entry["summation"][0] if isinstance(entry["summation"], list) else entry["summation"]
                print(f"      {str(resumen)[:200]}")


# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Traducir la reacción al inglés via UMLS con el código MedDRA
    reaccion_en = traducir_meddra_a_ingles(COD_MEDDRA) or REACCION

    print(f"Fármaco : {FARMACO}")
    print(f"Reacción: {REACCION} → '{reaccion_en}' (MedDRA {COD_MEDDRA})")

    test_cima(FARMACO)
    test_pubmed(FARMACO, reaccion_en)
    test_fda(FARMACO, reaccion_en)
    test_reactome(FARMACO, reaccion_en)

    print(f"\n{SEP}")
    print("Búsquedas completadas (sin llamadas a AWS).")
    print(SEP)
