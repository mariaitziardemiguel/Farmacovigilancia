"""
Utilidades de mapeo de terminología médica.

  MedDRA (local v28.1, sin API):
    meddra_code_to_name(code, lang)  → nombre PT en "es" o "en"
    meddra_pt_to_llts(code)          → LLTs del PT en inglés
    MedDRA                           → clase completa (search, hierarchy, etc.)

  RxNorm (API):
    rxnorm_to_inn(drug_name)         → INN en inglés + rxcui
"""

from __future__ import annotations
from pathlib import Path
import unicodedata
import requests

# ─────────────────────────────────────────────
# MedDRA — rutas y helpers de carga
# ─────────────────────────────────────────────

_PIPELINE_DIR = Path(__file__).resolve().parent
_ROOT         = _PIPELINE_DIR.parent.parent
_MEDDRA_BASE  = _ROOT / "data" / "MedDRA v28.1" / "MedDRA v28.1"
_EN           = _MEDDRA_BASE / "English" / "MedAscii"
_ES           = _MEDDRA_BASE / "Spanish" / "MedAscii"

_ENCODING = "latin-1"


def _read_asc(path: Path) -> list[list[str]]:
    rows = []
    with open(path, encoding=_ENCODING) as fh:
        for line in fh:
            line = line.rstrip("\r\n")
            if line:
                rows.append(line.split("$"))
    return rows


def _build_pt_names(pt_path: Path) -> dict[str, str]:
    return {row[0]: row[1] for row in _read_asc(pt_path) if len(row) > 1}


def _build_llt_tables(llt_path: Path) -> tuple[dict[str, str], dict[str, str]]:
    llt_names: dict[str, str] = {}
    llt_to_pt: dict[str, str] = {}
    for row in _read_asc(llt_path):
        if len(row) < 3:
            continue
        llt_names[row[0]] = row[1]
        llt_to_pt[row[0]] = row[2]
    return llt_names, llt_to_pt


def _build_hierarchy(mdhier_path: Path) -> dict[str, dict]:
    hier: dict[str, dict] = {}
    for row in _read_asc(mdhier_path):
        if len(row) < 5:
            continue
        pt_code = row[0]
        if pt_code not in hier:
            hier[pt_code] = {"pt_name": row[4]}
    return hier


def _build_pt_to_llts(llt_path: Path) -> dict[str, list[str]]:
    pt_llts: dict[str, list[str]] = {}
    for row in _read_asc(llt_path):
        if len(row) < 3:
            continue
        pt_llts.setdefault(row[2], []).append(row[0])
    return pt_llts


# ─────────────────────────────────────────────
# Clase MedDRA
# ─────────────────────────────────────────────

class MedDRA:
    """Interfaz de consulta local para MedDRA v28.1 (MedAscii)."""

    def __init__(self, en_dir: Path = _EN, es_dir: Path = _ES) -> None:
        print("[MedDRA] Cargando tablas locales...", end=" ", flush=True)
        self._pt_en: dict[str, str] = _build_pt_names(en_dir / "pt.asc")
        self._pt_es: dict[str, str] = _build_pt_names(es_dir / "pt.asc")
        self._llt_en, self._llt_to_pt_en = _build_llt_tables(en_dir / "llt.asc")
        self._llt_es, self._llt_to_pt_es = _build_llt_tables(es_dir / "llt.asc")
        self._hier: dict[str, dict] = _build_hierarchy(en_dir / "mdhier.asc")
        self._pt_to_llts: dict[str, list[str]] = _build_pt_to_llts(en_dir / "llt.asc")
        print(f"OK ({len(self._pt_en):,} PTs · {len(self._llt_en):,} LLTs)")

    def code_to_name(self, code: str, lang: str = "en") -> str | None:
        pt_names  = self._pt_en  if lang == "en" else self._pt_es
        llt_names = self._llt_en if lang == "en" else self._llt_es
        llt_to_pt = self._llt_to_pt_en if lang == "en" else self._llt_to_pt_es
        if code in pt_names:
            return pt_names[code]
        if code in llt_to_pt:
            pt_code = llt_to_pt[code]
            pt_name = pt_names.get(pt_code)
            if pt_name:
                print(f"    [MedDRA] {code} es LLT → PT padre {pt_code}: '{pt_name}'")
                return pt_name
        print(f"    [MedDRA] WARNING: código {code} no encontrado (lang={lang})")
        return None

    def code_to_names(self, code: str) -> dict[str, str | None]:
        return {"en": self.code_to_name(code, "en"), "es": self.code_to_name(code, "es")}

    def llt_to_pt(self, llt_code: str) -> tuple[str | None, str | None]:
        pt_code = self._llt_to_pt_en.get(llt_code)
        if not pt_code:
            return None, None
        return pt_code, self._pt_en.get(pt_code)

    def is_llt(self, code: str) -> bool:
        return code in self._llt_en and code not in self._pt_en

    def is_pt(self, code: str) -> bool:
        return code in self._pt_en

    def hierarchy(self, pt_code: str) -> dict | None:
        if self.is_llt(pt_code):
            pt_code = self._llt_to_pt_en.get(pt_code, pt_code)
        return self._hier.get(pt_code)

    def pt_to_llts(self, pt_code: str, lang: str = "en") -> list[str]:
        llt_codes = self._pt_to_llts.get(pt_code, [])
        llt_names = self._llt_en if lang == "en" else self._llt_es
        return [llt_names[c] for c in llt_codes if c in llt_names]

    def pt_to_llt_codes(self, pt_code: str) -> list[str]:
        return self._pt_to_llts.get(pt_code, [])

    def search(self, query: str, limit: int = 15) -> list[dict]:
        q = query.strip()
        if len(q) < 2:
            return []

        def _norm(s: str) -> str:
            return unicodedata.normalize("NFD", s.lower()).encode("ascii", "ignore").decode()

        results: list[dict] = []
        seen: set[str] = set()
        q_lower = _norm(q)
        is_code = q.isdigit()

        def _add_pt(code: str) -> None:
            if code in seen or len(results) >= limit:
                return
            seen.add(code)
            results.append({"code": code, "name_en": self._pt_en.get(code, ""),
                            "name_es": self._pt_es.get(code, ""), "type": "PT"})

        def _add_llt(code: str) -> None:
            if code in seen or code in self._pt_en:
                return
            seen.add(code)
            pt_code = self._llt_to_pt_en.get(code, "")
            results.append({"code": code, "name_en": self._llt_en.get(code, ""),
                            "name_es": self._llt_es.get(code, ""), "type": "LLT",
                            "pt_code": pt_code, "pt_name_en": self._pt_en.get(pt_code, "")})

        if is_code:
            for code in self._pt_en:
                if code.startswith(q): _add_pt(code)
                if len(results) >= limit: return results
            for code in self._llt_en:
                if code.startswith(q): _add_llt(code)
                if len(results) >= limit: return results
        else:
            for code, name in self._pt_en.items():
                if q_lower in _norm(name): _add_pt(code)
                if len(results) >= limit: return results
            for code, name in self._pt_es.items():
                if q_lower in _norm(name): _add_pt(code)
                if len(results) >= limit: return results
            for code, name in self._llt_en.items():
                if q_lower in _norm(name): _add_llt(code)
                if len(results) >= limit: return results
            for code, name in self._llt_es.items():
                if q_lower in _norm(name): _add_llt(code)
                if len(results) >= limit: return results

        return results


# ─────────────────────────────────────────────
# Singleton interno
# ─────────────────────────────────────────────

_meddra: MedDRA | None = None

def _get_meddra() -> MedDRA:
    global _meddra
    if _meddra is None:
        _meddra = MedDRA()
    return _meddra


# ─────────────────────────────────────────────
# API pública
# ─────────────────────────────────────────────

def meddra_code_to_name(code: str, lang: str = "en") -> str | None:
    """Dado un código MedDRA (PT o LLT) devuelve el nombre en el idioma indicado."""
    try:
        return _get_meddra().code_to_name(code, lang=lang)
    except Exception as e:
        print(f"    [terminology] WARNING meddra_code_to_name error: {e}")
        return None


def meddra_pt_to_llts(code: str) -> list[str]:
    """Devuelve los LLTs en inglés asociados al PT indicado."""
    try:
        return _get_meddra().pt_to_llts(code, lang="en")
    except Exception as e:
        print(f"    [terminology] WARNING meddra_pt_to_llts error: {e}")
        return []


def rxnorm_to_inn(drug_name: str) -> tuple[str | None, str | None]:
    """Busca el fármaco en RxNorm y devuelve (inn_ingles, rxcui)."""
    try:
        r1 = requests.get(
            "https://rxnav.nlm.nih.gov/REST/rxcui.json",
            params={"name": drug_name, "search": 2},
            timeout=10,
        )
        rxcui_list = r1.json().get("idGroup", {}).get("rxnormId", [])
        if not rxcui_list:
            print(f"    [terminology] WARNING RxNorm: '{drug_name}' no encontrado")
            return None, None
        rxcui = rxcui_list[0]
        r2 = requests.get(
            f"https://rxnav.nlm.nih.gov/REST/rxcui/{rxcui}/properties.json",
            timeout=10,
        )
        inn = r2.json().get("properties", {}).get("name")
        print(f"    [terminology] RxNorm '{drug_name}' → INN='{inn}' rxcui={rxcui}")
        return inn, rxcui
    except Exception as e:
        print(f"    [terminology] WARNING rxnorm_to_inn error: {e}")
        return None, None


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Uso: python terminology.py <codigo_meddra>")
        print("Ejemplo: python terminology.py 10044390")
        sys.exit(1)

    code = sys.argv[1]
    meddra = MedDRA()
    print(f"\nCódigo: {code}")
    print(f"  Tipo : {'LLT' if meddra.is_llt(code) else 'PT' if meddra.is_pt(code) else 'desconocido'}")
    print(f"  EN   : {meddra.code_to_name(code, 'en') or 'no encontrado'}")
    print(f"  ES   : {meddra.code_to_name(code, 'es') or 'no encontrado'}")
    llts = meddra.pt_to_llts(code)
    if llts:
        print(f"\nLLTs ({len(llts)}):")
        for name in llts:
            print(f"  - {name}")
