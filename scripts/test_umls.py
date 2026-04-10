"""
Test de busqueda en UMLS (NLM)
Documentacion: https://documentation.uts.nlm.nih.gov/rest/home.html
Cuenta: mariaitziar / mariiaitziar@gmail.com
"""
import requests

# ── CONFIGURACION ───────────────────────────────────────────
UMLS_API_KEY = "edef02c2-c71e-4208-8031-53ac10bc8c0f"
# ───────────────────────────────────────────────────────────

# ── OPCION A: codigo MedDRA → nombre en ingles ──────────────
COD_MEDDRA = "10044375"   # Diarrea

print(f"=== Codigo MedDRA {COD_MEDDRA} ===")
resp = requests.get(
    f"https://uts-ws.nlm.nih.gov/rest/content/current/source/MDR/{COD_MEDDRA}",
    params={"apiKey": UMLS_API_KEY},
    timeout=10
)
print(f"Status: {resp.status_code}")
if resp.ok:
    result = resp.json()["result"]
    print(f"Nombre EN: {result['name']}")
    print(f"CUI: {result.get('ui', 'N/A')}")
print()

# ── OPCION B: buscar por nombre para encontrar el CUI ───────
NOMBRE = "Mucositis"

print(f"=== Busqueda por nombre: '{NOMBRE}' -> codigo MedDRA ===")
resp2 = requests.get(
    "https://uts-ws.nlm.nih.gov/rest/search/current",
    params={"apiKey": UMLS_API_KEY, "string": NOMBRE, "sabs": "MDR", "searchType": "exact"},
    timeout=10
)
if resp2.ok:
    resultados = resp2.json().get("result", {}).get("results", [])
    if not resultados:
        print("  Sin resultados exactos. Prueba con searchType: 'words' o 'approximate'")
    for r in resultados[:5]:
        cui = r["ui"]
        # Obtener el codigo MedDRA a partir del CUI
        resp_atoms = requests.get(
            f"https://uts-ws.nlm.nih.gov/rest/content/current/CUI/{cui}/atoms",
            params={"apiKey": UMLS_API_KEY, "sabs": "MDR"},
            timeout=10
        )
        codigos_mdr = []
        if resp_atoms.ok:
            for atom in resp_atoms.json().get("result", []):
                cod = atom.get("code", "").split("/")[-1]
                codigos_mdr.append(f"{cod} ({atom['name']})")
        print(f"  CUI: {cui} | Nombre: {r['name']}")
        print(f"  Codigos MedDRA: {', '.join(codigos_mdr) if codigos_mdr else 'no encontrado'}")
print()

# ── OPCION C: ver todos los sinonimos de un CUI ─────────────
CUI = "C0011991"  # Diarrhoea

print(f"=== Sinonimos MedDRA para CUI {CUI} ===")
resp3 = requests.get(
    f"https://uts-ws.nlm.nih.gov/rest/content/current/CUI/{CUI}/atoms",
    params={"apiKey": UMLS_API_KEY, "sabs": "MDR"},
    timeout=10
)
if resp3.ok:
    for atom in resp3.json().get("result", [])[:10]:
        code = atom.get("code", "").split("/")[-1]
        print(f"  Codigo: {code} | Nombre: {atom['name']}")
