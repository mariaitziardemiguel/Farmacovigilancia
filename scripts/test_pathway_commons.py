"""
Test de Pathway Commons para vías biológicas.
Documentación: https://www.pathwaycommons.org/pc2/

Prueba 3 búsquedas:
  1. Por fármaco solo
  2. Por RAM sola
  3. Por fármaco + RAM (búsqueda combinada)
"""
import requests

# ── PARÁMETROS ──────────────────────────────────────────────
FARMACO = "warfarin"
RAM     = "hemorrhage"
# ────────────────────────────────────────────────────────────

SEP = "=" * 65
BASE_URL = "http://www.pathwaycommons.org/pc2"


def buscar_entidad(query: str, etiqueta: str):
    """
    Busca una entidad (fármaco, RAM o término combinado) en Pathway Commons.
    Devuelve los primeros resultados con nombre, tipo y fuente de datos.
    """
    print(f"\n{SEP}")
    print(f"Búsqueda: {etiqueta}")
    print(f"Query: '{query}'")
    print(SEP)

    resp = requests.get(
        f"{BASE_URL}/search.json",
        params={
            "q":        query,
            "organism": "9606",     # Homo sapiens
            "page":     0,
        },
        timeout=15
    )
    print(f"Status: {resp.status_code} | URL: {resp.url}\n")

    if not resp.ok:
        print(f"Error: {resp.text[:300]}")
        return

    data      = resp.json()
    total     = data.get("numHits", 0)
    resultados = data.get("searchHit", [])
    print(f"Total resultados: {total} | Mostrando primeros {len(resultados)}\n")

    for r in resultados[:5]:
        nombre   = r.get("name", ["sin nombre"])[0] if r.get("name") else "sin nombre"
        tipo     = r.get("type", "")
        fuentes  = r.get("dataSource", [])
        uri      = r.get("uri", "")
        excerpts = r.get("excerpt", "")

        print(f"  [{tipo}] {nombre}")
        print(f"    Fuentes : {', '.join(fuentes)}")
        print(f"    URI     : {uri}")
        if excerpts:
            print(f"    Extracto: {excerpts[:200]}")
        print()


def buscar_grafo(termino_a: str, termino_b: str):
    """
    Busca si existe conexión en el grafo de Pathway Commons entre dos términos.
    Usa el endpoint /graph con kind=PATHSBETWEEN.
    """
    print(f"\n{SEP}")
    print(f"Conexión en grafo entre: '{termino_a}' ↔ '{termino_b}'")
    print(SEP)

    # Primero obtenemos los URIs de cada término
    def obtener_uri(query: str) -> str | None:
        r = requests.get(
            f"{BASE_URL}/search.json",
            params={"q": query, "organism": "9606", "page": 0},
            timeout=15
        )
        if r.ok:
            hits = r.json().get("searchHit", [])
            if hits:
                uri = hits[0].get("uri")
                print(f"  URI '{query}': {uri}")
                return uri
        return None

    uri_a = obtener_uri(termino_a)
    uri_b = obtener_uri(termino_b)

    if not uri_a or not uri_b:
        print("  No se pudieron obtener los URIs de ambos términos")
        return

    print(f"\n  Buscando caminos entre los dos URIs...")
    resp = requests.get(
        f"{BASE_URL}/graph",
        params={
            "kind":   "PATHSBETWEEN",
            "source": [uri_a, uri_b],
            "limit":  1,
        },
        timeout=20
    )
    print(f"  Status: {resp.status_code}")

    if resp.ok and resp.content:
        print(f"  Respuesta recibida: {len(resp.content)} bytes")
        print(f"  Content-Type: {resp.headers.get('Content-Type', '')}")
        print(f"  (El grafo se devuelve en formato BioPAX/OWL — requiere parser específico)")
    else:
        print(f"  Sin conexión encontrada o error: {resp.text[:200]}")


# ── EJECUCIÓN ───────────────────────────────────────────────
print(f"Fármaco : {FARMACO}")
print(f"RAM     : {RAM}\n")

# 1. Por fármaco solo
buscar_entidad(FARMACO, "BÚSQUEDA 1 — Por fármaco solo")

# 2. Por RAM sola
buscar_entidad(RAM, "BÚSQUEDA 2 — Por RAM sola")

# 3. Por fármaco + RAM combinados
buscar_entidad(f"{FARMACO} {RAM}", "BÚSQUEDA 3 — Fármaco + RAM combinados")

# 4. Conexión en grafo entre fármaco y RAM
buscar_grafo(FARMACO, RAM)
