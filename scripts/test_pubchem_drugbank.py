"""
Test de PubChem (con datos integrados de DrugBank).
Documentación: https://pubchem.ncbi.nlm.nih.gov/docs/pug-rest

Objetivo: extraer evidencia molecular para la dimensión mecanística del informe.
  - Dianas farmacológicas (proteínas/genes que el fármaco activa o inhibe)
  - Enzimas metabolizadoras (CYPs — conecta con PharmGKB)
  - Mecanismo de acción (texto narrativo para el LLM)

Además prueba búsquedas adicionales:
  1. Por fármaco solo
  2. Por RAM sola
  3. Por fármaco + RAM combinados
"""
import re
import requests

# ── PARÁMETROS ──────────────────────────────────────────────
FARMACO = "alprazolam"
RAM     = "transient ischemic attack"
# ────────────────────────────────────────────────────────────

SEP  = "=" * 65
BASE = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"


# ─────────────────────────────────────────────────────────────
# Helper
# ─────────────────────────────────────────────────────────────

def obtener_cid(farmaco: str) -> str | None:
    """Obtiene el CID de PubChem para un fármaco por nombre."""
    resp = requests.get(
        f"{BASE}/compound/name/{farmaco}/cids/JSON",
        timeout=10
    )
    if resp.ok:
        cids = resp.json().get("IdentifierList", {}).get("CID", [])
        if cids:
            print(f"[PubChem] '{farmaco}' -> CID={cids[0]}")
            return str(cids[0])
    print(f"[PubChem] WARNING '{farmaco}' no encontrado")
    return None


def obtener_secciones(cid: str, heading: str) -> list:
    """Descarga una sección de la ficha PUG-View y devuelve las subsecciones."""
    resp = requests.get(
        f"https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/data/compound/{cid}/JSON",
        params={"heading": heading},
        timeout=15
    )
    if resp.ok:
        return resp.json().get("Record", {}).get("Section", [])
    return []


def extraer_textos(secciones: list, subsecciones_objetivo: set, max_chars: int = 600) -> dict:
    """Recorre secciones y extrae textos de las subsecciones buscadas."""
    resultado = {}
    for sec in secciones:
        for subsec in sec.get("Section", []):
            heading = subsec.get("TOCHeading", "")
            if heading in subsecciones_objetivo:
                textos = []
                for item in subsec.get("Information", []):
                    for s in item.get("Value", {}).get("StringWithMarkup", []):
                        texto = s.get("String", "").strip()
                        if texto:
                            textos.append(texto[:max_chars])
                if textos:
                    resultado[heading] = textos
    return resultado


# ─────────────────────────────────────────────────────────────
# BLOQUE MECANÍSTICO — dianas, enzimas, mecanismo de acción
# ─────────────────────────────────────────────────────────────

def buscar_mecanistica(farmaco: str) -> dict:
    print(f"\n{SEP}")
    print(f"MECANÍSTICA — '{farmaco}'")
    print(SEP)

    cid = obtener_cid(farmaco)
    if not cid:
        return {}

    # Mecanismo de acción y farmacología
    secciones = obtener_secciones(cid, "Pharmacology and Biochemistry")
    datos_farmaco = extraer_textos(secciones, {
        "Mechanism of Action",
        "Pharmacology",
        "Metabolism",
    })

    # Dianas moleculares y enzimas
    secciones_drug = obtener_secciones(cid, "Drug and Medication Information")
    datos_dianas = extraer_textos(secciones_drug, {
        "Drug Targets",
        "Drug Enzymes",
        "Drug Transporters",
        "Drug Carriers",
    })

    resultado = {**datos_farmaco, **datos_dianas}

    for heading, textos in resultado.items():
        print(f"\n  [{heading}]")
        for t in textos[:2]:
            print(f"  {t[:400]}")

    return resultado


# ─────────────────────────────────────────────────────────────
# BÚSQUEDA 1 — Por fármaco solo
# ─────────────────────────────────────────────────────────────

def buscar_por_farmaco(farmaco: str):
    print(f"\n{SEP}")
    print(f"BÚSQUEDA 1 — Por fármaco: '{farmaco}'")
    print(SEP)

    cid = obtener_cid(farmaco)
    if not cid:
        return

    resp = requests.get(
        f"{BASE}/compound/cid/{cid}/property/MolecularFormula,MolecularWeight,IUPACName/JSON",
        timeout=10
    )
    if resp.ok:
        props = resp.json().get("PropertyTable", {}).get("Properties", [{}])[0]
        print(f"  Fórmula   : {props.get('MolecularFormula', 'N/A')}")
        print(f"  Peso mol. : {props.get('MolecularWeight', 'N/A')}")
        print(f"  IUPAC     : {props.get('IUPACName', 'N/A')}")


# ─────────────────────────────────────────────────────────────
# BÚSQUEDA 2 — Por RAM sola
# ─────────────────────────────────────────────────────────────

def buscar_por_ram(ram: str):
    print(f"\n{SEP}")
    print(f"BÚSQUEDA 2 — Por RAM: '{ram}'")
    print(SEP)

    # La RAM no es un compuesto → buscar en anotaciones clínicas de PubChem
    resp = requests.get(
        "https://pubchem.ncbi.nlm.nih.gov/sdq/sdqagent.cgi",
        params={
            "infmt":  "json",
            "outfmt": "json",
            "query":  f'{{"select":"*","collection":"clinicaltrials","where":{{"ands":[{{"*":"{ram}"}}]}},"limit":5}}'
        },
        timeout=10
    )
    if resp.ok:
        hits = resp.json().get("SDQOutputSet", [{}])[0].get("rows", [])
        print(f"  Ensayos clínicos relacionados con '{ram}': {len(hits)}")
        for h in hits[:3]:
            print(f"  • {h.get('title', '')[:150]}")
    else:
        print(f"  Status: {resp.status_code} — sin resultados directos para '{ram}'")
        print("  Nota: la RAM como término clínico no tiene CID en PubChem.")
        print("  Para relacionar RAM con vías biológicas usar Reactome o UMLS.")


# ─────────────────────────────────────────────────────────────
# BÚSQUEDA 3 — Fármaco + RAM: ¿aparece la RAM en toxicidad?
# ─────────────────────────────────────────────────────────────

def buscar_farmaco_ram(farmaco: str, ram: str):
    print(f"\n{SEP}")
    print(f"BÚSQUEDA 3 — Fármaco + RAM: '{farmaco}' + '{ram}'")
    print(SEP)

    cid = obtener_cid(farmaco)
    if not cid:
        return

    secciones = obtener_secciones(cid, "Toxicity")
    datos = extraer_textos(secciones, {
        "Adverse Effects",
        "Side Effects and Warnings",
        "Human Toxicity Excerpts",
    }, max_chars=800)

    encontrado = False
    for heading, textos in datos.items():
        for texto in textos:
            if ram.lower() in texto.lower():
                print(f"  ✅ '{ram}' encontrado en [{heading}]:")
                print(f"     {texto[:400]}")
                encontrado = True

    if not encontrado:
        print(f"  '{ram}' no aparece directamente en la sección de toxicidad.")
        print(f"  Secciones disponibles con datos: {list(datos.keys())}")
        # Mostrar igualmente los primeros textos de toxicidad
        for heading, textos in list(datos.items())[:1]:
            print(f"\n  [{heading}] (muestra):")
            for t in textos[:2]:
                print(f"  {t[:300]}")


_PATRON_GEN = re.compile(r'Gene\s+Name[:\s]+([A-Z][A-Z0-9]+)', re.IGNORECASE)


def _extraer_genes(textos: list[str]) -> list[str]:
    """Extrae símbolos de genes de textos de Drug Targets/Enzymes (formato DrugBank)."""
    genes = []
    for texto in textos:
        for m in _PATRON_GEN.finditer(texto):
            g = m.group(1).upper()
            if g not in genes:
                genes.append(g)
    return genes


def obtener_datos_pubchem(farmaco: str) -> dict:
    """
    Función importable. Devuelve mecanismo de acción, metabolismo y genes diana para el LLM.
    """
    cid = obtener_cid(farmaco)
    if not cid:
        return {"disponible": False, "mensaje": f"'{farmaco}' no encontrado en PubChem"}

    secciones = obtener_secciones(cid, "Pharmacology and Biochemistry")
    datos = extraer_textos(secciones, {
        "Mechanism of Action",
        "Pharmacology",
        "Metabolism",
    }, max_chars=1000)

    secciones_drug = obtener_secciones(cid, "Drug and Medication Information")
    datos_dianas = extraer_textos(secciones_drug, {
        "Drug Targets",
        "Drug Enzymes",
    }, max_chars=1000)

    dianas_genes = _extraer_genes(
        [t for textos in datos_dianas.values() for t in textos]
    )

    return {
        "disponible": bool(datos),
        "cid": cid,
        "mecanismo_accion": " ".join(datos.get("Mechanism of Action", [])),
        "farmacologia": " ".join(datos.get("Pharmacology", [])),
        "metabolismo": " ".join(datos.get("Metabolism", [])),
        "dianas_genes": dianas_genes,
    }


# ── EJECUCIÓN ───────────────────────────────────────────────
if __name__ == "__main__":
    print(f"Fármaco : {FARMACO}")
    print(f"RAM     : {RAM}\n")

    buscar_mecanistica(FARMACO)
    buscar_por_farmaco(FARMACO)
    buscar_por_ram(RAM)
    buscar_farmaco_ram(FARMACO, RAM)
