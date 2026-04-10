#!/usr/bin/env python3
"""
Etapa 3: Generación de chunks desde texto limpio.

Lee  : <input_dir>/*.txt + manifest.json  (salida de clean_text.py)
Escribe: <output_dir>/<doc_id>.json  por documento

Formato de cada JSON:
  {
    "doc_id":  "doc_0000",
    "source":  "nombre_original.pdf",
    "chunks":  [
      {"chunk_id": 0, "text": "..."},
      ...
    ]
  }

Los chunks se cortan SIEMPRE al final de una oración (punto, cierre de
interrogación o exclamación), nunca en medio de una frase.
El tamaño objetivo en palabras se pasa por CLI (--size, default 200).
"""

import json
import os
import re
from pathlib import Path


# ---------------------------------------------------------------------------
# Utilidad de rutas Windows
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
# Segmentación en oraciones
# ---------------------------------------------------------------------------

# Abreviaturas comunes en textos médicos/científicos (no son fin de oración)
_ABBREVS = {
    "vs", "et", "al", "fig", "figs", "ref", "refs", "no", "vol", "pp",
    "ed", "eds", "e.g", "i.e", "cf", "approx", "dept", "dr", "prof",
    "sr", "jr", "mr", "mrs", "ms", "st", "ave", "jan", "feb", "mar",
    "apr", "jun", "jul", "aug", "sep", "oct", "nov", "dec",
    "mg", "kg", "ml", "mmhg", "mmol", "µg", "µl", "nm", "mm",
    "wk", "mo", "yr", "min", "sec", "h", "d",
}

# Detecta fin de oración real: . ! ? seguido de espacio + mayúscula o fin de texto
_SENT_END = re.compile(r'(?<![A-Z])([.!?])\s+(?=[A-Z\"\(\[]|$)')


def _is_abbreviation(text: str, dot_pos: int) -> bool:
    """Heurística: ¿el punto en dot_pos es una abreviatura?"""
    before = text[:dot_pos].split()
    if not before:
        return False
    last_word = before[-1].lower().rstrip(".")
    # Es abreviatura si: es corta, está en la lista, o es un número
    if last_word in _ABBREVS:
        return True
    if re.match(r'^\d+$', last_word):        # "Fig. 3", "Table 1."
        return True
    if len(last_word) <= 2:                   # "al.", "vs."
        return True
    return False


def split_sentences(text: str) -> list[str]:
    """
    Divide el texto en oraciones intentando no partir en abreviaturas.
    Cada oración conserva el punto final.
    """
    sentences: list[str] = []
    start = 0

    for m in _SENT_END.finditer(text):
        end = m.end(1)          # posición justo después del punto/!/?
        if _is_abbreviation(text, m.start(1)):
            continue
        sent = text[start:end].strip()
        if sent:
            sentences.append(sent)
        start = m.end()         # saltar el espacio tras el punto

    # Última oración (sin punto final o fin de texto)
    tail = text[start:].strip()
    if tail:
        sentences.append(tail)

    return sentences


# ---------------------------------------------------------------------------
# Construcción de chunks
# ---------------------------------------------------------------------------

def make_chunks(text: str, target: int = 200, overlap: int = 0) -> list[dict]:
    """
    Agrupa oraciones en chunks de ~target palabras.
    Cierra el chunk solo al final de una oración.

    Estrategia:
    - Acumular oraciones hasta alcanzar target palabras.
    - Al llegar a target, cerrar en el siguiente punto de oración.
    - Si una oración individual supera target, se mantiene sola (no se parte).

    Si overlap > 0, las últimas ~overlap palabras (en límite de oración) del chunk K
    se añaden como prefijo del chunk K+1, dando contexto al modelo sin duplicar
    entidades: voting.py descarta entidades que caen en esa región de prefijo.
    Cada chunk almacena overlap_prefix_chars = número de caracteres del prefijo
    heredado, que permite a voting.py saber qué región ignorar.
    """
    sentences = split_sentences(text)
    if not sentences:
        return []

    chunks: list[dict] = []
    chunk_id = 0
    sent_idx = 0
    overlap_sents: list[str] = []   # oraciones heredadas del chunk anterior

    while sent_idx < len(sentences):
        # Iniciar chunk con el overlap del chunk anterior
        current: list[str] = list(overlap_sents)
        current_words = sum(len(s.split()) for s in current)

        # Añadir oraciones nuevas hasta alcanzar el objetivo
        while sent_idx < len(sentences):
            sent = sentences[sent_idx]
            sw = len(sent.split())
            current.append(sent)
            current_words += sw
            sent_idx += 1
            if current_words >= target:
                break

        chunk_text = " ".join(current)

        # overlap_prefix_chars: longitud del prefijo heredado en chunk_text
        overlap_prefix_chars = len(" ".join(overlap_sents)) if overlap_sents else 0

        chunks.append({
            "chunk_id": chunk_id,
            "text": chunk_text,
            "overlap_prefix_chars": overlap_prefix_chars,
        })
        chunk_id += 1

        # Calcular overlap para el siguiente chunk:
        # tomar las últimas oraciones NUEVAS (no las heredadas) que sumen ~overlap palabras
        if overlap > 0 and sent_idx < len(sentences):
            new_sents = current[len(overlap_sents):]
            overlap_sents = []
            overlap_words = 0
            for s in reversed(new_sents):
                sw = len(s.split())
                if overlap_words + sw <= overlap:
                    overlap_sents.insert(0, s)
                    overlap_words += sw
                else:
                    break
        else:
            overlap_sents = []

    return chunks


# ---------------------------------------------------------------------------
# Exportación
# ---------------------------------------------------------------------------

def process_file(doc_id: str,
                 source: str,
                 in_path: Path,
                 out_path: Path,
                 target: int,
                 overlap: int = 0) -> int:
    with open(safe_path(in_path), encoding="utf-8") as f:
        text = f.read()

    chunks = make_chunks(text, target=target, overlap=overlap)

    result = {
        "doc_id": doc_id,
        "source": source,
        "chunks": chunks,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(safe_path(out_path), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    return len(chunks)


def process_folder(input_dir: Path, output_dir: Path, target: int, overlap: int = 0) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    # Cargar manifiesto para mapear doc_id -> nombre original
    manifest_path = input_dir / "manifest.json"
    if manifest_path.exists():
        with open(safe_path(manifest_path), encoding="utf-8") as f:
            manifest: dict[str, str] = json.load(f)
    else:
        manifest = {}

    txt_files = sorted(input_dir.glob("*.txt"))
    if not txt_files:
        print(f"No se encontraron ficheros .txt en {input_dir}")
        return

    total_chunks = 0
    for in_path in txt_files:
        doc_id = in_path.stem                          # "doc_0000"
        source = manifest.get(doc_id, in_path.name)
        out_path = output_dir / f"{doc_id}.json"

        n = process_file(doc_id, source, in_path, out_path, target, overlap)
        total_chunks += n
        print(f"  {doc_id}  ->  {n} chunks")

    print(f"\nTotal: {total_chunks} chunks en {len(txt_files)} documentos")
    print(f"Salida: {output_dir}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Etapa 3: genera chunks de texto para RAG."
    )
    parser.add_argument("-input", required=True,
                        help="Directorio con los .txt limpios (clean_text.py)")
    parser.add_argument("-o", "--output", default="output_chunks",
                        help="Directorio de salida (default: output_chunks/)")
    parser.add_argument("--size", type=int, default=200,
                        help="Palabras objetivo por chunk (default: 200)")
    parser.add_argument("--overlap", type=int, default=0,
                        help="Palabras de overlap entre chunks consecutivos (default: 0). "
                             "Se respeta el límite de oración. Las entidades del prefijo "
                             "heredado se descartan en voting.py para evitar duplicados.")
    args = parser.parse_args()

    inp = Path(args.input)
    out = Path(args.output)

    if not inp.is_dir():
        print(f"Error: '{inp}' no es una carpeta valida.")
        raise SystemExit(1)

    if args.overlap >= args.size:
        print(f"Error: --overlap ({args.overlap}) debe ser menor que --size ({args.size}).")
        raise SystemExit(1)

    print(f"Chunking  : {inp}")
    print(f"Salida    : {out}")
    print(f"Tam. chunk: ~{args.size} palabras (corte en final de oracion)")
    print(f"Overlap   : ~{args.overlap} palabras\n")
    process_folder(inp, out, target=args.size, overlap=args.overlap)


if __name__ == "__main__":
    main()
