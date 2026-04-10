"""
Test de busqueda en Reactome (rutas biologicas)
Documentacion: https://reactome.org/ContentService/
Busca por INN en ingles (via RxNorm) para obtener rutas moleculares relevantes.
"""
import requests

# ── PARAMETROS ─────────────────────────────────────────────
FARMACO = "alprazolam"
# ───────────────────────────────────────────────────────────

SEP = "=" * 65


def traducir_farmaco_a_inn(farmaco: str) -> str:
    """Usa RxNorm (NIH, sin API key) para obtener el INN en ingles."""
    try:
        resp = requests.get(
            "https://rxnav.nlm.nih.gov/REST/rxcui.json",
            params={"name": farmaco, "search": 2},
            timeout=10
        )
        rxcui_list = resp.json().get("idGroup", {}).get("rxnormId", [])
        if rxcui_list:
            r2 = requests.get(
                f"https://rxnav.nlm.nih.gov/REST/rxcui/{rxcui_list[0]}/properties.json",
                timeout=10
            )
            inn = r2.json().get("properties", {}).get("name", "")
            if inn:
                print(f"[RxNorm] '{farmaco}' -> '{inn}'")
                return inn
        print(f"[RxNorm] WARNING '{farmaco}' no encontrado, usando nombre original")
        return farmaco
    except Exception as e:
        print(f"[RxNorm] ERROR: {e}")
        return farmaco


def buscar_reactome(query: str, etiqueta: str):
    print(f"\n{SEP}")
    print(f"Reactome [{etiqueta}]: '{query}'")
    print(SEP)

    resp = requests.get(
        "https://reactome.org/ContentService/search/query",
        params={"query": query, "species": "Homo sapiens", "cluster": "true"},
        headers={"Accept": "application/json"},
        timeout=30
    )
    print(f"Status: {resp.status_code} | URL: {resp.url}")

    if not resp.ok:
        print(f"Error: {resp.text[:200]}")
        return

    data = resp.json()
    groups = data.get("results", [])
    total_entries = sum(len(g.get("entries", [])) for g in groups)
    print(f"Grupos: {len(groups)} | Entradas totales: {total_entries}\n")

    for g in groups:
        tipo = g.get("typeName")
        entries = g.get("entries", [])
        print(f"  --- Tipo: {tipo} ({len(entries)} entradas) ---")
        for e in entries[:4]:
            st_id = e.get("stId", "")
            name  = e.get("name", "")
            url   = f"https://reactome.org/content/detail/{st_id}" if st_id.startswith("R-") else "N/A (stId no valido)"
            print(f"    {name}")
            print(f"      stId : {st_id!r}")
            print(f"      URL  : {url}")
        print()


def _buscar_reactome(query: str) -> list[dict]:
    """Lanza una búsqueda en Reactome y devuelve lista de entradas."""
    resp = requests.get(
        "https://reactome.org/ContentService/search/query",
        params={"query": query, "species": "Homo sapiens", "cluster": "true"},
        headers={"Accept": "application/json"},
        timeout=30
    )
    if not resp.ok:
        return []
    entradas = []
    for g in resp.json().get("results", []):
        tipo = g.get("typeName", "")
        for e in g.get("entries", [])[:5]:
            st_id = e.get("stId", "")
            entradas.append({
                "tipo": tipo,
                "nombre": e.get("name", ""),
                "stId": st_id,
                "url": f"https://reactome.org/content/detail/{st_id}" if st_id.startswith("R-") else None,
            })
    return entradas


def obtener_datos_reactome(farmaco: str, dianas: list[str] | None = None) -> dict:
    """
    Función importable. Devuelve dict con vías y reacciones para el LLM.
    Si se pasan dianas (símbolos de gen), también las busca en Reactome.
    """
    farmaco_inn = traducir_farmaco_a_inn(farmaco)

    vias = []
    stids_vistos: set[str] = set()

    # Búsqueda por nombre del fármaco
    for entrada in _buscar_reactome(farmaco_inn):
        if entrada["stId"] not in stids_vistos:
            stids_vistos.add(entrada["stId"])
            vias.append({**entrada, "fuente": "farmaco"})

    # Búsqueda por dianas moleculares (solo Pathway y Reaction para evitar ruido)
    for gen in (dianas or [])[:5]:
        for entrada in _buscar_reactome(gen):
            if entrada["tipo"] not in {"Pathway", "Reaction"}:
                continue
            if entrada["stId"] not in stids_vistos:
                stids_vistos.add(entrada["stId"])
                vias.append({**entrada, "fuente": f"diana:{gen}"})

    if not vias:
        return {
            "farmaco_inn": farmaco_inn,
            "disponible": False,
            "mensaje": f"Sin datos en Reactome para '{farmaco_inn}' ni sus dianas moleculares",
            "vias": []
        }

    fuentes = sorted({v["fuente"] for v in vias})
    return {
        "farmaco_inn": farmaco_inn,
        "disponible": True,
        "mensaje": f"{len(vias)} entradas encontradas (fuentes: {', '.join(fuentes)})",
        "vias": vias,
    }


# ── EJECUCION ───────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 3:
        farmaco_arg  = sys.argv[1]
        reaccion_arg = " ".join(sys.argv[2:])
    else:
        farmaco_arg  = FARMACO
        reaccion_arg = None

    farmaco_inn = traducir_farmaco_a_inn(farmaco_arg)

    # Búsqueda 1: solo fármaco (INN)
    buscar_reactome(farmaco_inn, "solo fármaco (INN)")

    # Búsqueda 2: fármaco + RAM juntos (como hace n8n)
    if reaccion_arg:
        buscar_reactome(f"{farmaco_inn} {reaccion_arg}", "fármaco + RAM (como n8n)")
