#!/usr/bin/env python3
"""
Etapa 2: Limpieza del texto extraído de PDFs académicos.

Lee  : <input_dir>/*.txt  (generados por extract_text.py)
Escribe: <output_dir>/*.txt  (texto limpio, sin marcadores de página)
         <output_dir>/manifest.json  (copiado del input)

Limpieza aplicada:
  1. Cabeceras / pies de página: bloques que aparecen en ≥40 % de las páginas.
  2. URLs y correos electrónicos.
  3. Sección de Referencias (todo lo que va después del heading "References").
  4. Ruido residual: números de página sueltos, copyright, "Page X of Y",
     DOIs, patrones editoriales comunes.
"""

import json
import os
import re
import shutil
from collections import Counter, defaultdict
from pathlib import Path


# ---------------------------------------------------------------------------
# Utilidades de rutas (Windows long-path safe)
# ---------------------------------------------------------------------------

def safe_path(path) -> str:
    p = Path(path).resolve()
    s = str(p)
    if os.name != "nt" or s.startswith("\\\\?\\"):
        return s
    if s.startswith("\\\\"):
        return "\\\\?\\UNC\\" + s.lstrip("\\")
    return "\\\\?\\" + s


# ---------------------------------------------------------------------------
# Constantes y patrones de ruido
# ---------------------------------------------------------------------------

_PAGE_MARKER = re.compile(r"^\[\[PAGE:(\d+)\]\]$")

# Bloques enteros que son ruido con certeza
_NOISE_BLOCK = re.compile(
    r"""
    ^(?:
        \d{1,4}                                      # número de página suelto
      | page\s+\d+\s+of\s+\d+                        # "Page 3 of 12"
      | p\.\s*\d+                                     # "p. 4"
      | \d+\s*/\s*\d+                                 # "3/12"
      | (?:©|\(c\)|copyright)\s*\d{4}.*              # Copyright 2024 ...
      | all\s+rights\s+reserved.*
      | this\s+is\s+an\s+open\s+access\s+article.*
      | this\s+article\s+is\s+licensed.*
      | published\s+by\s+\w.*                        # Published by Elsevier...
      | downloaded\s+from\s+.*
      | for\s+personal\s+use\s+only.*
      | springer\s+nature.*
      | wolters\s+kluwer.*
      | publisher['']?s?\s+note.*
      | doi\s*:\s*\S+
      | https?://\S+
      | www\.\S+
      | \S+@\S+\.\S+                                  # email
    )$
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Líneas dentro de un bloque que contienen ruido (se eliminan línea a línea)
_NOISE_LINE = re.compile(
    r"""
    (?:
        https?://\S+
      | www\.\S+
      | \S+@\S+\.\S+
      | doi\s*:\s*\S+
      | downloaded\s+from
      | for\s+personal\s+use\s+only
      | all\s+rights\s+reserved
      | (?:©|\(c\)|copyright)\s*\d{4}
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Headings de referencias que indican el inicio de la sección bibliográfica
_REF_HEADING = re.compile(
    r"^(?:references?|bibliography|bibliograf[íi]a|riferimenti|références?|literatur)[\s:]*$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Normalización de bloques para detección de repetidos
# ---------------------------------------------------------------------------

def _normalize_for_dedup(text: str) -> str:
    """
    Normaliza un bloque para comparación: elimina números al final
    (números de página variables) y colapsa espacios.
    Ej. "Smith et al. J Nephrol 2020   341" → "smith et al. j nephrol 2020"
    """
    t = text.strip().lower()
    t = re.sub(r"\s*\d+\s*$", "", t)   # número de página al final
    t = re.sub(r"\s+", " ", t)
    return t


# ---------------------------------------------------------------------------
# Parseo del fichero con marcadores [[PAGE:N]]
# ---------------------------------------------------------------------------

def _parse_pages(raw: str) -> dict[int, list[str]]:
    """
    Devuelve {page_num: [bloque1, bloque2, ...]}
    Los bloques NO incluyen los marcadores [[PAGE:N]].
    Si el fichero no tiene marcadores (formato antiguo), todo va a la página 0.
    """
    pages: dict[int, list[str]] = defaultdict(list)
    current_page = 0

    for raw_block in raw.split("\n\n"):
        block = raw_block.strip()
        if not block:
            continue
        m = _PAGE_MARKER.match(block)
        if m:
            current_page = int(m.group(1))
        else:
            pages[current_page].append(block)

    return dict(pages)


# ---------------------------------------------------------------------------
# Detección de cabeceras / pies repetidos
# ---------------------------------------------------------------------------

def _find_repeated_headers(pages: dict[int, list[str]],
                            min_pages: int = 2,
                            min_fraction: float = 0.40,
                            max_words: int = 30) -> set[str]:
    """
    Devuelve el conjunto de bloques (normalizados) que aparecen en ≥ min_fraction
    de las páginas Y tienen ≤ max_words palabras.
    """
    n_pages = len(pages)
    if n_pages == 0:
        return set()

    threshold = max(min_pages, int(n_pages * min_fraction))

    # Contar en cuántas páginas distintas aparece cada versión normalizada
    page_count: Counter = Counter()
    norm_to_original: dict[str, str] = {}

    for blocks in pages.values():
        seen_this_page: set[str] = set()
        for b in blocks:
            if len(b.split()) > max_words:
                continue
            norm = _normalize_for_dedup(b)
            if norm and norm not in seen_this_page:
                page_count[norm] += 1
                seen_this_page.add(norm)
                norm_to_original.setdefault(norm, b)

    repeated = {norm for norm, cnt in page_count.items() if cnt >= threshold}
    return repeated


# ---------------------------------------------------------------------------
# Localización de la sección de referencias
# ---------------------------------------------------------------------------

def _find_references_cutoff(all_blocks: list[str]) -> int:
    """
    Devuelve el índice del primer bloque de referencias, buscando solo
    en la segunda mitad del documento (para no cortar texto legítimo).
    Devuelve len(all_blocks) si no se encuentra nada.
    """
    start_search = len(all_blocks) // 2
    for i in range(start_search, len(all_blocks)):
        if _REF_HEADING.match(all_blocks[i].strip()):
            return i
    return len(all_blocks)


# ---------------------------------------------------------------------------
# Limpieza de un fichero
# ---------------------------------------------------------------------------

def clean_text(raw: str) -> tuple[str, list[dict]]:
    """
    Devuelve (texto_limpio, lista_de_eliminados).
    Cada entrada de lista_de_eliminados es:
      {"reason": str, "text": str}
    """
    pages = _parse_pages(raw)
    repeated_headers = _find_repeated_headers(pages)

    # Lista plana de bloques en orden de página
    all_blocks: list[str] = []
    for page_num in sorted(pages.keys()):
        all_blocks.extend(pages[page_num])

    # Corte en referencias
    cutoff = _find_references_cutoff(all_blocks)
    body_blocks  = all_blocks[:cutoff]
    ref_blocks   = all_blocks[cutoff:]

    clean_blocks:   list[str]  = []
    removed:        list[dict] = []

    # Registrar todo lo cortado como referencias
    if ref_blocks:
        removed.append({
            "reason": "referencias",
            "text": "\n\n".join(ref_blocks),
        })

    for block in body_blocks:
        # 1. Ruido completo como bloque
        if _NOISE_BLOCK.match(block.strip()):
            removed.append({"reason": "ruido_bloque", "text": block})
            continue

        # 2. Cabeceras / pies repetidos
        norm = _normalize_for_dedup(block)
        if norm in repeated_headers:
            removed.append({"reason": "cabecera_repetida", "text": block})
            continue

        # 3. Limpiar línea a línea dentro del bloque
        original_block = block
        clean_lines   = []
        removed_lines = []
        for line in block.splitlines():
            if _NOISE_LINE.search(line):
                removed_lines.append(line)
            else:
                clean_lines.append(line)
        if removed_lines:
            removed.append({
                "reason": "lineas_ruido",
                "text": "\n".join(removed_lines),
            })
        block = "\n".join(clean_lines).strip()
        if not block:
            if original_block.strip():
                removed.append({"reason": "bloque_vacio_tras_limpieza", "text": original_block})
            continue

        # 4. Descartar bloques muy cortos sin letras
        words = block.split()
        if len(words) <= 2 and not any(c.isalpha() for c in block):
            removed.append({"reason": "ruido_corto", "text": block})
            continue

        clean_blocks.append(block)

    return "\n\n".join(clean_blocks), removed


# ---------------------------------------------------------------------------
# Exportación
# ---------------------------------------------------------------------------

def process_file(in_path: Path, out_path: Path, removed_path: Path) -> None:
    with open(safe_path(in_path), encoding="utf-8") as f:
        raw = f.read()

    cleaned, removed = clean_text(raw)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(safe_path(out_path), "w", encoding="utf-8") as f:
        f.write(cleaned)

    # Escribir log de eliminados
    removed_path.parent.mkdir(parents=True, exist_ok=True)
    with open(safe_path(removed_path), "w", encoding="utf-8") as f:
        f.write(f"=== ELIMINADOS DE: {in_path.name} ===\n\n")
        by_reason: dict[str, list[str]] = {}
        for entry in removed:
            by_reason.setdefault(entry["reason"], []).append(entry["text"])
        for reason, texts in by_reason.items():
            f.write(f"{'─' * 60}\n")
            f.write(f"[{reason.upper()}]  ({len(texts)} elemento/s)\n")
            f.write(f"{'─' * 60}\n")
            for t in texts:
                f.write(t.strip() + "\n")
                f.write("· · ·\n")
            f.write("\n")

    orig_words  = len(raw.split())
    clean_words = len(cleaned.split())
    removed_pct = 100 * (1 - clean_words / orig_words) if orig_words else 0
    print(f"  {in_path.name}  ->  {clean_words:,} palabras  (-{removed_pct:.1f}%)")


def process_folder(input_dir: Path, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    # Copiar manifiesto si existe
    manifest_src = input_dir / "manifest.json"
    if manifest_src.exists():
        shutil.copy2(safe_path(manifest_src), safe_path(output_dir / "manifest.json"))
        print(f"Manifiesto copiado -> {output_dir / 'manifest.json'}\n")

    removed_dir = output_dir / "removed_log"
    removed_dir.mkdir(parents=True, exist_ok=True)

    txt_files = sorted(input_dir.glob("*.txt"))
    if not txt_files:
        print(f"No se encontraron ficheros .txt en {input_dir}")
        return

    for in_path in txt_files:
        removed_path = removed_dir / f"{in_path.stem}_removed.txt"
        process_file(in_path, output_dir / in_path.name, removed_path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Etapa 2: limpia el texto extraído de PDFs académicos."
    )
    parser.add_argument("-input", required=True,
                        help="Directorio con los .txt de extract_text.py")
    parser.add_argument("-o", "--output", default="output_clean",
                        help="Directorio de salida (default: output_clean/)")
    args = parser.parse_args()

    inp = Path(args.input)
    out = Path(args.output)

    if not inp.is_dir():
        print(f"Error: '{inp}' no es una carpeta válida.")
        raise SystemExit(1)

    print(f"Limpiando textos de: {inp}")
    print(f"Salida en          : {out}\n")
    process_folder(inp, out)
    print("\nListo.")


if __name__ == "__main__":
    main()
