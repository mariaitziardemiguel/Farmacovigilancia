#!/usr/bin/env python3
"""
Etapa 1: Extracción de texto limpio desde PDFs académicos.

Salida por documento : <output_dir>/<doc_id>.txt
Manifiesto global    : <output_dir>/manifest.json  →  {doc_id: nombre_original}
"""

import json
import hashlib
import os
import re
import unicodedata
from pathlib import Path
from collections import defaultdict, Counter
from dataclasses import dataclass

import fitz  # PyMuPDF


# ---------------------------------------------------------------------------
# Utilidades de rutas
# ---------------------------------------------------------------------------

def safe_path(path) -> str:
    p = Path(path).resolve()
    s = str(p)
    if os.name != "nt" or s.startswith("\\\\?\\"):
        return s
    if s.startswith("\\\\"):
        return "\\\\?\\UNC\\" + s.lstrip("\\")
    return "\\\\?\\" + s


def safe_stem(pdf_file: Path, max_len: int = 120) -> str:
    stem = re.sub(r'[<>:"/\\|?*]', "_", pdf_file.stem).strip().rstrip(".")
    stem = stem or "documento"
    if len(stem) > max_len:
        h = hashlib.md5(pdf_file.name.encode()).hexdigest()[:8]
        stem = f"{stem[:max_len]}_{h}"
    return stem


# ---------------------------------------------------------------------------
# Normalización de texto
# ---------------------------------------------------------------------------

_DASHES = re.compile(r"[‐\u2010-\u2015\u2212]")
_SPACES = re.compile(r"[ \t]+")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\u00AD", "")
    text = _DASHES.sub("-", text)
    text = text.replace("\r", "\n")
    text = _SPACES.sub(" ", text)
    text = re.sub(r" *\n *", "\n", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Estructura de bloque
# ---------------------------------------------------------------------------

@dataclass
class Block:
    page: int
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    avg_font_size: float = 0.0
    page_w: float = 0.0
    page_h: float = 0.0
    column: str = "single"
    role: str = "body"

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def n_words(self) -> int:
        return len(self.text.split())


# ---------------------------------------------------------------------------
# Extracción de bloques desde el PDF
# ---------------------------------------------------------------------------

def extract_blocks(pdf_path: Path) -> list[Block]:
    doc = fitz.open(safe_path(pdf_path))
    result: list[Block] = []

    for page_num, page in enumerate(doc):
        page_w = float(page.rect.width)
        page_h = float(page.rect.height)
        raw = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)

        for blk in raw.get("blocks", []):
            if blk.get("type", 0) != 0:
                continue

            lines_text = []
            font_sizes = []
            bx0 = bx1 = by0 = by1 = None

            for line in blk.get("lines", []):
                spans = line.get("spans", [])
                if not spans:
                    continue
                line_text = " ".join(
                    s.get("text", "").strip()
                    for s in spans
                    if s.get("text", "").strip()
                )
                line_text = normalize(line_text)
                if not line_text:
                    continue
                for s in spans:
                    font_sizes.append(s.get("size", 10))
                lx0 = min(s["bbox"][0] for s in spans)
                ly0 = min(s["bbox"][1] for s in spans)
                lx1 = max(s["bbox"][2] for s in spans)
                ly1 = max(s["bbox"][3] for s in spans)
                lines_text.append(line_text)
                bx0 = lx0 if bx0 is None else min(bx0, lx0)
                by0 = ly0 if by0 is None else min(by0, ly0)
                bx1 = lx1 if bx1 is None else max(bx1, lx1)
                by1 = ly1 if by1 is None else max(by1, ly1)

            if not lines_text or bx0 is None:
                continue

            avg_fs = sum(font_sizes) / len(font_sizes) if font_sizes else 10.0
            result.append(Block(
                page=page_num,
                x0=round(bx0, 2), y0=round(by0, 2),
                x1=round(bx1, 2), y1=round(by1, 2),
                text=" ".join(lines_text),
                avg_font_size=round(avg_fs, 2),
                page_w=page_w, page_h=page_h,
            ))

    doc.close()
    return result


# ---------------------------------------------------------------------------
# Detección de layout en columnas
# ---------------------------------------------------------------------------

def detect_split_x(blocks: list[Block],
                   min_blocks: int = 4,
                   min_gap_pt: float = 8.0) -> float | None:
    if len(blocks) < min_blocks:
        return None

    page_w = blocks[0].page_w
    candidates = [b for b in blocks if b.width < page_w * 0.55 and b.n_words >= 2]
    if len(candidates) < min_blocks:
        return None

    sorted_by_x0 = sorted(candidates, key=lambda b: b.x0)
    best_gap = 0.0
    best_split = None

    for i in range(len(sorted_by_x0) - 1):
        lmax = max(b.x1 for b in sorted_by_x0[:i+1])
        rmin = sorted_by_x0[i+1].x0
        gap = rmin - lmax
        if gap > best_gap and gap >= min_gap_pt:
            split_candidate = (lmax + rmin) / 2
            if page_w * 0.20 < split_candidate < page_w * 0.70:
                left_side  = [b for b in candidates if b.x0 < split_candidate]
                right_side = [b for b in candidates if b.x0 >= split_candidate]
                if len(left_side) >= 2 and len(right_side) >= 2:
                    best_gap = gap
                    best_split = round(split_candidate, 2)

    return best_split


def detect_split_x_fallback(blocks: list[Block]) -> float | None:
    if not blocks:
        return None

    page_w = blocks[0].page_w
    text_blocks = [b for b in blocks if b.width < page_w * 0.55 and b.n_words >= 2]
    if len(text_blocks) < 4:
        return None

    bin_size = 10.0
    n_bins = int(page_w / bin_size) + 2
    hist = [[] for _ in range(n_bins)]
    for b in text_blocks:
        idx = min(int(b.x0 / bin_size), n_bins - 1)
        hist[idx].append(b)

    bins_sorted = sorted(range(n_bins), key=lambda i: len(hist[i]), reverse=True)
    top_bins = [b for b in bins_sorted if len(hist[b]) >= 2]
    if len(top_bins) < 2:
        return None

    left_bin = right_bin = None
    for i in range(len(top_bins)):
        for j in range(i+1, len(top_bins)):
            b1, b2 = top_bins[i], top_bins[j]
            if abs(b1 - b2) * bin_size >= page_w * 0.25:
                left_bin  = min(b1, b2)
                right_bin = max(b1, b2)
                break
        if left_bin is not None:
            break

    if left_bin is None:
        return None

    if right_bin * bin_size > page_w * 0.70:
        return None

    lmax = max(b.x1 for b in hist[left_bin])
    rmin = min(b.x0 for b in hist[right_bin])

    if rmin - lmax < 5:
        return None

    return round((lmax + rmin) / 2, 2)


# ---------------------------------------------------------------------------
# Filtro de sidebars
# ---------------------------------------------------------------------------

_SIDEBAR_RE = re.compile(
    r"""
    (?:
        received\s*:|revised\s*:|accepted\s*:|published\s*:
      | address\s+for\s+correspondence
      | e\s*[-–]?\s*mail\s*:
      | doi\s*:
      | quick\s+response\s+code
      | access\s+this\s+article
      | website\s*:
      | how\s+to\s+cite
      | financial\s+support
      | conflicts?\s+of\s+interest
      | department(?:s)?\s+of
      | \d{2}[-/]\d{2}[-/]\d{4}
      | @\w+\.\w+
      | https?://
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


def estimate_body_font(blocks: list[Block]) -> float:
    sizes = [round(b.avg_font_size * 2) / 2 for b in blocks if b.n_words >= 5]
    if not sizes:
        return 10.0
    return Counter(sizes).most_common(1)[0][0]


def is_sidebar(block: Block,
               split_x: float,
               body_font: float,
               right_x_start: float) -> bool:
    right_col_w = block.page_w - right_x_start
    if block.x0 > right_x_start + right_col_w * 0.55:
        return True
    if body_font > 0 and block.avg_font_size < body_font * 0.88:
        return True
    if _SIDEBAR_RE.search(block.text):
        return True
    half_w = block.page_w / 2
    if (block.width < half_w * 0.55
            and block.n_words <= 12
            and block.x0 > split_x + (block.page_w - split_x) * 0.10):
        return True
    return False


def classify_column(block: Block, split_x: float | None) -> str:
    if split_x is None:
        return "single"
    if block.width > block.page_w * 0.55:
        return "single"
    return "left" if block.x0 < split_x else "right"


# ---------------------------------------------------------------------------
# Ordenación de bloques por página
# ---------------------------------------------------------------------------

def order_page(page_blocks: list[Block]) -> list[Block]:
    """Devuelve los bloques de cuerpo principal ordenados en orden de lectura."""
    if not page_blocks:
        return []

    split_x = detect_split_x(page_blocks)
    if split_x is None:
        split_x = detect_split_x_fallback(page_blocks)

    for b in page_blocks:
        b.column = classify_column(b, split_x)

    if split_x is None:
        return sorted(page_blocks, key=lambda b: (b.y0, b.x0))

    singles = [b for b in page_blocks if b.column == "single"]
    lefts   = [b for b in page_blocks if b.column == "left"]
    rights  = [b for b in page_blocks if b.column == "right"]

    body_font     = estimate_body_font(lefts + [b for b in rights if b.n_words >= 8])
    right_x_start = min((b.x0 for b in rights), default=split_x)

    right_body = []
    for b in rights:
        if is_sidebar(b, split_x, body_font, right_x_start):
            b.role = "sidebar"
        else:
            b.role = "body"
            right_body.append(b)

    lefts.sort(key=lambda b: (b.y0, b.x0))
    right_body.sort(key=lambda b: (b.y0, b.x0))
    singles.sort(key=lambda b: b.y0)

    all_body    = lefts + right_body
    col_y_start = min((b.y0 for b in all_body), default=0.0)
    col_y_end   = max((b.y1 for b in all_body), default=float("inf"))

    pre_singles  = [b for b in singles if b.y1 <= col_y_start + 5]
    post_singles = [b for b in singles if b.y0 >= col_y_end - 5]
    mid_singles  = [b for b in singles
                    if b not in pre_singles and b not in post_singles]

    def interleave(col: list[Block], mids: list[Block]) -> list[Block]:
        out: list[Block] = []
        it = iter(sorted(mids, key=lambda b: b.y0))
        nxt = next(it, None)
        for cb in col:
            while nxt is not None and nxt.y0 <= cb.y0:
                out.append(nxt)
                nxt = next(it, None)
            out.append(cb)
        while nxt is not None:
            out.append(nxt)
            nxt = next(it, None)
        return out

    return (
        pre_singles
        + interleave(lefts, mid_singles)
        + right_body
        + post_singles
    )


# ---------------------------------------------------------------------------
# Reconstrucción del texto principal
# ---------------------------------------------------------------------------

def extract_main_text(pdf_path: Path) -> str:
    """
    Extrae y devuelve el texto principal del PDF como string limpio.
    Incluye marcadores [[PAGE:N]] para que la etapa de limpieza pueda
    detectar cabeceras y pies de página repetidos.
    """
    blocks = extract_blocks(pdf_path)

    by_page: dict[int, list[Block]] = defaultdict(list)
    for b in blocks:
        by_page[b.page].append(b)

    segments: list[str] = []
    for page_num in sorted(by_page.keys()):
        page_blocks = order_page(by_page[page_num])
        if not page_blocks:
            continue
        segments.append(f"[[PAGE:{page_num}]]")
        for b in page_blocks:
            segments.append(b.text)

    return "\n\n".join(segments)


# ---------------------------------------------------------------------------
# Exportación
# ---------------------------------------------------------------------------

def export(pdf_path: Path, output_dir: Path, doc_id: str) -> None:
    """Guarda el texto limpio en <output_dir>/<doc_id>.txt."""
    output_dir.mkdir(parents=True, exist_ok=True)
    text = extract_main_text(pdf_path)
    out_path = output_dir / f"{doc_id}.txt"
    with open(safe_path(out_path), "w", encoding="utf-8") as f:
        f.write(text)
    print(f"  txt: {out_path}")


def process_folder(folder: Path, output_dir: Path) -> None:
    """
    Procesa todos los PDFs de una carpeta.
    Genera <doc_id>.txt por documento y un manifest.json con el mapeo
    doc_id -> nombre_original.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, str] = {}

    pdfs = sorted(folder.glob("*.pdf"))
    for idx, pdf in enumerate(pdfs):
        doc_id = f"doc_{idx:04d}"
        safe_name = pdf.name.encode("ascii", "replace").decode("ascii")
        print(f"\n[{doc_id}] {safe_name}")
        try:
            export(pdf, output_dir, doc_id)
            manifest[doc_id] = pdf.name
        except Exception as e:
            print(f"  ERROR: {e}")

    manifest_path = output_dir / "manifest.json"
    with open(safe_path(manifest_path), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"\nManifiesto guardado en: {manifest_path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Etapa 1: extrae texto limpio de PDFs académicos."
    )
    parser.add_argument("-input", required=True, help="PDF o carpeta con PDFs")
    parser.add_argument("-o", "--output", default="output",
                        help="Directorio de salida (default: output/)")
    parser.add_argument("--id", default=None,
                        help="doc_id manual (solo si -input es un PDF individual)")
    args = parser.parse_args()

    inp = Path(args.input)
    out = Path(args.output)

    if inp.is_file() and inp.suffix.lower() == ".pdf":
        doc_id = args.id or f"doc_{safe_stem(inp)}"
        print(f"Procesando: {inp.name}  →  {doc_id}")
        export(inp, out, doc_id)
        manifest_path = out / "manifest.json"
        existing: dict = {}
        if manifest_path.exists():
            with open(safe_path(manifest_path), encoding="utf-8") as f:
                existing = json.load(f)
        existing[doc_id] = inp.name
        with open(safe_path(manifest_path), "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
    elif inp.is_dir():
        process_folder(inp, out)
    else:
        print(f"Error: '{inp}' no es un PDF ni una carpeta válida.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
