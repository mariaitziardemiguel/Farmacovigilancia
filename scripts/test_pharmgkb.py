"""
Test de PharmGKB + CPIC para biomarcadores farmacogenéticos.
Produce un contexto compacto y consolidado por gen, listo para el LLM.

APIs:
  - https://api.pharmgkb.org/v1/
  - https://api.cpicpgx.org/v1/
"""
import requests
import json
from collections import Counter, defaultdict

# ── PARÁMETROS — cambiar aquí ────────────────────────────────
FARMACO = "ALPRAZOLAM"   # usar nombre en inglés (INN)
# ────────────────────────────────────────────────────────────

NIVELES_ALTOS = {"1A", "1B", "2A", "2B"}
ORDEN_NIVEL   = {"1A": 0, "1B": 1, "2A": 2, "2B": 3}

SEP = "=" * 65


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def obtener_rxcui(farmaco: str) -> tuple[str, str] | tuple[None, None]:
    try:
        resp = requests.get(
            "https://rxnav.nlm.nih.gov/REST/rxcui.json",
            params={"name": farmaco, "search": 2}, timeout=10
        )
        rxcui_list = resp.json().get("idGroup", {}).get("rxnormId", [])
        if not rxcui_list:
            print(f"[RxNorm] WARNING '{farmaco}' no encontrado")
            return None, None
        rxcui = rxcui_list[0]
        r2 = requests.get(
            f"https://rxnav.nlm.nih.gov/REST/rxcui/{rxcui}/properties.json", timeout=10
        )
        inn = r2.json().get("properties", {}).get("name", farmaco)
        print(f"[RxNorm] '{farmaco}' -> INN='{inn}' | rxcui={rxcui}")
        return inn, rxcui
    except Exception as e:
        print(f"[RxNorm] ERROR: {e}")
        return None, None


def obtener_drug_id(farmaco_inn: str) -> str | None:
    try:
        resp = requests.get(
            "https://api.pharmgkb.org/v1/data/drug",
            params={"name": farmaco_inn, "view": "min"}, timeout=10
        )
        data = resp.json().get("data", [])
        if data:
            drug_id = data[0].get("id")
            print(f"[PharmGKB] '{farmaco_inn}' -> drug_id={drug_id}")
            return drug_id
        print(f"[PharmGKB] WARNING '{farmaco_inn}' no encontrado")
        return None
    except Exception as e:
        print(f"[PharmGKB] ERROR: {e}")
        return None


def get_nivel(ann: dict) -> str:
    return ann.get("levelOfEvidence", {}).get("term", "")


# ─────────────────────────────────────────────────────────────
# Extracción intermedia (interna — no va al LLM tal cual)
# ─────────────────────────────────────────────────────────────

def extraer_datos_anotacion(ann: dict) -> dict:
    nivel = get_nivel(ann)
    loc   = ann.get("location", {})

    gen_symbol = loc["genes"][0].get("symbol", "") if loc.get("genes") else ""
    variante   = loc.get("rsid") or loc.get("displayName", "")

    alelo_fenotipos = [
        {"alelo": ap["allele"], "fenotipo": ap["phenotype"]}
        for ap in ann.get("allelePhenotypes", [])
        if not ap.get("limitedEvidence")
    ]

    enfermedades = [d["name"] for d in ann.get("relatedDiseases", [])]

    guias = [
        {
            "nombre": g["name"],
            "url": f"https://www.clinpgx.org/guidelineAnnotation/{g.get('accessionId') or g.get('id')}"
        }
        for g in ann.get("relatedGuidelines", [])
    ]

    etiquetas = [
        {
            "nombre": lb["name"],
            "url": f"https://www.pharmgkb.org/labelAnnotation/{lb.get('accessionId') or lb.get('id')}"
        }
        for lb in ann.get("relatedLabels", [])
    ]

    return {
        "nivel_evidencia": nivel,
        "gen": gen_symbol,
        "variante": variante,
        "tipos": ann.get("types", []),
        "alelo_fenotipos": alelo_fenotipos,
        "enfermedades": enfermedades,
        "guias": guias,
        "etiquetas": etiquetas,
        "tiene_guias_cpic_dpwg": bool(guias),
        "tiene_etiquetas_fda_ema": bool(etiquetas),
    }


# ─────────────────────────────────────────────────────────────
# Consolidación por gen → una entrada compacta por gen
# ─────────────────────────────────────────────────────────────

def consolidar_genes(anotaciones: list[dict], cpic_idx: dict) -> tuple[list[dict], list[dict]]:
    """
    Agrupa todas las anotaciones del mismo gen y produce:
      - lista para el LLM (sin URLs)
      - lista de referencias con URLs (solo para impresión en terminal)
    """
    por_gen = defaultdict(list)
    for ann in anotaciones:
        por_gen[ann["gen"] or "?"].append(ann)

    llm_entries = []
    ref_entries = []

    for gen, anns in por_gen.items():
        # Nivel máximo: prioriza 1A/1B/2A/2B; si no hay, coge el nivel real (ej: "3")
        niveles_validados = [a["nivel_evidencia"] for a in anns if a["nivel_evidencia"] in ORDEN_NIVEL]
        if niveles_validados:
            nivel_max = min(niveles_validados, key=lambda x: ORDEN_NIVEL[x])
        else:
            nivel_max = next((a["nivel_evidencia"] for a in anns if a["nivel_evidencia"]), "desconocido")
        tipos       = sorted({t for a in anns for t in a["tipos"]})
        tiene_guias = any(a["tiene_guias_cpic_dpwg"] for a in anns)
        tiene_etiq  = any(a["tiene_etiquetas_fda_ema"] for a in anns)

        # Efecto esperado: agrupado por categoría funcional del alelo
        # Detecta la función del alelo desde el texto y agrupa consecuencias únicas
        import re as _re
        grupos: dict[str, set] = {"normal": set(), "decreased": set(), "no_function": set(), "other": set()}
        PATRON_OUTCOME = _re.compile(
            r"may (have|require) ([^.]{10,120})\.", _re.IGNORECASE
        )
        for ann in anns:
            for fp in ann["alelo_fenotipos"]:
                texto = fp["fenotipo"]
                if "normal function" in texto.lower():
                    clave = "normal"
                elif "no function" in texto.lower():
                    clave = "no_function"
                elif "decreased function" in texto.lower():
                    clave = "decreased"
                else:
                    clave = "other"
                m = PATRON_OUTCOME.search(texto)
                if m:
                    grupos[clave].add(m.group(0).strip())

        efecto_lineas = []
        ETIQUETAS = {"normal": "Función normal", "decreased": "Función reducida", "no_function": "Sin función", "other": "Otro"}
        for clave, etiqueta in ETIQUETAS.items():
            if grupos[clave]:
                efecto_lineas.append(f"{etiqueta}: {' / '.join(sorted(grupos[clave]))}")

        # Enfermedades únicas vinculadas
        enfermedades = list(dict.fromkeys(e for a in anns for e in a["enfermedades"]))

        # URLs únicas por gen (solo para terminal)
        urls_guias     = {g["url"]: g["nombre"] for a in anns for g in a["guias"]}
        urls_etiquetas = {e["url"]: e["nombre"] for a in anns for e in a["etiquetas"]}

        cpic = cpic_idx.get(gen, {})

        llm_entries.append({
            "gen": gen,
            "nivel_max_evidencia": nivel_max,
            "accionable_cpic": cpic.get("accionable", False),
            "nivel_cpic": cpic.get("nivel_cpic", ""),
            "tipos_relacion": tipos,
            "tiene_guias_cpic_dpwg": tiene_guias,
            "tiene_etiquetas_fda_ema": tiene_etiq,
            "enfermedades_vinculadas": enfermedades,
            "efecto_esperado": " | ".join(efecto_lineas),
        })

        ref_entries.append({
            "gen": gen,
            "nivel_max_evidencia": nivel_max,
            "guias_cpic_dpwg": [{"nombre": n, "url": u} for u, n in urls_guias.items()],
            "etiquetas_fda_ema": [{"nombre": n, "url": u} for u, n in urls_etiquetas.items()],
        })

    llm_entries.sort(key=lambda x: ORDEN_NIVEL.get(x["nivel_max_evidencia"], 99))
    ref_entries.sort(key=lambda x: ORDEN_NIVEL.get(x["nivel_max_evidencia"], 99))
    return llm_entries, ref_entries


def obtener_datos_pharmgkb(farmaco: str) -> dict:
    """
    Función importable. Devuelve contexto farmacogenético para el LLM.
    """
    inn, rxcui = obtener_rxcui(farmaco)
    farmaco_inn = inn or farmaco
    drug_id = obtener_drug_id(farmaco_inn)

    anotaciones_raw = []
    if drug_id:
        resp = requests.get(
            "https://api.pharmgkb.org/v1/data/clinicalAnnotation",
            params={"relatedChemicals.accessionId": drug_id, "view": "base"},
            timeout=15
        )
        todas = resp.json().get("data", []) if resp.ok else []
        altas = [a for a in todas if get_nivel(a) in NIVELES_ALTOS]
        if not altas and todas:
            altas = todas
        anotaciones_raw = [extraer_datos_anotacion(a) for a in altas]

    cpic_idx = {}
    if rxcui:
        resp = requests.get("https://api.cpicpgx.org/v1/pair", timeout=15)
        if resp.ok:
            pares = [p for p in resp.json() if p.get("drugid") == f"RxNorm:{rxcui}" and not p.get("removed")]
            cpic_idx = {
                p["genesymbol"]: {
                    "nivel_cpic": p.get("cpiclevel", ""),
                    "accionable": p.get("usedforrecommendation", False),
                }
                for p in pares
            }

    ann_fuertes     = [a for a in anotaciones_raw if a["nivel_evidencia"] in {"1A", "1B"}]
    ann_secundarias = [a for a in anotaciones_raw if a["nivel_evidencia"] in {"2A", "2B"}]
    ann_baja        = [a for a in anotaciones_raw if a["nivel_evidencia"] not in {"1A", "1B", "2A", "2B"}]

    genes_fuertes,     _ = consolidar_genes(ann_fuertes, cpic_idx)
    genes_secundarios, _ = consolidar_genes(ann_secundarias, cpic_idx)
    genes_baja,        _ = consolidar_genes(ann_baja, cpic_idx)

    return {
        "farmaco": farmaco_inn,
        "farmacogenetica": {
            "evidencia_fuerte_1A_1B": genes_fuertes,
            "evidencia_secundaria_2A_2B": genes_secundarios,
            "evidencia_baja_referencia": {
                "nota": "Sin biomarcadores validados (1A-2B). Evidencia nivel 3 o inferior (exploratoria, insuficiente para recomendación clínica).",
                "genes": genes_baja,
            } if genes_baja else None,
        }
    }


# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"Fármaco: {FARMACO}\n")

    inn, rxcui = obtener_rxcui(FARMACO)
    farmaco_inn = inn or FARMACO
    drug_id = obtener_drug_id(farmaco_inn)

    # ── PharmGKB Clinical Annotations ────────────────────────
    anotaciones_raw = []

    if drug_id:
        resp = requests.get(
            "https://api.pharmgkb.org/v1/data/clinicalAnnotation",
            params={"relatedChemicals.accessionId": drug_id, "view": "base"},
            timeout=15
        )
        todas = resp.json().get("data", []) if resp.ok else []

        dist = Counter(get_nivel(a) for a in todas)
        print(f"[PharmGKB] {len(todas)} anotaciones totales | distribución: {dict(sorted(dist.items()))}")

        altas = [a for a in todas if get_nivel(a) in NIVELES_ALTOS]
        print(f"[PharmGKB] {len(altas)} anotaciones 1A/1B/2A/2B")

        # Fallback: si no hay 1A/1B/2A/2B, incluir todas las disponibles
        if not altas and todas:
            print(f"[PharmGKB] Sin evidencia 1A-2B — usando todas las anotaciones disponibles como referencia (nivel bajo)\n")
            altas = todas
        else:
            print()

        anotaciones_raw = [extraer_datos_anotacion(a) for a in altas]

    # ── CPIC pares gen-fármaco ────────────────────────────────
    cpic_idx = {}

    if rxcui:
        resp = requests.get("https://api.cpicpgx.org/v1/pair", timeout=15)
        if resp.ok:
            pares = [
                p for p in resp.json()
                if p.get("drugid") == f"RxNorm:{rxcui}" and not p.get("removed")
            ]
            cpic_idx = {
                p["genesymbol"]: {
                    "nivel_cpic": p.get("cpiclevel", ""),
                    "accionable": p.get("usedforrecommendation", False),
                }
                for p in pares
            }
            print(f"[CPIC] {len(cpic_idx)} pares gen-fármaco encontrados")

    # ── Consolidar por gen y separar por fuerza de evidencia ─
    ann_fuertes      = [a for a in anotaciones_raw if a["nivel_evidencia"] in {"1A", "1B"}]
    ann_secundarias  = [a for a in anotaciones_raw if a["nivel_evidencia"] in {"2A", "2B"}]
    ann_baja         = [a for a in anotaciones_raw if a["nivel_evidencia"] not in {"1A", "1B", "2A", "2B"}]

    genes_fuertes,     refs_fuertes     = consolidar_genes(ann_fuertes, cpic_idx)
    genes_secundarios, refs_secundarios = consolidar_genes(ann_secundarias, cpic_idx)
    genes_baja,        refs_baja        = consolidar_genes(ann_baja, cpic_idx)

    # ── Referencias con URLs (solo terminal, no van al LLM) ──
    todas_refs = refs_fuertes + refs_secundarios + refs_baja
    if todas_refs:
        print(f"\n{SEP}")
        print("REFERENCIAS (URLs — solo para consulta, no se pasan al LLM)")
        print(SEP)
        for ref in todas_refs:
            print(f"\n  Gen: {ref['gen']}  [{ref['nivel_max_evidencia']}]")
            for g in ref["guias_cpic_dpwg"]:
                print(f"    [Guía]     {g['nombre']}")
                print(f"               {g['url']}")
            for e in ref["etiquetas_fda_ema"]:
                print(f"    [Etiqueta] {e['nombre']}")
                print(f"               {e['url']}")

    # ── Contexto final para el LLM ───────────────────────────
    contexto_llm = {
        "farmaco": farmaco_inn,
        "farmacogenetica": {
            "evidencia_fuerte_1A_1B": genes_fuertes,
            "evidencia_secundaria_2A_2B": genes_secundarios,
            "evidencia_baja_referencia": {
                "nota": "Sin biomarcadores validados (1A-2B). Los siguientes genes tienen únicamente evidencia de nivel 3 o inferior (exploratoria, insuficiente para recomendación clínica).",
                "genes": genes_baja,
            } if genes_baja else None,
        }
    }

    print(f"\n{SEP}")
    print("CONTEXTO PARA EL LLM (prototipo sección 5 del informe)")
    print(SEP)
    print(json.dumps(contexto_llm, indent=2, ensure_ascii=False))

    print(f"\n{SEP}")
    print(f"Resumen: {len(genes_fuertes)} genes evidencia fuerte | "
          f"{len(genes_secundarios)} genes evidencia secundaria | "
          f"{len(genes_baja)} genes evidencia baja (fallback) | "
          f"{len(cpic_idx)} pares CPIC")
    print(SEP)
