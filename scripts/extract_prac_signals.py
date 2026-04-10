"""
Extrae la sección 4 (Signals assessment and prioritisation) de cada acta PRAC.
Busca en data/EMA PRAC/minutes Y data/EMA PRAC/agendas.
Salida: data/prac_signals.json  →  lista de documentos con fecha + texto sección 4.
"""

import json
import re
import sys
from pathlib import Path

import fitz  # PyMuPDF


# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

PRAC_DIR = Path(__file__).parent.parent / "data" / "EMA PRAC"
MINUTES_DIR = PRAC_DIR / "minutes"
AGENDAS_DIR = PRAC_DIR / "agendas"
OUTPUT    = Path(__file__).parent.parent / "data" / "prac_signals.json"

# Patrón principal (con número de sección)
SECTION_PATTERN = re.compile(
    r"4[\.\s]+Signals?\s+assessment\s+and\s+prioriti[sz]ation\w*([\s\S]*?)"
    r"(?=^\s*5[\.\s]+Risk\s+Management|^\s*5[\.\s]+Pharmacovigilance|^\s*6[\.\s]|\Z)",
    re.IGNORECASE | re.MULTILINE,
)


# Patrones para detectar la fecha en las primeras líneas del PDF
DATE_PATTERNS = [
    # "10-13 February 2025" / "10 to 13 February 2025"
    re.compile(
        r"\b(\d{1,2}(?:\s*[-–to]+\s*\d{1,2})?\s+"
        r"(?:January|February|March|April|May|June|July|August|"
        r"September|October|November|December)\s+\d{4})\b",
        re.IGNORECASE,
    ),
    # "February 2025"
    re.compile(
        r"\b((?:January|February|March|April|May|June|July|August|"
        r"September|October|November|December)\s+\d{4})\b",
        re.IGNORECASE,
    ),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def extract_full_text(pdf_path: Path) -> str:
    """Extrae texto completo del PDF en orden de lectura."""
    doc = fitz.open(str(pdf_path))
    pages = []
    for page in doc:
        pages.append(page.get_text("text"))
    doc.close()
    return "\n".join(pages)


def find_date(text: str, filename: str) -> str:
    """Busca la fecha en las primeras ~500 caracteres del texto."""
    header = text[:500]
    for pattern in DATE_PATTERNS:
        m = pattern.search(header)
        if m:
            return m.group(1).strip()

    # Fallback: extraer del nombre de archivo
    # "minutes-prac-meeting-10-13-february-2025_en.pdf"
    m = re.search(
        r"(\d{1,2}-\d{1,2}-([a-z]+-\d{4}))",
        filename,
        re.IGNORECASE,
    )
    if m:
        return m.group(0).replace("-", " ").title()

    return "Fecha no encontrada"


def clean_text_for_llm(text: str) -> str:
    """
    Limpia el texto para uso con LLM:
    - Elimina espacios múltiples
    - Normaliza saltos de línea
    - Elimina caracteres raros
    - Preserva estructura de párrafos
    """
    # Normalizar saltos de línea
    text = re.sub(r'\r\n', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    
    # Eliminar espacios múltiples en línea
    text = re.sub(r' {2,}', ' ', text)
    
    # Eliminar espacios al inicio/final de línea
    lines = [line.rstrip() for line in text.split('\n')]
    text = '\n'.join(lines)
    
    # Eliminar comillas raras, guiones raros
    text = text.replace('"', '"').replace('"', '"')
    text = text.replace(''', "'").replace(''', "'")
    text = re.sub(r'[-–—]+', '-', text)
    
    # Limpiar caracteres de control pero preservar formato
    text = ''.join(c for c in text if ord(c) >= 32 or c in '\n\t')
    
    return text.strip()


def extract_section4(text: str) -> tuple[str, str]:
    """
    Devuelve (texto_seccion4, estado).
    estado: "ok" | "fallback" | "not_found"

    Toma la ÚLTIMA coincidencia para evitar capturar el índice/TOC,
    que siempre aparece antes del contenido real.
    """
    flat = re.sub(r"\n{3,}", "\n\n", text)

    matches = list(SECTION_PATTERN.finditer(flat))
    if matches:
        m = matches[-1]
        section_text = m.group(1).strip()
        section_text = clean_text_for_llm(section_text)
        if not section_text:
            # Mostrar 1000 chars a partir del inicio del match para depurar
            raw_context = flat[m.start():m.start() + 1000]
            print(f"\n{'─'*60}")
            print(f"  [0 chars capturados — texto real desde el match:]")
            print(raw_context.replace("\n", "↵\n"))
            print(f"{'─'*60}\n")
        return section_text, "ok"

    return "", "not_found"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def process_pdfs_from_folder(folder_path: Path, source_name: str, skip: set[str]) -> list:
    """Procesa todos los PDFs de una carpeta específica."""
    pdfs = sorted(folder_path.glob("*.pdf"))
    if not pdfs:
        print(f"  [⚠] Sin PDFs en {source_name}")
        return []

    pdfs = [p for p in pdfs if p.name not in skip]
    if not pdfs:
        print(f"  [✓] {source_name}: todos ya procesados.")
        return []

    results = []
    ok = fallback = not_found = 0

    print(f"\n  Procesando {source_name} ({len(pdfs)} PDFs nuevos)...")
    for i, pdf in enumerate(pdfs, 1):
        print(f"    [{i:3d}] {pdf.name[:50]:50s}", end=" ", flush=True)

        try:
            text = extract_full_text(pdf)
        except Exception as e:
            print(f"ERROR lectura")
            results.append({
                "filename": pdf.name,
                "source": source_name,
                "date": "Error",
                "section4": "",
                "status": "read_error",
                "chars": 0,
            })
            continue

        date         = find_date(text, pdf.name)
        section, st  = extract_section4(text)

        if st == "ok":
            ok += 1
        elif st == "fallback":
            fallback += 1
        else:
            not_found += 1

        print(f"✓ {st:8s} | {len(section):6,d} chars")
        if st == "not_found" or len(section) == 0:
            preview = text[:2000].replace("\n", "↵ ")
            print(f"\n{'─'*60}")
            print(f"  [TEXTO VISTO — primeros 2000 chars de {pdf.name}]")
            print(preview)
            print(f"{'─'*60}\n")

        results.append({
            "filename": pdf.name,
            "source": source_name,
            "date": date,
            "section4": section,
            "status": st,
            "chars": len(section),
        })

    print(f"  {source_name}: {ok} ok, {fallback} fallback, {not_found} not_found")
    return results


def main() -> None:
    print(f"{'='*60}")
    print(f"Extrayendo sección 4 de PDFs PRAC/EMA")
    print(f"Carpetas: {MINUTES_DIR} + {AGENDAS_DIR}")
    print(f"{'='*60}")

    # Cargar resultados previos del JSON si existe
    existing = []
    if OUTPUT.exists():
        with open(OUTPUT, encoding="utf-8") as f:
            existing = json.load(f)
        print(f"  JSON existente: {len(existing)} entradas ya procesadas.")
    skip = {r["filename"] for r in existing}

    new_results = []

    # Procesar minutes
    if MINUTES_DIR.exists():
        new_results.extend(process_pdfs_from_folder(MINUTES_DIR, "minutes", skip))

    # Procesar agendas
    if AGENDAS_DIR.exists():
        new_results.extend(process_pdfs_from_folder(AGENDAS_DIR, "agendas", skip))

    all_results = existing + new_results

    if not new_results and not existing:
        print(f"ERROR: No se encontraron PDFs en {MINUTES_DIR} o {AGENDAS_DIR}")
        sys.exit(1)

    if not new_results:
        print("\nNada nuevo que procesar.")
        return

    # Ordenar por source + fecha
    all_results.sort(key=lambda r: (r["source"], r["date"]))

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)

    # Estadísticas
    total = len(all_results)
    ok = sum(1 for r in all_results if r["status"] == "ok")
    fallback = sum(1 for r in all_results if r["status"] == "fallback")
    not_found = sum(1 for r in all_results if r["status"] == "not_found")
    errors = sum(1 for r in all_results if r["status"] == "read_error")
    total_chars = sum(r["chars"] for r in all_results)

    print(f"\n{'='*60}")
    print(f"TOTAL:")
    print(f"  PDFs procesados : {total}")
    print(f"  - OK            : {ok}")
    print(f"  - Fallback      : {fallback}")
    print(f"  - No encontrada : {not_found}")
    print(f"  - Errores       : {errors}")
    print(f"  Total caracteres: {total_chars:,}")
    print(f"Guardado en: {OUTPUT}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
