"""
Test de OpenFDA para interacciones entre fármacos.
Documentación: https://open.fda.gov/apis/drug/label/

Sin API key. Extrae la sección de interacciones de la ficha FDA.

Prueba:
  1. Interacciones del fármaco principal
  2. Interacción entre fármaco principal y un segundo fármaco
"""
import requests

# ── PARÁMETROS ──────────────────────────────────────────────
FARMACO = "alprazolam"
FARMACO_2 = "warfarin"
# ────────────────────────────────────────────────────────────

SEP = "=" * 65


def obtener_interacciones(farmaco: str) -> tuple[str, str] | tuple[None, None]:
    """Descarga la sección drug_interactions de la ficha FDA. Devuelve (texto, url_fuente)."""
    resp = requests.get(
        "https://api.fda.gov/drug/label.json",
        params={"search": f'openfda.generic_name:"{farmaco}"', "limit": 1},
        timeout=15
    )
    if resp.ok:
        results = resp.json().get("results", [])
        if results:
            resultado = results[0]
            interacciones = resultado.get("drug_interactions", [])
            if interacciones:
                # URL de la ficha FDA en DailyMed
                set_id = resultado.get("set_id", "")
                url = f"https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid={set_id}" if set_id else "N/A"
                return interacciones[0], url
    return None, None


# ─────────────────────────────────────────────────────────────
# BÚSQUEDA 1 — Interacciones del fármaco principal
# ─────────────────────────────────────────────────────────────

def buscar_interacciones(farmaco: str):
    print(f"\n{SEP}")
    print(f"BÚSQUEDA 1 — Interacciones de '{farmaco}'")
    print(SEP)

    texto, url = obtener_interacciones(farmaco)
    if not texto:
        print(f"  Sin datos de interacciones para '{farmaco}' en OpenFDA")
        return

    print(f"  Fuente : {url}")
    print(f"  Longitud texto: {len(texto)} caracteres\n")
    print(texto[:3000])
    if len(texto) > 3000:
        print(f"\n  ... ({len(texto) - 3000} caracteres más)")


# ─────────────────────────────────────────────────────────────
# BÚSQUEDA 2 — Interacción entre dos fármacos concretos
# ─────────────────────────────────────────────────────────────

def buscar_interaccion_entre(farmaco1: str, farmaco2: str):
    print(f"\n{SEP}")
    print(f"BÚSQUEDA 2 — Interacción entre '{farmaco1}' y '{farmaco2}'")
    print(SEP)

    for nombre, otro in [(farmaco1, farmaco2), (farmaco2, farmaco1)]:
        texto, url = obtener_interacciones(nombre)
        if not texto:
            print(f"  Sin datos para '{nombre}'")
            continue

        print(f"\n  Fuente '{nombre}': {url}")
        if otro.lower() in texto.lower():
            idx    = texto.lower().find(otro.lower())
            inicio = max(0, idx - 200)
            fin    = min(len(texto), idx + 600)
            print(f"  ✅ '{otro}' mencionado en las interacciones de '{nombre}':")
            print(f"  ...{texto[inicio:fin]}...")
        else:
            print(f"  '{otro}' NO mencionado en las interacciones de '{nombre}'")


def obtener_datos_interacciones(farmaco: str) -> dict:
    """
    Función importable. Devuelve interacciones farmacológicas para el LLM.
    """
    texto, url = obtener_interacciones(farmaco)
    if not texto:
        return {"disponible": False, "mensaje": f"Sin datos de interacciones para '{farmaco}' en OpenFDA"}

    return {
        "disponible": True,
        "url_ficha_fda": url,
        "texto_interacciones": texto[:3000],
    }


# ── EJECUCIÓN ───────────────────────────────────────────────
if __name__ == "__main__":
    print(f"Fármaco principal : {FARMACO}")
    print(f"Fármaco secundario: {FARMACO_2}\n")

    buscar_interacciones(FARMACO)
    buscar_interaccion_entre(FARMACO, FARMACO_2)
