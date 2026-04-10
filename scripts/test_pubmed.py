"""
Búsqueda clínica en PubMed (NCBI E-utilities) para la Dimensión Clínica (sección 1.2).
Realiza cuatro búsquedas temáticas y devuelve los abstracts consolidados para el LLM.

Documentación: https://www.ncbi.nlm.nih.gov/books/NBK25499/
"""
import requests
import json
import xml.etree.ElementTree as ET

# ── PARÁMETROS ───────────────────────────────────────────────
CASO = {
    "farmaco":    "amiodarone",
    "ram":        "Pulmonary toxicity",
    "cod_meddra": "10037383",
}
UMLS_API_KEY = "edef02c2-c71e-4208-8031-53ac10bc8c0f"
# ────────────────────────────────────────────────────────────

BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


# ─────────────────────────────────────────────────────────────
# Normalización: fármaco → INN y RAM → término inglés via UMLS
# ─────────────────────────────────────────────────────────────

def normalizar_farmaco(farmaco: str) -> str:
    """Fármaco → INN en inglés via RxNorm."""
    try:
        resp = requests.get(
            "https://rxnav.nlm.nih.gov/REST/rxcui.json",
            params={"name": farmaco, "search": 2}, timeout=10
        )
        rxcui_list = resp.json().get("idGroup", {}).get("rxnormId", [])
        if rxcui_list:
            r2 = requests.get(
                f"https://rxnav.nlm.nih.gov/REST/rxcui/{rxcui_list[0]}/properties.json",
                timeout=10
            )
            inn = r2.json().get("properties", {}).get("name", "")
            if inn:
                print(f"[RxNorm] '{farmaco}' -> INN='{inn}'")
                return inn
    except Exception as e:
        print(f"[RxNorm] ERROR: {e}")
    print(f"[RxNorm] WARNING '{farmaco}' no encontrado, usando nombre original")
    return farmaco


def normalizar_ram(cod_meddra: str, ram_fallback: str) -> str:
    """Código MedDRA → término PT en inglés via UMLS."""
    try:
        resp = requests.get(
            f"https://uts-ws.nlm.nih.gov/rest/content/current/source/MDR/{cod_meddra}",
            params={"apiKey": UMLS_API_KEY}, timeout=10
        )
        if resp.ok:
            nombre = resp.json()["result"]["name"]
            print(f"[UMLS] código {cod_meddra} -> '{nombre}'")
            return nombre
        print(f"[UMLS] WARNING código {cod_meddra} no encontrado ({resp.status_code})")
    except Exception as e:
        print(f"[UMLS] ERROR: {e}")
    print(f"[UMLS] Usando nombre original: '{ram_fallback}'")
    return ram_fallback



def _esearch(term: str, n: int, show_translation: bool = False) -> tuple[list[str], str]:
    """Devuelve (ids, total_encontrados)."""
    try:
        resp = requests.get(
            f"{BASE_URL}/esearch.fcgi",
            params={"db": "pubmed", "term": term, "retmode": "json", "retmax": str(n)},
            timeout=10
        )
        result = resp.json().get("esearchresult", {})
        if show_translation:
            translation = result.get("querytranslation", "")
            if translation:
                print(f"  [PubMed ATM] Query expandida: {translation}")
        return result.get("idlist", []), result.get("count", "0")
    except Exception as e:
        print(f"[esearch] ERROR: {e}")
        return [], "0"


def _efetch(ids: list[str]) -> str:
    """Descarga título + abstract de los IDs dados, vía XML."""
    if not ids:
        return ""
    try:
        resp = requests.get(
            f"{BASE_URL}/efetch.fcgi",
            params={"db": "pubmed", "id": ",".join(ids), "rettype": "abstract", "retmode": "xml"},
            timeout=15
        )
        root = ET.fromstring(resp.content)
        bloques = []
        for art in root.findall(".//PubmedArticle"):
            pmid  = art.findtext(".//PMID", "").strip()
            title = art.findtext(".//ArticleTitle", "").strip()
            # AbstractText puede tener varios nodos (structured abstract)
            abstract_parts = [n.text or "" for n in art.findall(".//AbstractText")]
            abstract = " ".join(p.strip() for p in abstract_parts if p.strip())
            if title or abstract:
                bloques.append(f"PMID: {pmid}\nTitle: {title}\nAbstract: {abstract}")
        return "\n\n".join(bloques)
    except Exception as e:
        print(f"[efetch] ERROR: {e}")
        return ""


# ─────────────────────────────────────────────────────────────
# Función importable — datos para el LLM
# ─────────────────────────────────────────────────────────────

def obtener_datos_pubmed_clinico(farmaco_inn: str, ram_en: str) -> dict:
    """
    Función importable. Devuelve datos clínicos de PubMed para el LLM.
    Los parámetros deben venir ya normalizados (INN y término en inglés).

    Devuelve hasta 4 bloques (máx. 5 abstracts en total):
      1. casos_reportados  — hasta 2 abstracts de casos clínicos/notificaciones
      2. factores_riesgo   — hasta 2 abstracts de factores predisponentes
      3. recomendaciones   — hasta 1 abstract de manejo/prevención
      + asociacion_general — hasta 5 abstracts, solo si los 3 anteriores dan 0 resultados

    Bloques sin resultados quedan marcados explícitamente como "sin resultados".
    Cada bloque incluye: query, n_encontrados, ids (PMIDs) y texto de abstracts.
    """

    busquedas_especificas = {
        "casos_reportados": {
            "query": (
                f'{farmaco_inn} AND "{ram_en}" AND '
                f'(case OR "case report"[Publication Type] OR "case series"[Publication Type])'
            ),
            "max_abstracts": 2,
            "tipo_evidencia": "directa",
            "interpretacion_patron": "Se identificaron %n artículos reportando casos clínicos de %ram con %farmaco.",
        },
        "factores_riesgo": {
            "query": (
                f'{farmaco_inn} AND "{ram_en}" AND '
                f'(risk OR susceptibility OR predisposing OR "risk factor")'
            ),
            "max_abstracts": 2,
            "tipo_evidencia": "directa",
            "interpretacion_patron": "Se encontraron %n estudios sobre factores de riesgo para %ram en usuarios de %farmaco.",
        },
        "recomendaciones": {
            "query": (
                f'{farmaco_inn} AND "{ram_en}" AND '
                f'(management OR prevention OR monitoring OR guideline)'
            ),
            "max_abstracts": 1,
            "tipo_evidencia": "directa/contextual",
            "interpretacion_patron": "Se localizaron %n recomendaciones de manejo/prevención para %ram en pacientes con %farmaco.",
        },
    }

    busqueda_general = {
        "asociacion_general": {
            "query": f'{farmaco_inn} AND "{ram_en}"',
            "max_abstracts": 5,
            "tipo_evidencia": "directa",
            "interpretacion_patron": "Se encontraron %n artículos en PubMed que mencionan %ram y %farmaco.",
        },
    }

    secciones = {}
    ids_vistos: set[str] = set()  # Solo para n_articulos_descargados_unicos
    evidencia_directa = 0

    def _ejecutar_busqueda(clave: str, cfg: dict) -> None:
        nonlocal evidencia_directa
        query = cfg["query"]
        ids, total = _esearch(query, cfg["max_abstracts"])
        # Cada sección descarga sus propios abstracts sin deduplicar,
        # para que ninguna sección quede vacía por solapamiento con otra.
        texto = _efetch(ids)
        ids_vistos.update(ids)

        n_encontrados = int(total)
        n_descargados = len(ids)
        evidencia_directa += n_encontrados

        interpretacion = (
            cfg["interpretacion_patron"]
            .replace("%n", str(n_encontrados))
            .replace("%farmaco", farmaco_inn)
            .replace("%ram", ram_en)
        )
        if n_encontrados == 0:
            interpretacion = f"Sin evidencia documentada en PubMed sobre {ram_en} específicamente relacionada con {farmaco_inn}."

        urls = [f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" for pmid in ids]

        secciones[clave] = {
            "query": query,
            "n_encontrados": n_encontrados,
            "n_descargados": n_descargados,
            "ids": ids,
            "urls": urls,
            "texto": texto or "Sin resultados para esta búsqueda.",
            "tipo_evidencia": cfg["tipo_evidencia"],
            "interpretacion": interpretacion,
        }

    # ── Ejecutar búsquedas específicas ────────────────────────────────────────
    for clave, cfg in busquedas_especificas.items():
        _ejecutar_busqueda(clave, cfg)

    # ── Fallback: asociacion_general solo si todas las específicas son vacías ──
    if evidencia_directa == 0:
        for clave, cfg in busqueda_general.items():
            _ejecutar_busqueda(clave, cfg)
    
# ── Cálculo de disponibilidad y síntesis ────────────────────────────────
    disponible_directo = evidencia_directa > 0

    n_articulos_descargados_unicos = len(ids_vistos)

    # Síntesis automática de interpretación general
    n_encontrados_directos = sum(s["n_encontrados"] for s in secciones.values())
    n_descargados_directos = sum(s["n_descargados"] for s in secciones.values())

    if disponible_directo:
        interpretacion_general = (
            f"Se identificaron {n_encontrados_directos} artículos en PubMed con términos relacionados con "
            f"{farmaco_inn} y {ram_en} (búsquedas directas). "
            f"Se descargaron {n_descargados_directos} abstracts únicos para revisión. "
            f"La relevancia clínica de cada artículo debe verificarse individualmente."
        )
    else:
        interpretacion_general = (
            f"No se encontraron artículos en PubMed que combinen {farmaco_inn} y {ram_en} "
            f"en las búsquedas directas realizadas."
        )

    return {
        "farmaco": farmaco_inn,
        "ram": ram_en,
        "disponible_directo": disponible_directo,
        "n_articulos_descargados_unicos": n_articulos_descargados_unicos,
        "interpretacion": interpretacion_general,
        "secciones": secciones,
    }


# ─────────────────────────────────────────────────────────────
# Main — ejecución directa para pruebas
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 3:
        farmaco_arg   = sys.argv[1]
        cod_meddra    = sys.argv[2]
        ram_fallback  = " ".join(sys.argv[3:]) if len(sys.argv) > 3 else CASO["ram"]
    else:
        farmaco_arg   = CASO["farmaco"]
        cod_meddra    = CASO["cod_meddra"]
        ram_fallback  = CASO["ram"]

    SEP = "=" * 70

    # ── NORMALIZACIÓN ────────────────────────────────────────
    print(f"\n{SEP}")
    print("NORMALIZACIÓN")
    print(SEP)
    farmaco_inn = normalizar_farmaco(farmaco_arg)
    ram_en      = normalizar_ram(cod_meddra, ram_fallback)

    # Nombre en español via UMLS (para la búsqueda ES)
    import sys as _sys, os as _os
    _sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), "..", "src", "pipeline"))
    from terminology import meddra_code_to_name
    ram_es = meddra_code_to_name(cod_meddra, lang="es") or ram_fallback

    print(f"  Fármaco entrada   : {farmaco_arg}")
    print(f"  Fármaco (INN EN)  : {farmaco_inn}")
    print(f"  RAM (EN)          : {ram_en}")
    print(f"  RAM (ES)          : {ram_es}")

    # ── BÚSQUEDA EN INGLÉS ──────────────────────────────────
    print(f"\n{SEP}")
    print("BÚSQUEDA EN INGLÉS  (fármaco INN + RAM EN)")
    print(SEP)

    datos = obtener_datos_pubmed_clinico(farmaco_inn, ram_en)

    print(f"  Artículos encontrados (total secciones) : {sum(s['n_encontrados'] for s in datos['secciones'].values())}")
    print(f"  Artículos descargados únicos            : {datos['n_articulos_descargados_unicos']}")
    for clave, sec in datos["secciones"].items():
        marca = "▶" if sec["n_encontrados"] > 0 else "✗"
        print(f"  {marca} [{clave}] {sec['n_encontrados']} artículos")
        for url in sec["urls"]:
            print(f"      {url}")

    # ── BÚSQUEDA LIBRE EN INGLÉS (sin campos MeSH) ─────────
    print(f"\n{SEP}")
    print("BÚSQUEDA LIBRE EN INGLÉS  (sin campos MeSH estrictos)")
    print(SEP)

    ids_libre, total_libre = _esearch(
        f"{farmaco_inn} AND \"{ram_en}\"",
        n=5,
        show_translation=True,
    )
    print(f"  Query: {farmaco_inn} AND \"{ram_en}\"")
    print(f"  Artículos encontrados: {total_libre}")
    for pmid in ids_libre:
        print(f"    https://pubmed.ncbi.nlm.nih.gov/{pmid}/")

    # ── BÚSQUEDA EN CASTELLANO ──────────────────────────────
    print(f"\n{SEP}")
    print("BÚSQUEDA EN CASTELLANO  (fármaco ES + RAM ES)")
    print(SEP)

    ids_es, total_es = _esearch(
        f"{farmaco_arg} AND {ram_es} AND (reaccion adversa OR toxicidad OR efecto adverso)",
        n=5
    )
    print(f"  Query: {farmaco_arg} AND {ram_es} AND (reaccion adversa OR toxicidad)")
    print(f"  Artículos encontrados: {total_es}")
    for pmid in ids_es:
        print(f"    https://pubmed.ncbi.nlm.nih.gov/{pmid}/")

    # ── COMPARATIVA ─────────────────────────────────────────
    print(f"\n{SEP}")
    print("COMPARATIVA")
    print(SEP)
    total_en = sum(s["n_encontrados"] for s in datos["secciones"].values())
    print(f"  EN MeSH estricto  ({farmaco_inn} + {ram_en})     : {total_en} artículos")
    print(f"  EN libre          ({farmaco_inn} + \"{ram_en}\") : {total_libre} artículos")
    print(f"  ES libre          ({farmaco_arg} + {ram_es})     : {total_es} artículos")
    print(f"  --> Las queries MeSH exigen términos exactos del tesauro; la búsqueda libre es más permisiva")


    # Salida JSON estructurada
    print(f"\n{SEP}")
    print("SALIDA JSON ESTRUCTURADA (para LLM)")
    print(SEP)
    output_json = {
        "farmaco": datos["farmaco"],
        "ram": datos["ram"],
        "disponible_directo": datos["disponible_directo"],
        "n_articulos_descargados_unicos": datos["n_articulos_descargados_unicos"],
        "interpretacion": datos["interpretacion"],
        "secciones": {
            k: {
                "query": v["query"],
                "n_encontrados": v["n_encontrados"],
                "n_descargados": v["n_descargados"],
                "ids": v["ids"],
                "urls": v["urls"],
                "texto": v["texto"],
            }
            for k, v in datos["secciones"].items()
        }
    }
    print(json.dumps(output_json, indent=2, ensure_ascii=False))

