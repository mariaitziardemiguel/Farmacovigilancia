"""
Prueba de la API CIMA (AEMPS) — búsqueda de medicamentos y ficha técnica.
Uso: python scripts/test_cima.py <nombre_farmaco>
Ejemplo: python scripts/test_cima.py alprazolam
"""

import sys
import requests

CIMA_BASE = "https://cima.aemps.es/cima/rest"


def buscar_cima(nombre: str) -> None:
    print(f"\n{'='*60}")
    print(f"Búsqueda en CIMA: '{nombre}'")
    print('='*60)

    resp = requests.get(
        f"{CIMA_BASE}/medicamentos",
        params={"nombre": nombre, "estado": 1},
        timeout=10,
    )
    print(f"HTTP {resp.status_code}")
    if not resp.ok:
        print("Error en la petición")
        return

    resultados = resp.json().get("resultados", [])
    print(f"Resultados encontrados: {len(resultados)}\n")

    for i, med in enumerate(resultados[:5]):
        nreg   = med.get("nregistro", "")
        nombre_med = med.get("nombre", "")
        estado = med.get("estado", {}).get("descripcion", "")
        pacts  = [p.get("nombre", "") for p in med.get("pactivos", [])]
        docs   = med.get("docs", [])
        ft_url = next((d.get("urlHtml", d.get("url", "")) for d in docs if d.get("tipo") == 1), None)

        print(f"  [{i}] {nombre_med}")
        print(f"       Nº registro : {nreg}")
        print(f"       Estado      : {estado}")
        print(f"       P. activos  : {', '.join(pacts)}")
        print(f"       Ficha téc.  : {ft_url or 'no disponible'}")
        print()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python scripts/test_cima.py <nombre_farmaco>")
        print("Ejemplo: python scripts/test_cima.py alprazolam")
        sys.exit(1)

    nombre = " ".join(sys.argv[1:])
    buscar_cima(nombre)
