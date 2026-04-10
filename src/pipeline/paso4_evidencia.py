import io
import re
import json
import unicodedata
import requests
import boto3
import pdfplumber
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
import time
from datetime import datetime

import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from terminology import rxnorm_to_inn, meddra_code_to_name, meddra_pt_to_llts
import drugbank
from dotenv import load_dotenv

load_dotenv()

INFERENCE_PROFILE_ARN = os.environ["INFERENCE_PROFILE_ARN"]
MODEL_HAIKU  = INFERENCE_PROFILE_ARN
REGION       = INFERENCE_PROFILE_ARN.split(":")[3]

# Reintentos por defecto
MAX_REINTENTOS = 3
TIEMPO_ESPERA = 2  # segundos


def _extract_token_usage(response: dict, response_body: dict) -> tuple[int | None, int | None]:
    usage = response_body.get("usage", {}) if isinstance(response_body, dict) else {}
    in_tokens = usage.get("input_tokens")
    out_tokens = usage.get("output_tokens")

    headers = response.get("ResponseMetadata", {}).get("HTTPHeaders", {}) if isinstance(response, dict) else {}
    if in_tokens is None:
        in_hdr = headers.get("x-amzn-bedrock-input-token-count")
        if in_hdr is not None:
            try:
                in_tokens = int(in_hdr)
            except Exception:
                in_tokens = None
    if out_tokens is None:
        out_hdr = headers.get("x-amzn-bedrock-output-token-count")
        if out_hdr is not None:
            try:
                out_tokens = int(out_hdr)
            except Exception:
                out_tokens = None
    return in_tokens, out_tokens


def _reintentar(func, *args, max_intentos=MAX_REINTENTOS, **kwargs):
    """Ejecuta una función con reintentos automáticos."""
    for intento in range(max_intentos):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            if intento < max_intentos - 1:
                print(f"      ⚠️  Reintento {intento + 1}/{max_intentos} en {TIEMPO_ESPERA}s - Error: {str(e)[:80]}")
                time.sleep(TIEMPO_ESPERA)
            else:
                print(f"      ❌ Falló definitivamente después de {max_intentos} intentos")
                raise


def traducir_farmaco_a_inn(farmaco: str) -> str:
    """Devuelve el INN en inglés del fármaco via RxNorm."""
    inn, _ = rxnorm_to_inn(farmaco)
    return inn or farmaco


def _obtener_rxnorm(farmaco: str) -> tuple:
    """Compatibilidad: devuelve (inn, rxcui) usando terminology.rxnorm_to_inn."""
    inn, rxcui = rxnorm_to_inn(farmaco)
    return (inn or farmaco, rxcui)


def _resolver_farmaco_inn(farmaco: str, farmaco_ya_inn: bool = False) -> tuple[str, str | None]:
    """Resuelve INN una sola vez; si ya viene normalizado evita llamada a RxNorm."""
    if farmaco_ya_inn:
        return farmaco, None
    return _obtener_rxnorm(farmaco)


def traducir_meddra_a_ingles(cod_dedra: str) -> str | None:
    """Devuelve el PT MedDRA en inglés. Delega en terminology.meddra_code_to_name."""
    return meddra_code_to_name(cod_dedra, lang="en")


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _llamar_llm(prompt: str) -> str:
    """Llama a Claude Haiku via AWS Bedrock."""
    client = boto3.client("bedrock-runtime", region_name=REGION)
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 2048,
        "messages": [{"role": "user", "content": prompt}]
    })

    print("\n" + "="*60)
    print(">>> [paso4] USER PROMPT")
    print(prompt)
    print("="*60)

    response = client.invoke_model(modelId=MODEL_HAIKU, body=body)
    response_body = json.loads(response["body"].read())
    input_tokens, output_tokens = _extract_token_usage(response, response_body)
    resultado = response_body["content"][0]["text"].strip()

    print("\n>>> [paso4] RESPUESTA LLM")
    print(resultado)
    print(f">>> [paso4] TOKENS input={input_tokens} output={output_tokens}")
    print("="*60 + "\n")

    return resultado


def _strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text or "") if unicodedata.category(c) != "Mn")


def _contains_any_term(text: str, terms: list[str]) -> bool:
    if not text:
        return False
    text_plain = _strip_accents(text).lower()
    for raw_term in terms or []:
        term = (raw_term or "").strip()
        if not term:
            continue
        # Match flexible spacing and boundaries first.
        pattern = re.compile(
            r"(?<!\\w)" + re.escape(term).replace(r"\ ", r"\\s+") + r"(?!\\w)",
            re.IGNORECASE,
        )
        if pattern.search(text):
            return True
        # Accent-insensitive fallback.
        if _strip_accents(term).lower() in text_plain:
            return True
    return False


def _extract_fragment(text: str, terms: list[str]) -> str:
    if not text:
        return ""
    sentences = re.split(r"(?<=[\.!?])\s+", text)
    for s in sentences:
        if _contains_any_term(s, terms):
            return s.strip()
    return ""


# ─────────────────────────────────────────────
# 1. OpenFDA (FAERS)
# ─────────────────────────────────────────────

def _consultar_faers(farmaco: str, reaccion_en: str) -> int | None:
    """Devuelve el total de casos o None si no hay resultados (404)."""
    url = "https://api.fda.gov/drug/event.json"
    params = {
        "search": f'patient.drug.medicinalproduct:"{farmaco}" AND patient.reaction.reactionmeddrapt:"{reaccion_en}"',
        "limit": "1"
    }
    resp = requests.get(url, params=params, timeout=10)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json().get("meta", {}).get("results", {}).get("total", 0)


def buscar_openfda(farmaco: str, reaccion: str, cod_dedra: str = None, farmaco_ya_inn: bool = False, llts_en: list[str] | None = None) -> tuple:
    """
    Consulta OpenFDA FAERS buscando por PT y todos sus LLTs (sinónimos MedDRA).
    Normaliza el fármaco a INN en inglés via RxNorm.
    Devuelve texto con desglose por término y refs con totales.
    """
    print("    [FDA] Consultando OpenFDA FAERS...")

    # Normalizar fármaco a INN inglés (o usarlo tal cual si ya llega normalizado)
    farmaco_en, _ = _resolver_farmaco_inn(farmaco, farmaco_ya_inn)
    farmaco_en = farmaco_en or farmaco
    print(f"    [FDA] Fármaco normalizado: '{farmaco}' → '{farmaco_en}'")

    # PT en inglés: usar lo recibido; resolver por MedDRA solo si falta
    reaccion_pt = (reaccion or "").strip() or None
    if not reaccion_pt and cod_dedra:
        reaccion_pt = traducir_meddra_a_ingles(str(cod_dedra))
    reaccion_pt = reaccion_pt or reaccion
    print(f"    [FDA] PT: '{reaccion_pt}'")

    # LLTs: usar los recibidos; calcular por MedDRA solo como fallback
    llts = [str(x).strip() for x in (llts_en or []) if str(x).strip()]
    if not llts and cod_dedra:
        llts = meddra_pt_to_llts(str(cod_dedra))
        print(f"    [FDA] LLTs encontrados: {len(llts)}")

    try:
        # Buscar PT
        total_pt = _consultar_faers(farmaco_en, reaccion_pt) or 0
        print(f"    [FDA] PT '{reaccion_pt}': {total_pt} casos")

        # Buscar cada LLT
        resultados_llt = []
        for llt in llts:
            if llt.lower() == reaccion_pt.lower():
                continue
            n = _consultar_faers(farmaco_en, llt)
            if n:
                resultados_llt.append({"termino": llt, "total": n})
                print(f"    [FDA]   LLT '{llt}': {n} casos")

        total_combinado = total_pt + sum(r["total"] for r in resultados_llt)

        # Construir texto resumen
        lineas = [
            f"PT '{reaccion_pt}': {total_pt} casos",
        ]
        if resultados_llt:
            lineas.append("LLTs (sinónimos MedDRA):")
            for r in resultados_llt:
                lineas.append(f"  '{r['termino']}': {r['total']} casos")
        lineas.append(f"Total combinado (PT + LLTs): {total_combinado} casos")

        texto = "\n".join(lineas)
        refs = {
            "farmaco_en": farmaco_en,
            "pt": reaccion_pt,
            "total_pt": total_pt,
            "llts": resultados_llt,
            "total_combinado": total_combinado,
        }
        print(f"    [FDA] OK — total combinado: {total_combinado} casos")
        return texto, refs

    except Exception as e:
        print(f"    [FDA] WARNING Error: {e}")
        return "Numero de casos notificados = No disponible", {}


# ─────────────────────────────────────────────
# 2. PubMed
# ─────────────────────────────────────────────

import xml.etree.ElementTree as ET

def _pubmed_esearch(term: str, n: int) -> tuple:
    """Devuelve (ids, total_encontrados, querytranslation)."""
    try:
        resp = requests.get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
            params={"db": "pubmed", "term": term, "retmode": "json", "retmax": str(n), "sort": "relevance"},
            timeout=10
        )
        result = resp.json().get("esearchresult", {})
        return (
            result.get("idlist", []),
            result.get("count", "0"),
            result.get("querytranslation", ""),
        )
    except Exception as e:
        print(f"    [PubMed] ERROR esearch: {e}")
        return [], "0", ""


def _pubmed_efetch(ids: list) -> list:
    """Descarga título + abstract de los IDs dados vía XML. Devuelve lista de dicts."""
    if not ids:
        return []
    try:
        resp = requests.get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
            params={"db": "pubmed", "id": ",".join(ids), "rettype": "abstract", "retmode": "xml"},
            timeout=15
        )
        root = ET.fromstring(resp.content)
        articulos = []
        for art in root.findall(".//PubmedArticle"):
            pmid  = art.findtext(".//PMID", "").strip()
            title = art.findtext(".//ArticleTitle", "").strip()
            abstract_parts = [n.text or "" for n in art.findall(".//AbstractText")]
            abstract = " ".join(p.strip() for p in abstract_parts if p.strip())
            if title or abstract:
                articulos.append({"pmid": pmid, "titulo": title, "abstract": abstract})
        return articulos
    except Exception as e:
        print(f"    [PubMed] ERROR efetch: {e}")
        return []


def buscar_pubmed(farmaco: str, reaccion: str, sexo: str, raza: str, edad: str, cod_dedra: str = None, farmaco_ya_inn: bool = False, llts_en: list[str] | None = None) -> tuple:
    """
    Búsqueda en PubMed: 3 secciones temáticas.
    - Máximo 1 artículo por sección.
    - Si una sección no tiene resultados, se queda vacía (sin resultados).
    - Los slots de secciones vacías se usan para mostrar un artículo extra
      EN LA SECCIÓN QUE SÍ TIENE MÁS resultados (no se mueven entre categorías).
    Devuelve (dict_secciones, refs).
    """
    print("    [PubMed] Búsqueda en 3 secciones temáticas...")

    # Normalizar fármaco (o usarlo tal cual si ya llega normalizado)
    farmaco_inn, _ = _resolver_farmaco_inn(farmaco, farmaco_ya_inn)
    if farmaco_inn != farmaco:
        print(f"    [PubMed] Fármaco: '{farmaco}' → '{farmaco_inn}'")

    # RAM en inglés: usar lo recibido; resolver por MedDRA solo si falta
    reaccion_en = (reaccion or "").strip()
    if (not reaccion_en) and cod_dedra:
        reaccion_en_tmp = traducir_meddra_a_ingles(str(cod_dedra))
        if reaccion_en_tmp:
            reaccion_en = reaccion_en_tmp
            print(f"    [PubMed] RAM (EN): '{reaccion_en}'")

    llts = [str(x).strip() for x in (llts_en or []) if str(x).strip()]
    if not llts and cod_dedra:
        llts = [llt for llt in meddra_pt_to_llts(str(cod_dedra)) if llt and llt.lower() != reaccion_en.lower()]
        print(f"    [PubMed] LLTs para repetir búsqueda: {len(llts)}")

    terminos_busqueda = [reaccion_en] + llts

    busquedas_base = {
        "casos_reportados": '(case OR "case report" OR "case series")',
        "factores_riesgo":  '(risk OR susceptibility OR predisposing OR "risk factor")',
        "recomendaciones":  '(management OR prevention OR monitoring OR guideline)',
    }

    # Paso 1: esearch con retmax=2 para cada término y cada bloque temático
    datos = {}
    for clave, filtro in busquedas_base.items():
        ids_unicos = []
        vistos = set()
        origenes_por_pmid = defaultdict(list)
        detalles_terminos = []
        total_estimado = 0
        terminos_sql = " OR ".join([f'"{t}"' for t in terminos_busqueda])

        for termino in terminos_busqueda:
            query = f'{farmaco_inn} AND "{termino}" AND {filtro}'
            ids, total, qt = _pubmed_esearch(query, 2)
            total_estimado += int(total)
            detalles_terminos.append({"termino": termino, "query": query, "total": int(total), "qt": qt, "ids": ids})
            for pmid in ids:
                if pmid in vistos:
                    if termino not in origenes_por_pmid[pmid]:
                        origenes_por_pmid[pmid].append(termino)
                    continue
                vistos.add(pmid)
                ids_unicos.append(pmid)
                origenes_por_pmid[pmid].append(termino)

        datos[clave] = {
            "ids": ids_unicos,
            "n_encontrados": total_estimado,
            "query": f'{farmaco_inn} AND ({terminos_sql}) AND {filtro}',
            "qt": "; ".join(f"{d['termino']}: {d['total']}" for d in detalles_terminos),
            "terminos_busqueda": terminos_busqueda,
            "detalle_terminos": detalles_terminos,
            "origenes_por_pmid": {pmid: origenes for pmid, origenes in origenes_por_pmid.items()},
        }
        print(f"    [PubMed] {clave}: {total_estimado} encontrados acumulados, {len(ids_unicos)} IDs únicos")

    # Paso 2: asignación — 1 artículo a cada sección con IDs disponibles
    # Los slots de secciones vacías se dan a la(s) sección(es) con más resultados,
    # siempre que tengan un segundo ID disponible, y se muestran EN SU PROPIA SECCIÓN.
    asignacion = {k: min(1, len(d["ids"])) for k, d in datos.items()}
    slots_libres = sum(1 for v in asignacion.values() if v == 0)
    if slots_libres > 0:
        # Candidatas: secciones con al menos 2 IDs (pueden dar un artículo extra)
        candidatas = sorted(
            [k for k, d in datos.items() if len(d["ids"]) >= 2],
            key=lambda k: datos[k]["n_encontrados"],
            reverse=True,
        )
        for clave_extra in candidatas[:slots_libres]:
            asignacion[clave_extra] = 2
            print(f"    [PubMed] Slot extra → '{clave_extra}' mostrará 2 artículos")

    # Paso 3: descargar artículos
    secciones = {}
    ids_vistos = set()
    for clave, d in datos.items():
        ids_usar = d["ids"][:asignacion[clave]]
        articulos = _pubmed_efetch(ids_usar) if ids_usar else []
        ids_vistos.update(ids_usar)

        articulos_por_pmid = {a["pmid"]: a for a in articulos}
        articulos_enriquecidos = []
        for pmid in ids_usar:
            art = dict(articulos_por_pmid.get(pmid, {"pmid": pmid, "titulo": "", "abstract": ""}))
            origenes = d["origenes_por_pmid"].get(pmid, [])
            art["llt_origenes"] = origenes
            articulos_enriquecidos.append(art)

        secciones[clave] = {
            "n_encontrados":  d["n_encontrados"],
            "n_descargados":  len(articulos_enriquecidos),
            "ids":            ids_usar,
            "urls":           [f"https://pubmed.ncbi.nlm.nih.gov/{p}/" for p in ids_usar],
            "texto":          "\n\n".join(
                                  f"PMID: {a['pmid']}\nTitle: {a['titulo']}\nAbstract: {a['abstract']}"
                                  + (f"\nOrigen: {', '.join(a.get('llt_origenes', []))}" if a.get('llt_origenes') else "")
                                  for a in articulos_enriquecidos
                              ) if articulos_enriquecidos else "Sin resultados para esta búsqueda.",
            "articulos":      articulos_enriquecidos,
            "query":          d["query"],
            "search_details": d["qt"],
            "terminos_busqueda": d["terminos_busqueda"],
        }

    # Fallback si ninguna sección devolvió artículos
    if not ids_vistos:
        print(f"    [PubMed] Sin resultados → búsqueda general")
        query_general = f'{farmaco_inn} AND "{reaccion_en}"'
        ids_gen, total_gen, qt_gen = _pubmed_esearch(query_general, 3)
        articulos_gen = _pubmed_efetch(ids_gen)
        ids_vistos.update(ids_gen)
        secciones["asociacion_general"] = {
            "n_encontrados":  int(total_gen),
            "n_descargados":  len(articulos_gen),
            "ids":            ids_gen,
            "urls":           [f"https://pubmed.ncbi.nlm.nih.gov/{p}/" for p in ids_gen],
            "texto":          "\n\n".join(
                                  f"PMID: {a['pmid']}\nTitle: {a['titulo']}\nAbstract: {a['abstract']}"
                                  for a in articulos_gen
                              ) if articulos_gen else "Sin resultados.",
            "articulos":      articulos_gen,
            "query":          query_general,
            "search_details": qt_gen,
        }

    refs = [{"pmid": p, "url": f"https://pubmed.ncbi.nlm.nih.gov/{p}/"} for p in ids_vistos]
    print(f"    [PubMed] OK — {len(ids_vistos)} artículos")
    return secciones, refs





# ─────────────────────────────────────────────
# 4. PRAC / EMA
# ─────────────────────────────────────────────

def buscar_prac(farmaco: str, reaccion: str, cod_dedra: str = None, farmaco_ya_inn: bool = False, llts_en: list[str] | None = None) -> tuple:
    """
    Busca en prac_signals.json por INN del fármaco.
    Para cada sección 4 que lo mencione, indica si la RAM (PT o LLTs) también aparece.
    Devuelve el texto encontrado para que sea visible en el informe.
    """
    import re
    from pathlib import Path

    SIGNALS_JSON = Path(__file__).resolve().parent.parent.parent / "data" / "prac_signals.json"

    if not SIGNALS_JSON.exists():
        print("    [PRAC] WARNING No se encontró prac_signals.json")
        return "Datos PRAC/EMA no disponibles (ejecuta extract_prac_signals.py).", []

    with open(SIGNALS_JSON, encoding="utf-8") as f:
        entradas = json.load(f)

    # Normalizar fármaco a INN en inglés (o usarlo tal cual si ya llega normalizado)
    inn, _ = _resolver_farmaco_inn(farmaco, farmaco_ya_inn)
    inn = inn or farmaco
    print(f"    [PRAC] Fármaco normalizado: '{farmaco}' → '{inn}'")

    # Obtener PT y LLTs en inglés para la RAM (priorizar lo ya normalizado)
    reaccion_en = (reaccion or "").strip()
    if (not reaccion_en) and cod_dedra:
        pt_en = traducir_meddra_a_ingles(str(cod_dedra))
        if pt_en:
            reaccion_en = pt_en
    llts = [str(x).strip() for x in (llts_en or []) if str(x).strip()]
    if not llts and cod_dedra:
        llts = meddra_pt_to_llts(str(cod_dedra))
    terminos_ram = [reaccion_en] + [l for l in llts if l.lower() != reaccion_en.lower()]
    print(f"    [PRAC] RAM: '{reaccion_en}' + {len(llts)} LLTs")

    # Buscar entradas que mencionen el INN
    patron_inn = re.compile(re.escape(inn), re.IGNORECASE)
    coincidencias = [e for e in entradas if e.get("section4") and patron_inn.search(e["section4"])]
    print(f"    [PRAC] Entradas con '{inn}': {len(coincidencias)} de {len(entradas)}")

    if not coincidencias:
        return f"La RAM '{reaccion_en}' no aparece en las actas PRAC analizadas.", []

    patron_subseccion = re.compile(r'(?=(?:^|\n)4\.\d+(?:\.\d+)*\.?\s)', re.MULTILINE)

    # Recopilar subsecciones del fármaco y separar: match directo (regex) vs fallback LLM.
    directas = []
    tareas_llm = []  # (filename, fecha, fuente, subseccion_texto)
    for entrada in coincidencias:
        section4 = entrada["section4"]
        fecha    = entrada.get("date", "sin fecha")
        fuente   = entrada.get("source", "")
        filename = entrada.get("filename", "")

        partes = [p.strip() for p in patron_subseccion.split(section4) if p.strip()]
        if not partes:
            partes = [section4]

        for parte in partes:
            if patron_inn.search(parte):
                if _contains_any_term(parte, terminos_ram):
                    directas.append((filename, fecha, fuente, parte))
                else:
                    tareas_llm.append((filename, fecha, fuente, parte))

    print(
        f"    [PRAC] Subsecciones con '{inn}': {len(directas) + len(tareas_llm)} "
        f"(regex directas: {len(directas)}, fallback LLM: {len(tareas_llm)})"
    )

    def _evaluar_subseccion(tarea):
        filename, fecha, fuente, subseccion = tarea
        prompt = f"""Subsección de un acta del PRAC (EMA):

{subseccion}

¿Menciona la siguiente reacción adversa?
RAM: {reaccion_en}

Responde SOLO con este JSON (sin markdown):
{{"ram_presente": true/false, "fragmento": "frase exacta donde aparece la RAM, o null si no aparece"}}"""
        try:
            respuesta = _llamar_llm(prompt)
            datos_llm = json.loads(respuesta)
        except Exception:
            import re as _re
            m = _re.search(r'\{[\s\S]*\}', respuesta if 'respuesta' in dir() else '{}')
            try:
                datos_llm = json.loads(m.group(0)) if m else {"ram_presente": False, "fragmento": None}
            except Exception:
                datos_llm = {"ram_presente": False, "fragmento": None}
        return filename, fecha, fuente, subseccion, datos_llm

    # Llamadas LLM en paralelo solo para subsecciones sin match directo.
    from concurrent.futures import ThreadPoolExecutor, as_completed
    resultados_llm = []
    if tareas_llm:
        with ThreadPoolExecutor(max_workers=6) as executor:
            futuros = {executor.submit(_evaluar_subseccion, t): t for t in tareas_llm}
            for futuro in as_completed(futuros):
                try:
                    resultados_llm.append(futuro.result())
                except Exception as e:
                    print(f"    [PRAC] Error LLM subsección: {e}")

    # Agrupar por documento y construir output
    from collections import defaultdict
    por_documento = defaultdict(list)

    # 1) Coincidencias directas por regex (sin LLM)
    for filename, fecha, fuente, subseccion in directas:
        fragmento = _extract_fragment(subseccion, terminos_ram) or subseccion
        por_documento[(filename, fecha, fuente)].append(fragmento)

    # 2) Fallback LLM solo si regex no encontró la RAM
    for filename, fecha, fuente, subseccion, datos_llm in resultados_llm:
        if datos_llm.get("ram_presente"):
            fragmento = datos_llm.get("fragmento") or subseccion
            por_documento[(filename, fecha, fuente)].append(fragmento)

    bloques = []
    refs = []
    for (filename, fecha, fuente), fragmentos in por_documento.items():
        bloque = f"── {fuente.upper()} | {fecha} | {filename}\n\n" + "\n\n".join(fragmentos)
        bloques.append(bloque)
        refs.append({"filename": filename, "date": fecha, "source": fuente, "ram_encontrada": True})

    # Documentos donde el fármaco aparece pero el LLM no encontró RAM
    docs_con_farm = {(e.get("filename"), e.get("date", ""), e.get("source", "")) for e in coincidencias}
    docs_con_ram  = {(r["filename"], r["date"], r["source"]) for r in refs}
    for filename, fecha, fuente in docs_con_farm - docs_con_ram:
        refs.append({"filename": filename, "date": fecha, "source": fuente, "ram_encontrada": False})

    separador = "\n\n" + "─" * 60 + "\n\n"
    n_con_ram = sum(1 for r in refs if r["ram_encontrada"])
    texto_final = separador.join(bloques) if bloques else ""
    print(
        f"    [PRAC] OK — {len(coincidencias)} docs con '{inn}', "
        f"{len(directas)} subsecciones directas por regex, {n_con_ram} docs con RAM confirmada total"
    )
    return texto_final, refs




# ─────────────────────────────────────────────
# 6. DrugBank
# ─────────────────────────────────────────────

def buscar_drugbank(farmaco: str, farmaco_ya_inn: bool = False) -> tuple:
    """
    Consulta DrugBank: mecanismo de acción, dianas moleculares y enzimas CYP.
    Devuelve (texto_mecanismo_y_dianas, refs).
    """
    inicio = time.time()
    print(f"\n  [DrugBank] Iniciando búsqueda para '{farmaco}'")

    try:
        inn, _ = _resolver_farmaco_inn(farmaco, farmaco_ya_inn)
        print(f"      → INN resuelto: {inn}")

        profile = drugbank.get_profile(inn)

        if not profile:
            print(f"      Sin resultados para '{inn}'")
            return f"No se encontró información en DrugBank para '{inn}'.", []

        texto = drugbank.get_summary_for_llm(inn)
        targets = drugbank.get_targets(inn) or []
        enzymes = profile.get("enzymes", [])
        pathways = profile.get("pathways", [])

        refs = [{"farmaco": inn, "targets": len(targets),
                 "url": f"https://go.drugbank.com/drugs?name={inn}"}]

        duracion = time.time() - inicio
        print(f"      ✓ OK: {len(targets)} dianas | {len(enzymes)} enzimas | {len(pathways)} rutas | {duracion:.2f}s")
        return texto, refs

    except Exception as e:
        duracion = time.time() - inicio
        print(f"       Error: {e} | {duracion:.2f}s")
        return f"Error al consultar DrugBank: {e}", []


# ─────────────────────────────────────────────
# 7. Reactome
# ─────────────────────────────────────────────

def buscar_reactome(farmaco: str, farmaco_ya_inn: bool = False) -> tuple:
    """
    Busca pathways humanos en Reactome por nombre de fármaco.
    Filtra exclusivamente a Homo sapiens.
    Devuelve (texto, refs).
    """
    inicio = time.time()
    inn, _ = _resolver_farmaco_inn(farmaco, farmaco_ya_inn)
    inn = inn or farmaco
    print(f"\n  [Reactome] Buscando pathways para '{inn}'...")

    try:
        resp = requests.get(
            "https://reactome.org/ContentService/search/query",
            params={"query": inn, "types": "Pathway", "cluster": "true", "rows": 25},
            timeout=6,
        )
        resp.raise_for_status()

        pathways = []
        for group in resp.json().get("results", []):
            for entry in group.get("entries", []):
                species = entry.get("species", [])
                if "Homo sapiens" not in species:
                    continue
                pathways.append({
                    "stId":    entry.get("stId", ""),
                    "name":    entry.get("name", ""),
                    "url":     f"https://reactome.org/PathwayBrowser/#/{entry.get('stId','')}",
                })

        duracion = time.time() - inicio
        print(f"      ✓ {len(pathways)} pathways humanos | {duracion:.2f}s")

        if not pathways:
            return f"No se encontraron pathways en Reactome para '{inn}'.", []

        lineas = [f"Pathways Reactome (Homo sapiens) para {inn}:"]
        for p in pathways:
            lineas.append(f"  [{p['stId']}] {p['name']}")
        return "\n".join(lineas), pathways

    except Exception as e:
        duracion = time.time() - inicio
        msg = "Reactome no accesible (timeout de red)" if "timeout" in str(e).lower() or "timed out" in str(e).lower() else f"Error Reactome: {str(e)[:80]}"
        print(f"      ❌ {msg} | {duracion:.2f}s")
        return msg, []


# ─────────────────────────────────────────────
# 8. PharmGKB + CPIC (integrados)
# ─────────────────────────────────────────────

from collections import defaultdict

_NIVELES_ALTOS  = {"1A", "1B", "2A", "2B"}
_ORDEN_NIVEL    = {"1A": 0, "1B": 1, "2A": 2, "2B": 3}


@lru_cache(maxsize=256)
def _pgkb_buscar_drug_id_por_nombre(nombre: str) -> str | None:
    try:
        resp = requests.get(
            "https://api.pharmgkb.org/v1/data/drug",
            params={"name": nombre, "view": "min"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
        if not data:
            return None

        return data[0].get("id")
    except Exception as e:
        print(f"      ❌ PharmGKB lookup error for '{nombre}': {e}")
        return None


@lru_cache(maxsize=256)
def _pgkb_obtener_anotaciones(drug_id: str) -> list[dict]:
    try:
        resp = requests.get(
            "https://api.pharmgkb.org/v1/data/clinicalAnnotation",
            params={"relatedChemicals.accessionId": drug_id, "view": "base"},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get("data", [])
    except Exception as e:
        print(f"      ❌ PharmGKB annotations error for '{drug_id}': {e}")
        return []


def _pgkb_get_nivel(ann: dict) -> str:
    return ann.get("levelOfEvidence", {}).get("term", "")


def _pgkb_es_nivel_3(ann: dict) -> bool:
    nivel = _pgkb_get_nivel(ann).strip()
    return nivel == "3" or nivel.startswith("3")


def _pgkb_extraer_anotacion(ann: dict) -> dict:
    nivel   = _pgkb_get_nivel(ann)
    loc     = ann.get("location", {})
    gen     = loc["genes"][0].get("symbol", "") if loc.get("genes") else ""

    alelo_fenotipos = [
        {"alelo": ap["allele"], "fenotipo": ap["phenotype"]}
        for ap in ann.get("allelePhenotypes", [])
        if not ap.get("limitedEvidence")
    ]
    enfermedades = [d["name"] for d in ann.get("relatedDiseases", [])]
    tiene_guias  = bool(ann.get("relatedGuidelines", []))
    tiene_etiq   = bool(ann.get("relatedLabels", []))

    return {
        "nivel_evidencia": nivel,
        "gen": gen,
        "alelo_fenotipos": alelo_fenotipos,
        "enfermedades": enfermedades,
        "tiene_guias_cpic_dpwg": tiene_guias,
        "tiene_etiquetas_fda_ema": tiene_etiq,
        "contextos_clinicos": ann.get("types", []),
    }


def _pgkb_consolidar_genes(anotaciones: list) -> list:
    """Agrupa anotaciones por gen y produce una entrada compacta por gen."""
    por_gen = defaultdict(list)
    for ann in anotaciones:
        por_gen[ann["gen"] or "?"].append(ann)

    entries = []

    for gen, anns in por_gen.items():
        niveles_v = [a["nivel_evidencia"] for a in anns if a["nivel_evidencia"] in _ORDEN_NIVEL]
        nivel_max = min(niveles_v, key=lambda x: _ORDEN_NIVEL[x]) if niveles_v else \
                    next((a["nivel_evidencia"] for a in anns if a["nivel_evidencia"]), "desconocido")

        # Recoger pares alelo-fenotipo unicos
        vistos: set = set()
        alelo_fenotipos = []
        for ann in anns:
            for fp in ann["alelo_fenotipos"]:
                key = (fp["alelo"], fp["fenotipo"])
                if key not in vistos:
                    vistos.add(key)
                    alelo_fenotipos.append({"alelo": fp["alelo"], "fenotipo": fp["fenotipo"]})

        contextos_clinicos = sorted({c for a in anns for c in a.get("contextos_clinicos", []) if c})

        entries.append({
            "gen": gen,
            "nivel_evidencia": nivel_max,
            "alelo_fenotipos": alelo_fenotipos[:8],
            "tiene_guias_cpic_dpwg": any(a["tiene_guias_cpic_dpwg"] for a in anns),
            "tiene_etiquetas_fda_ema": any(a["tiene_etiquetas_fda_ema"] for a in anns),
            "enfermedades": list(dict.fromkeys(e for a in anns for e in a["enfermedades"])),
            "contextos_clinicos": contextos_clinicos,
        })

    entries.sort(key=lambda x: _ORDEN_NIVEL.get(x["nivel_evidencia"], 99))
    return entries


def buscar_pharmgkb(farmaco: str, farmaco_ya_inn: bool = False) -> tuple:
    """
    Consulta PharmGKB + CPIC para información farmacogenética consolidada por gen.
    Devuelve (dict_estructurado, refs).
    """
    inicio = time.time()
    print(f"\n  [PharmGKB+CPIC] Iniciando búsqueda para '{farmaco}'")

    inn, rxcui = _resolver_farmaco_inn(farmaco, farmaco_ya_inn)
    farmaco_inn = inn or farmaco
    print(f"      → INN: {farmaco_inn} | rxcui: {rxcui}")

    # 1. Obtener drug_id de PharmGKB
    drug_id = None
    for candidato in (farmaco_inn, farmaco):
        candidato = (candidato or "").strip()
        if not candidato:
            continue
        drug_id = _pgkb_buscar_drug_id_por_nombre(candidato)
        if drug_id:
            print(f"      → PharmGKB drug_id: {drug_id} ({candidato})")
            break

    if not drug_id:
        print(f"      ℹ '{farmaco_inn}' no encontrado en PharmGKB")

    # 2. Anotaciones clínicas
    anotaciones_raw = []
    if drug_id:
        todas = _pgkb_obtener_anotaciones(drug_id)
        if todas:
            altas = [a for a in todas if _pgkb_get_nivel(a) in _NIVELES_ALTOS]
            nivel_3 = [a for a in todas if _pgkb_es_nivel_3(a)]
            if altas:
                seleccion = altas
            else:
                seleccion = []
                genes_vistos = set()
                for ann in nivel_3:
                    gen = ann.get("location", {}).get("genes", [{}])[0].get("symbol", "") if ann.get("location", {}).get("genes") else ""
                    clave_gen = gen or "?"
                    if clave_gen in genes_vistos:
                        continue
                    genes_vistos.add(clave_gen)
                    seleccion.append(ann)
                    if len(seleccion) >= 2:
                        break
            anotaciones_raw = [_pgkb_extraer_anotacion(a) for a in seleccion]
            print(f"      → {len(todas)} anotaciones totales, {len(altas)} altas, {len(nivel_3)} nivel 3, {len(anotaciones_raw)} usadas")

    # 4. Consolidar por grupos de evidencia
    ann_fuertes     = [a for a in anotaciones_raw if a["nivel_evidencia"] in {"1A", "1B"}]
    ann_secundarias = [a for a in anotaciones_raw if a["nivel_evidencia"] in {"2A", "2B"}]
    ann_baja        = [a for a in anotaciones_raw if a["nivel_evidencia"] not in {"1A", "1B", "2A", "2B"}]

    resultado = {
        "farmaco": farmaco_inn,
        "farmacogenetica": {
            "evidencia_fuerte_1A_1B":    _pgkb_consolidar_genes(ann_fuertes),
            "evidencia_secundaria_2A_2B": _pgkb_consolidar_genes(ann_secundarias),
            "evidencia_baja_referencia": _pgkb_consolidar_genes(ann_baja) if ann_baja else None,
        }
    }

    refs = [{"farmaco": farmaco_inn, "pharmgkb_id": drug_id,
             "url": f"https://www.pharmgkb.org/drug/{drug_id}" if drug_id else "https://www.pharmgkb.org"}]

    duracion = time.time() - inicio
    print(f"      ✓ OK: {len(ann_fuertes)} genes fuertes | {len(ann_secundarias)} secundarios | {duracion:.2f}s")
    return resultado, refs


# ─────────────────────────────────────────────
# CIMA (AEMPS) — búsqueda por principio activo
# ─────────────────────────────────────────────

_CIMA_URL = "https://cima.aemps.es/cima/rest/medicamentos"

_PATRON_FT = (
    r"(4\.3\.?\s*Contraindicaciones[\s\S]*?(?=4\.4\.?\s*Advertencias))"
    r"|(4\.4\.?\s*Advertencias y precauciones especiales de empleo[\s\S]*?(?=4\.5\.?\s*Interacci[oó]n))"
    r"|(4\.5\.?\s*Interacci[oó]n con otros medicamentos y otras formas de interacci[oó]n"
    r"[\s\S]*?(?=4\.6\.?\s*Fertilidad, embarazo y lactancia))"
    r"|(4\.8\.?\s*Reacciones adversas[\s\S]*?(?=4\.9\.?\s*Sobredosis))"
    r"|(4\.9\.?\s*Sobredosis[\s\S]*?(?=5\.?\s*PROPIEDADES FARMACOL[OÓ]GICAS|5\.?\s*[Pp]ropiedades farmacol[oó]gicas))"
    r"|(5\.1\.?\s*Propiedades farmacodin[aá]micas[\s\S]*?(?=5\.2\.?\s*Propiedades farmacocin[eé]ticas))"
    r"|(5\.2\.?\s*Propiedades farmacocin[eé]ticas[\s\S]*?(?=5\.3\.?\s*Datos precl[ií]nicos sobre seguridad))"
)


def buscar_cima(
    farmaco_nombre: str,
    farmaco_inn: str | None = None,
    reaccion: str = "",
) -> tuple:
    """
    Busca la ficha técnica en CIMA.
            1. Solo por nombre comercial en castellano (farmaco_nombre)
        Si se indica reacción:
                        1) intenta match directo por regex y evita LLM si hay match
            2) si no hay match directo, usa LLM como fallback
            3) si LLM dice que no, no aporta texto al informe
    Devuelve (texto_ft, [{"ft_url": url, "nombre_oficial": nombre}]).
    """
    print("\n" + "="*60)
    print(">>> [CIMA] Búsqueda ficha técnica")
    print(f"    Nombre (castellano): {farmaco_nombre}")

    try:
        resp = requests.get(
            _CIMA_URL,
            params={"nombre": farmaco_nombre.strip(), "comerc": "1", "autorizados": "1"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        print(f"    Por nombre → {data.get('totalFilas', 0)} resultados")

        if not data.get("resultados"):
            print("    ⚠️  Sin resultados en CIMA")
            return "Sin resultados en CIMA para este medicamento.", []

        medicamento = data["resultados"][0]
        nombre_oficial = medicamento.get("nombre", farmaco_nombre)
        docs = medicamento.get("docs", [])
        if not docs:
            print("    ⚠️  Sin documentos en CIMA")
            return "Ficha técnica no disponible en CIMA.", []

        ft_url = docs[0].get("url", "")
        print(f"    Medicamento: {nombre_oficial}")
        print(f"    URL ficha técnica: {ft_url}")

        # Descargar y extraer PDF
        pdf_resp = requests.get(ft_url, timeout=30)
        pdf_resp.raise_for_status()

        with pdfplumber.open(io.BytesIO(pdf_resp.content)) as pdf:
            texto_completo = " ".join(page.extract_text() or "" for page in pdf.pages)

        texto_completo = re.sub(r"\r?\n+", " ", texto_completo)
        texto_completo = re.sub(r"\s{2,}", " ", texto_completo).strip()
        print(f"    PDF extraído: {len(texto_completo)} caracteres")

        matches = re.findall(_PATRON_FT, texto_completo, flags=re.IGNORECASE)
        matches = [m for grupo in matches for m in grupo if m]

        if matches:
            texto_ft = "\n\n---\n\n".join(m.strip() for m in matches)
        else:
            texto_ft = texto_completo

        print(f"    ✅ Texto extraído: {len(texto_ft)} caracteres")

        # Validación de RAM en CIMA: regex primero, LLM solo fallback.
        if reaccion:
            terminos_ram = [reaccion]
            ram_directa = _contains_any_term(texto_ft, terminos_ram)

            if ram_directa:
                fragmento = _extract_fragment(texto_ft, terminos_ram)
                print(f"    [CIMA] RAM '{reaccion}' encontrada por regex directa")
                if fragmento:
                    print(f"    [CIMA] Fragmento detectado: {fragmento[:240]}")
            else:
                print(f"    [CIMA] Sin match directo por regex para '{reaccion}'. Fallback LLM...")
                prompt_cima = f"""Ficha técnica (CIMA/AEMPS):

{texto_ft[:8000]}

¿Aparece la siguiente reacción adversa en este texto?
RAM: {reaccion}

Responde SOLO con este JSON (sin markdown):
{{"ram_presente": true/false, "fragmento": "frase exacta del texto donde aparece la RAM, o null si no aparece"}}"""

                try:
                    respuesta_cima = _llamar_llm(prompt_cima)
                    datos_cima = json.loads(respuesta_cima)
                except Exception:
                    m = re.search(r'\{[\s\S]*\}', respuesta_cima if 'respuesta_cima' in dir() else '{}')
                    try:
                        datos_cima = json.loads(m.group(0)) if m else {"ram_presente": False, "fragmento": None}
                    except Exception:
                        datos_cima = {"ram_presente": False, "fragmento": None}

                if datos_cima.get("ram_presente"):
                    fragmento = datos_cima.get("fragmento") or ""
                    print(f"    [CIMA] RAM '{reaccion}' confirmada por LLM fallback")
                    if fragmento:
                        print(f"    [CIMA] Fragmento LLM: {fragmento[:240]}")
                else:
                    texto_ft = f"La RAM '{reaccion}' no aparece en la ficha técnica de CIMA."
                    print(f"    [CIMA] RAM '{reaccion}' no confirmada")

        print("="*60 + "\n")
        return texto_ft, [{"ft_url": ft_url, "nombre_oficial": nombre_oficial}]

    except Exception as e:
        print(f"    ❌ Error CIMA: {e}")
        print("="*60 + "\n")
        return f"Error al consultar CIMA: {str(e)[:120]}", []


# ─────────────────────────────────────────────
# Función principal
# ─────────────────────────────────────────────

def buscar_evidencia(datos: dict) -> dict:
    """
    Ejecuta las 6 búsquedas en paralelo con logging detallado.
    Fuentes: OpenFDA, PubMed, PRAC/EMA, CIMA, DrugBank, PharmGKB+CPIC.
    Devuelve dict con los resultados listos para el informe.
    """
    inicio_total = time.time()
    farmaco       = datos.get("Fármaco", "")
    reaccion      = datos.get("RAM", "")
    cod_dedra     = datos.get("cod_dedra", None)
    sexo          = datos.get("sex_id", "")
    raza          = datos.get("natio_dic_id", "")
    edad          = str(datos.get("Edad", ""))

    print("\n" + "="*70)
    print("PASO 4 - BUSQUEDA DE EVIDENCIA (6 FUENTES EN PARALELO)")
    print("="*70)
    print(f"Fármaco: {farmaco} | RAM: {reaccion} | MedDRA: {cod_dedra}")
    print(f"Sexo: {sexo} | Edad: {edad} | Raza: {raza}")
    print("-"*70)

    resultados = {}
    refs = {}

    with ThreadPoolExecutor(max_workers=6) as executor:
        futuros = {
            executor.submit(buscar_openfda,  farmaco, reaccion, cod_dedra):        "FDA FAERS",
            executor.submit(buscar_pubmed,   farmaco, reaccion, sexo, raza, edad, cod_dedra): "PubMed",
            executor.submit(buscar_prac,     farmaco, reaccion, cod_dedra):        "PRAC/EMA",
            executor.submit(buscar_drugbank, farmaco):                              "DrugBank",
            executor.submit(buscar_pharmgkb, farmaco):                              "PharmGKB",
            executor.submit(buscar_reactome, farmaco):                              "Reactome",
        }

        completadas = 0
        for futuro in as_completed(futuros):
            nombre = futuros[futuro]
            completadas += 1
            try:
                resultado, ref_data = futuro.result()
                resultados[nombre] = resultado
                refs[nombre] = ref_data
                n = len(str(resultado))
                print(f"  ✓ [{completadas}/6] {nombre} → OK ({n} chars)")
            except Exception as e:
                resultados[nombre] = f"Error al consultar {nombre}: {str(e)[:60]}"
                refs[nombre] = []
                print(f"  ✗ [{completadas}/6] {nombre} → FALLO ({str(e)[:50]})")

    resultados["refs"] = refs

    duracion_total = time.time() - inicio_total
    print("-"*70)
    print(f"✓ BUSQUEDA COMPLETADA en {duracion_total:.2f}s")
    print("="*70 + "\n")

    return resultados
