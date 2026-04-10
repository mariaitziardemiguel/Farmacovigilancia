"""
Módulo de consulta DrugBank para el pipeline de farmacovigilancia.

Requiere haber ejecutado previamente:
    python data/drugbank/extract_drugbank.py

Funciones principales:
    get_inn(drug_name)             → INN en inglés (complementa a RxNorm)
    get_profile(drug_name)         → perfil farmacológico completo
    get_interactions(drug_name)    → interacciones fármaco-fármaco
    get_targets(drug_name)         → dianas moleculares
    get_pgx(drug_name)             → farmacogenética (SNPs)
    get_summary_for_llm(drug_name) → resumen texto para pasar al LLM
"""

import json
import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "drugbank"

# ── Carga perezosa de índices ─────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _lookup() -> dict:
    path = _DATA_DIR / "drugbank_lookup.json"
    if not path.exists():
        logger.warning("DrugBank lookup no encontrado. Ejecuta extract_drugbank.py")
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)

@lru_cache(maxsize=1)
def _profiles() -> dict:
    path = _DATA_DIR / "drugbank_profiles.json"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)

@lru_cache(maxsize=1)
def _interactions() -> dict:
    path = _DATA_DIR / "drugbank_interactions.json"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)

@lru_cache(maxsize=1)
def _targets() -> dict:
    path = _DATA_DIR / "drugbank_targets.json"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)

@lru_cache(maxsize=1)
def _pgx() -> dict:
    path = _DATA_DIR / "drugbank_pgx.json"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ── Resolución de nombre → drugbank_id ───────────────────────────────────────

def _resolve(drug_name: str) -> tuple[str | None, str | None]:
    """
    Dado cualquier nombre (INN, marca, sinónimo) devuelve (drugbank_id, inn).
    Búsqueda case-insensitive.
    """
    entry = _lookup().get(drug_name.strip().lower())
    if entry:
        return entry["drugbank_id"], entry["inn"]
    return None, None


# ── API pública ───────────────────────────────────────────────────────────────

def get_inn(drug_name: str) -> str | None:
    """
    Devuelve el INN en inglés para cualquier nombre de fármaco.
    Devuelve None si no se encuentra.

    Ejemplo:
        get_inn("Refludan")  →  "Lepirudin"
        get_inn("aspirin")   →  "Aspirin"
    """
    _, inn = _resolve(drug_name)
    if inn:
        logger.info("[DrugBank] '%s' → INN '%s'", drug_name, inn)
    else:
        logger.warning("[DrugBank] '%s' no encontrado en lookup", drug_name)
    return inn


def get_profile(drug_name: str) -> dict | None:
    """
    Devuelve el perfil farmacológico completo del fármaco.
    Incluye: mecanismo, indicación, farmacocinética, targets, categorías ATC, etc.
    """
    db_id, _ = _resolve(drug_name)
    if not db_id:
        return None
    return _profiles().get(db_id)


def get_interactions(drug_name: str) -> list[dict]:
    """
    Devuelve la lista de interacciones fármaco-fármaco.
    Cada entrada: {drugbank_id, name, description}
    """
    db_id, _ = _resolve(drug_name)
    if not db_id:
        return []
    return _interactions().get(db_id, [])


def get_targets(drug_name: str) -> list[dict]:
    """
    Devuelve las dianas moleculares del fármaco.
    Cada entrada: {name, organism, actions, gene_symbol, uniprot_id}
    """
    db_id, _ = _resolve(drug_name)
    if not db_id:
        return []
    return _targets().get(db_id, [])


def get_pgx(drug_name: str) -> dict:
    """
    Devuelve datos farmacogenéticos del fármaco.
    Retorna: {snp_effects: [...], snp_adrs: [...]} 
    """
    db_id, _ = _resolve(drug_name)
    if not db_id:
        return {"snp_effects": [], "snp_adrs": []}
    return _pgx().get(db_id, {"snp_effects": [], "snp_adrs": []})


def get_summary_for_llm(drug_name: str) -> str:
    """
    Genera un resumen en texto plano del perfil DrugBank listo para incluir
    en un prompt LLM. Devuelve cadena vacía si no se encuentra el fármaco.

    Incluye solo estas secciones:
    1) mechanism
    2) pharmacodynamics
    3) targets
    4) enzymes
    5) transporters
    6) snp_effects
    7) snp_adrs
    8) pathways
    9) toxicity
    """
    profile = get_profile(drug_name)
    if not profile:
        logger.warning("[DrugBank] '%s' no encontrado — resumen vacío", drug_name)
        return ""

    inn = profile.get("inn", drug_name)
    lines = [f"=== DrugBank: {inn} ({profile.get('drugbank_id', '')}) ==="]

    # mechanism
    mechanism = profile.get("mechanism", "")
    if mechanism:
        lines.append(f"\nmechanism:\n{mechanism}")

    # pharmacodynamics
    pharmacodynamics = profile.get("pharmacodynamics", "")
    if pharmacodynamics:
        lines.append(f"\npharmacodynamics:\n{pharmacodynamics}")

    # targets
    tgts = profile.get("targets", [])
    if tgts:
        lines.append("\ntargets:")
        for t in tgts:
            acts = ", ".join(t.get("actions", []))
            gene = t.get("gene_symbol", "")
            gene_str = f" [{gene}]" if gene else ""
            lines.append(f"  - {t['name']}{gene_str}: {acts}")

    # enzymes
    enzymes = profile.get("enzymes", [])
    if enzymes:
        lines.append("\nenzymes:")
        for e in enzymes:
            acts = ", ".join(e.get("actions", []))
            gene = e.get("gene_symbol", "")
            gene_str = f" [{gene}]" if gene else ""
            lines.append(f"  - {e['name']}{gene_str}: {acts}")

    # transporters
    transporters = profile.get("transporters", [])
    if transporters:
        lines.append("\ntransporters:")
        for tr in transporters:
            acts = ", ".join(tr.get("actions", []))
            gene = tr.get("gene_symbol", "")
            gene_str = f" [{gene}]" if gene else ""
            lines.append(f"  - {tr['name']}{gene_str}: {acts}")

    # snp_effects / snp_adrs (farmacogenetica)
    pgx_data = get_pgx(drug_name)
    snp_effects = pgx_data.get("snp_effects", [])
    snp_adrs = pgx_data.get("snp_adrs", [])
    if snp_effects:
        lines.append("\nsnp_effects:")
        for s in snp_effects:
            lines.append(
                f"  - gene={s.get('gene','?')} rs_id={s.get('rs_id','')} "
                f"description={s.get('description','')}"
            )
    if snp_adrs:
        lines.append("\nsnp_adrs:")
        for s in snp_adrs:
            lines.append(
                f"  - gene={s.get('gene','?')} rs_id={s.get('rs_id','')} "
                f"adverse_reaction={s.get('adverse_reaction','')}"
            )

    # pathways
    pathways = profile.get("pathways", [])
    if pathways:
        lines.append("\npathways:")
        for pw in pathways:
            lines.append(f"  - {pw.get('name','')} [{pw.get('smpdb_id','')}]")

    # toxicity
    toxicity = profile.get("toxicity", "")
    if toxicity:
        lines.append(f"\ntoxicity:\n{toxicity}")

    return "\n".join(lines)


# ── CLI rápido para pruebas ───────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    name = sys.argv[1] if len(sys.argv) > 1 else "Aspirin"
    print(f"\nConsultando DrugBank para: '{name}'")
    print("-" * 60)
    inn = get_inn(name)
    print(f"INN: {inn}")
    print()
    summary = get_summary_for_llm(name)
    print(summary if summary else "(No encontrado en DrugBank)")