import sys
import os
import tempfile
import uuid
from pathlib import Path

WEBAPP_DIR = os.path.dirname(__file__)
SRC_DIR = os.path.join(WEBAPP_DIR, "..")
PREPROCESSING_DIR = os.path.join(SRC_DIR, "preprocessing")
sys.path.insert(0, WEBAPP_DIR)
sys.path.insert(0, SRC_DIR)
sys.path.insert(0, PREPROCESSING_DIR)

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from typing import List
import asyncio
import json as _json

from utils.llm_client import get_response

app = FastAPI()
app.mount("/static", StaticFiles(directory=os.path.join(WEBAPP_DIR, "static")), name="static")

# ── Caché de evidencia por sesión ─────────────────────────────────────────────
_evidencia_cache: dict = {}  # session_id -> {"datos": ..., "evidencia": ...}


def _build_chat_system(datos: dict, evidencia: dict) -> str:
    """Construye el system prompt del chat a partir de la evidencia completa del pipeline."""
    farmaco    = datos.get("Fármaco", datos.get("Farmaco", ""))
    ram        = datos.get("RAM", "")
    cod_dedra  = datos.get("cod_dedra", "")
    desc_dedra = datos.get("desc_dedra", "")

    lines = [
        "Eres DrugWatch IA, un asistente experto en farmacovigilancia. "
        "Responde en español con precisión clínica y científica. "
        "Basa tus respuestas exclusivamente en la información del caso y las fuentes de evidencia que se proporcionan a continuación. "
        "Si algo no está en la evidencia disponible, indícalo claramente.",
        "",
        "=== CASO CLÍNICO ===",
        f"Fármaco: {farmaco}",
        f"RAM sospechada: {ram}",
        f"Código MedDRA: {cod_dedra}" + (f" ({desc_dedra})" if desc_dedra else ""),
    ]
    if datos.get("Edad"):       lines.append(f"Edad: {datos['Edad']} años")
    if datos.get("sex_id"):     lines.append(f"Sexo: {datos['sex_id']}")
    if datos.get("natio_dic_id"): lines.append(f"Etnia: {datos['natio_dic_id']}")

    sources = [
        ("CIMA",      "CIMA — Ficha Técnica AEMPS"),
        ("FDA FAERS", "FDA FAERS — Notificaciones espontáneas"),
        ("PubMed",    "PubMed — Literatura científica"),
        ("PathwayCommons", "Pathway Commons — Interacciones gen-fármaco"),
        ("PharmGKB",  "PharmGKB+CPIC — Farmacogenética"),
        ("DrugBank",  "DrugBank — Perfil farmacológico"),
        ("PRAC/EMA",  "PRAC/EMA — Actas de señales"),
    ]

    lines += ["", "=== EVIDENCIA DE FUENTES EXTERNAS ==="]
    for key, label in sources:
        val = evidencia.get(key)
        if not val:
            continue
        lines.append(f"\n--- {label} ---")
        lines.append(val if isinstance(val, str) else _json.dumps(val, ensure_ascii=False))

    return "\n".join(lines)


def extract_pdf_text(pdf_bytes: bytes, filename: str) -> str:
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "extract_text",
        os.path.join(PREPROCESSING_DIR, "extract_text.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(pdf_bytes)
        tmp_path = Path(tmp.name)
    try:
        return mod.extract_main_text(tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)


@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = os.path.join(WEBAPP_DIR, "static", "index.html")
    with open(html_path, encoding="utf-8") as f:
        return f.read()


@app.get("/form", response_class=HTMLResponse)
async def form_page():
    html_path = os.path.join(WEBAPP_DIR, "static", "form.html")
    with open(html_path, encoding="utf-8") as f:
        return f.read()


# ── MedDRA local (singleton) ──────────────────────────────────────────────────
_meddra_instance = None

def _get_meddra():
    global _meddra_instance
    if _meddra_instance is None:
        from pipeline.terminology import MedDRA
        _meddra_instance = MedDRA()
    return _meddra_instance


@app.get("/meddra/search")
async def meddra_search(q: str = "", limit: int = 15):
    """Búsqueda de términos MedDRA por código o nombre. Mínimo 2 caracteres."""
    if len(q.strip()) < 2:
        return []
    loop = asyncio.get_event_loop()
    meddra = await loop.run_in_executor(None, _get_meddra)
    results = await loop.run_in_executor(None, meddra.search, q.strip(), min(limit, 30))
    return results


@app.get("/meddra/resolve")
async def meddra_resolve(code: str):
    """Resuelve un código MedDRA (PT o LLT) devolviendo nombres EN/ES y LLTs."""
    loop = asyncio.get_event_loop()
    meddra = await loop.run_in_executor(None, _get_meddra)

    is_pt  = meddra.is_pt(code)
    is_llt = meddra.is_llt(code)
    if not is_pt and not is_llt:
        return JSONResponse({"error": f"Código {code} no encontrado en MedDRA"}, status_code=404)

    term_type = "PT" if is_pt else "LLT"
    name_en   = meddra.code_to_name(code, "en")
    name_es   = meddra.code_to_name(code, "es")

    pt_code = code
    pt_name_en = pt_name_es = None
    if is_llt:
        pt_code, pt_name_en = meddra.llt_to_pt(code)
        if pt_code:
            pt_name_es = meddra.code_to_name(pt_code, "es")

    llts_en  = meddra.pt_to_llts(pt_code, "en") if pt_code else []
    llts_es  = meddra.pt_to_llts(pt_code, "es") if pt_code else []

    return {
        "code":       code,
        "type":       term_type,
        "name_en":    name_en,
        "name_es":    name_es,
        "pt_code":    pt_code if is_llt else None,
        "pt_name_en": pt_name_en,
        "pt_name_es": pt_name_es,
        "llts_en":    llts_en[:25],
        "llts_es":    llts_es[:25],
    }


# ── Fármaco autocomplete (RxNorm) ─────────────────────────────────────────────

def _rxnorm_suggestions(q: str) -> list[str]:
    """Llama a RxNorm spellingsuggestions y devuelve lista de nombres."""
    import requests as _req
    resp = _req.get(
        "https://rxnav.nlm.nih.gov/REST/spellingsuggestions.json",
        params={"name": q},
        timeout=8,
    )
    resp.raise_for_status()
    suggestions = (resp.json()
                   .get("suggestionGroup", {})
                   .get("suggestionList") or {})
    return suggestions.get("suggestion", []) if isinstance(suggestions, dict) else []


def _rxnorm_resolve(name: str) -> dict:
    """Dado un nombre de fármaco, devuelve {inn, rxcui} via RxNorm."""
    import requests as _req
    # Paso 1: rxcui con búsqueda aproximada
    r1 = _req.get(
        "https://rxnav.nlm.nih.gov/REST/rxcui.json",
        params={"name": name, "search": 2},
        timeout=8,
    )
    r1.raise_for_status()
    rxcui_list = r1.json().get("idGroup", {}).get("rxnormId", [])
    if not rxcui_list:
        return {"inn": None, "rxcui": None}
    rxcui = rxcui_list[0]
    # Paso 2: nombre INN canónico
    r2 = _req.get(
        f"https://rxnav.nlm.nih.gov/REST/rxcui/{rxcui}/properties.json",
        timeout=8,
    )
    r2.raise_for_status()
    inn = r2.json().get("properties", {}).get("name")
    return {"inn": inn, "rxcui": rxcui}


@app.get("/farmaco/search")
async def farmaco_search(q: str = ""):
    """
    Autocompletado de fármacos via RxNorm. Mínimo 2 caracteres.
    Devuelve [{name_es, name_en, inn}] para mostrar:
    castellano -> ingles | INN: principio_activo.
    """
    if len(q.strip()) < 2:
        return []
    q = q.strip()
    loop = asyncio.get_event_loop()
    try:
        suggestions = await loop.run_in_executor(None, _rxnorm_suggestions, q)
        name_en = (suggestions[0] if suggestions else q).strip()

        resolved = await loop.run_in_executor(None, _rxnorm_resolve, name_en)
        inn = (resolved.get("inn") or "").strip()
        rxcui = resolved.get("rxcui")

        if not inn and name_en.lower() != q.lower():
            resolved = await loop.run_in_executor(None, _rxnorm_resolve, q)
            inn = (resolved.get("inn") or "").strip()
            rxcui = resolved.get("rxcui")

        if not inn:
            return []

        return [{
            "name": q,
            "name_es": q,
            "name_en": name_en,
            "inn": inn,
            "rxcui": rxcui,
        }]
    except Exception:
        return []


@app.get("/farmaco/resolve")
async def farmaco_resolve(name: str):
    """Resuelve un nombre de fármaco a INN + rxcui via RxNorm."""
    if not name.strip():
        return JSONResponse({"error": "nombre vacío"}, status_code=400)
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, _rxnorm_resolve, name.strip())
        if not result.get("inn"):
            return JSONResponse({"error": f"No se encontró '{name}' en RxNorm"}, status_code=404)
        return result
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)




def _serializar_pubmed(resultado) -> tuple[str, str | None]:
    """Convierte el dict estructurado de PubMed a (texto legible, search_details).
    Devuelve (texto, primer_search_details_encontrado)."""
    if isinstance(resultado, str):
        return resultado, None
    
    partes = []
    primer_search_details = None
    
    nombres = {
        "casos_reportados": "CASOS REPORTADOS",
        "factores_riesgo":  "FACTORES DE RIESGO",
        "recomendaciones":  "RECOMENDACIONES Y MANEJO",
        "asociacion_general": "ASOCIACIÓN GENERAL",
    }
    for clave, etiqueta in nombres.items():
        sec = resultado.get(clave)
        if not sec:
            continue
        
        # Capturar el primer search_details
        if primer_search_details is None and sec.get("search_details"):
            primer_search_details = sec.get("search_details")
        
        n = sec.get("n_encontrados", 0)
        texto = sec.get("texto", "Sin resultados.")
        urls  = sec.get("urls", [])
        query = sec.get("query", "")
        
        partes.append(f"─── {etiqueta} ({n} artículos encontrados) ───")
        if query:
            partes.append(f"Query: {query}")
        partes.append(texto)
        if urls:
            partes.append("URLs: " + " | ".join(urls))
    
    texto_final = "\n\n".join(partes) if partes else "Sin resultados en PubMed."
    return texto_final, primer_search_details


def _serializar_pharmgkb(resultado) -> str:
    """Convierte el dict estructurado de PharmGKB+CPIC a texto legible para la tarjeta raw."""
    if isinstance(resultado, str):
        return resultado
    farmaco = resultado.get("farmaco", "")
    fg = resultado.get("farmacogenetica", {})
    partes = [f"PharmGKB + CPIC — {farmaco}"]

    def _genes_lineas(genes, titulo):
        if not genes:
            return []
        lineas = [f"\n{titulo}:"]
        for g in genes:
            guia = " ✓ GUÍA CPIC/DPWG" if g.get("tiene_guias_cpic_dpwg") else ""
            etiquetas = " ✓ ETIQUETA FDA/EMA" if g.get("tiene_etiquetas_fda_ema") else ""
            alelos = g.get("alelo_fenotipos", [])
            n_alelos = len(alelos)
            n_contextos = len(g.get("contextos_clinicos", []))
            n_enfermedades = len(g.get("enfermedades", []))
            extra = []
            if n_alelos:
                extra.append(f"{n_alelos} alelo-fenotipo")
            if n_contextos:
                extra.append(f"{n_contextos} contextos")
            if n_enfermedades:
                extra.append(f"{n_enfermedades} enfermedades")
            sufijo = f" ({'; '.join(extra)})" if extra else ""
            lineas.append(f"  · {g.get('gen','')} [evidencia: {g.get('nivel_evidencia','')}]{guia}{etiquetas}{sufijo}")
        return lineas

    partes += _genes_lineas(fg.get("evidencia_fuerte_1A_1B", []),     "Genes evidencia fuerte (1A/1B)")
    partes += _genes_lineas(fg.get("evidencia_secundaria_2A_2B", []),  "Genes evidencia secundaria (2A/2B)")
    baja = fg.get("evidencia_baja_referencia")
    if isinstance(baja, list) and baja:
        partes += _genes_lineas(baja[:3], "Genes evidencia baja (referencia)")

    return "\n".join(partes) if len(partes) > 1 else "Sin datos farmacogenéticos relevantes."


def _fuente_a_seccion(nombre: str, resultado, ref_data) -> dict | None:
    """Convierte el resultado de UNA fuente en una tarjeta para la UI (streaming por fuente)."""
    source_cfg = {
        "CIMA":      {"id": "cima_raw",    "titulo": "CIMA - Base de Datos AEMPS",        "fuentes": ["CIMA"]},
        "FDA FAERS": {"id": "fda_raw",     "titulo": "FDA FAERS - Notificaciones",         "fuentes": ["FDA FAERS"]},
        "DrugBank":  {"id": "drugbank_raw","titulo": "DrugBank - Perfiles Farmacologicos", "fuentes": ["DrugBank"]},
        "PharmGKB":  {"id": "pharmgkb_raw","titulo": "PharmGKB+CPIC - Farmacogenetica",   "fuentes": ["PharmGKB", "CPIC"]},
        "PubMed":    {"id": "pubmed_raw",  "titulo": "PubMed - Literatura Cientifica",     "fuentes": ["PubMed"]},
        "PRAC/EMA":  {"id": "prac_raw",     "titulo": "PRAC/EMA - Actas de Señales",        "fuentes": ["PRAC/EMA"]},
        "PathwayCommons": {"id": "pathway_commons_raw", "titulo": "Pathway Commons - Interacciones gen-fármaco", "fuentes": ["PathwayCommons"]},
    }
    cfg = source_cfg.get(nombre)
    if not cfg:
        return None

    # Serializar a texto legible según la fuente
    if nombre == "PubMed":
        texto, search_details = _serializar_pubmed(resultado)
    elif nombre == "PharmGKB":
        texto = _serializar_pharmgkb(resultado)
        search_details = None
    else:
        texto = resultado if isinstance(resultado, str) else str(resultado)
        search_details = None

    contenido = {"texto": texto, "contradiccion": None}
    if search_details:
        contenido["search_details"] = search_details
    if nombre == "CIMA" and isinstance(ref_data, list) and ref_data:
        contenido["ft_url"]          = ref_data[0].get("ft_url", "")
        contenido["nombre_oficial"]  = ref_data[0].get("nombre_oficial", "")
        contenido["ram_encontrada"]  = ref_data[0].get("ram_encontrada", True)
        contenido["ram"]             = ref_data[0].get("ram", "")
    if nombre == "FDA FAERS" and isinstance(ref_data, dict):
        contenido["fda_refs"] = ref_data
    if nombre == "PathwayCommons" and isinstance(ref_data, list):
        contenido["pathway_commons_refs"] = ref_data
    if nombre == "PRAC/EMA":
        # Los refs ya llevan fragmentos y ram_encontrada directamente desde buscar_prac
        contenido["prac_docs"] = [
            {
                "source":        r.get("source", ""),
                "date":          r.get("date", ""),
                "filename":      r.get("filename", ""),
                "fragmentos":    r.get("fragmentos", []),
                "ram_encontrada": r.get("ram_encontrada", False),
            }
            for r in (ref_data if isinstance(ref_data, list) else [])
        ]
    if nombre == "PharmGKB" and isinstance(resultado, dict):
        contenido["genes_data"] = resultado.get("farmacogenetica", {})
    if nombre == "PubMed" and isinstance(resultado, dict):
        _nombres_etiq = {
            "casos_reportados":   "Casos Reportados",
            "factores_riesgo":    "Factores de Riesgo",
            "recomendaciones":    "Recomendaciones y Manejo",
            "asociacion_general": "Asociación General",
        }
        pubmed_secciones = []
        for clave, etiq in _nombres_etiq.items():
            sec = resultado.get(clave)
            if not sec:
                continue
            pubmed_secciones.append({
                "clave":        clave,
                "etiqueta":     etiq,
                "n_encontrados": sec.get("n_encontrados", 0),
                "articulos":    sec.get("articulos", []),
            })
        contenido["pubmed_secciones"] = pubmed_secciones

    return {"id": cfg["id"], "titulo": cfg["titulo"], "estado": "ok",
            "contenido": contenido, "fuentes": cfg["fuentes"]}


@app.post("/generate_manual")
async def generate_manual(
    nhc:       str = Form(default=""),
    farmaco:   str = Form(...),
    farmaco_inn: str = Form(default=""),
    farmaco_es: str = Form(default=""),
    cod_dedra: str = Form(...),
    ram_en:    str = Form(default=""),
    ram_es:    str = Form(default=""),
    llts_en_json: str = Form(default="[]"),
    edad:      str = Form(default=""),
    sexo:      str = Form(default=""),
    etnia:     str = Form(default=""),

):
    from pipeline.paso4_evidencia import (
        buscar_openfda, buscar_pubmed,
        buscar_prac, buscar_drugbank, buscar_pharmgkb, buscar_pathway_commons,
        buscar_cima,
    )
    from pipeline.paso5_streaming import generar_secciones, generar_informe_interacciones

    farmaco_inn = farmaco_inn.strip()
    farmaco_es = (farmaco_es or farmaco).strip()
    ram_en = ram_en.strip()
    ram_es = ram_es.strip()
    try:
        llts_en = _json.loads(llts_en_json or "[]")
        if not isinstance(llts_en, list):
            llts_en = []
        llts_en = [str(x).strip() for x in llts_en if str(x).strip()]
    except Exception:
        llts_en = []

    if not farmaco_inn:
        raise HTTPException(status_code=400, detail="Falta farmaco_inn normalizado")
    if not farmaco_es:
        raise HTTPException(status_code=400, detail="Falta farmaco_es normalizado")
    if not ram_en:
        raise HTTPException(status_code=400, detail="Falta ram_en normalizado")

    desc_dedra = ram_es or ram_en or cod_dedra

    datos = {
        "id_patient_pseu": nhc,
        "Fármaco":         farmaco_es,
        "Fármaco_INN":     farmaco_inn,
        "RAM":             ram_es or ram_en or cod_dedra,  # Para display en UI
        "RAM_EN":          ram_en,            # Para búsquedas en inglés
        "RAM_ES":          ram_es,            # Para display
        "LLTS_EN":         llts_en,
        "cod_dedra":       cod_dedra,
        "desc_dedra":      desc_dedra,
        "Edad":            edad,
        "sex_id":          sexo,
        "natio_dic_id":    etnia,
    }

    def event(tipo: str, payload: dict) -> str:
        return f"data: {_json.dumps({'tipo': tipo, **payload}, ensure_ascii=False)}\n\n"

    async def stream():
        loop = asyncio.get_event_loop()

        yield event("progreso", {"mensaje": f"Caso manual: {farmaco_es} ({farmaco_inn}) | {desc_dedra}"})

        # Cabecera (antes de buscar evidencia)
        yield event("cabecera", {
            "farmaco":     farmaco_es,
            "farmaco_inn": farmaco_inn,
            "ram":         ram_es or "Desconocida",
            "ram_en":      ram_en,
            "cod_dedra":   cod_dedra,
            "desc_dedra":  desc_dedra,
            "caso":        nhc or "Manual",
        })

        # Paso 4: todas las fuentes incluida CIMA en paralelo
        SOURCE_TIMEOUT = 45  # segundos por fuente

        source_defs = [
            ("CIMA",       buscar_cima,      (farmaco_es, farmaco_inn, ram_es or ram_en)),
            ("DrugBank",   buscar_drugbank,  (farmaco_inn, True)),
            ("PharmGKB",   buscar_pharmgkb,  (farmaco_inn, True)),
            ("PathwayCommons", buscar_pathway_commons, (farmaco_inn, True)),
            ("FDA FAERS",  buscar_openfda,   (farmaco_inn, ram_en, cod_dedra, True, llts_en)),
            ("PubMed",     buscar_pubmed,    (farmaco_inn, ram_en, sexo, etnia, edad, cod_dedra, True, llts_en)),
            ("PRAC/EMA",   buscar_prac,      (farmaco_inn, ram_en, cod_dedra, True, llts_en)),
        ]

        yield event("progreso", {"mensaje": "Buscando evidencia en fuentes externas..."})

        task_to_name = {}
        for name, fn, args in source_defs:
            fut = loop.run_in_executor(None, fn, *args)
            task = asyncio.ensure_future(asyncio.wait_for(fut, timeout=SOURCE_TIMEOUT))
            task_to_name[task] = name

        evidencia = {}
        refs_dict = {}

        remaining = set(task_to_name.keys())

        while remaining:
            done, remaining = await asyncio.wait(remaining, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                name = task_to_name[task]
                try:
                    texto, ref_data = task.result()
                    evidencia[name] = texto
                    refs_dict[name] = ref_data
                    seccion = _fuente_a_seccion(name, texto, ref_data)
                    if seccion:
                        yield event("seccion", seccion)
                    yield event("progreso", {"mensaje": f"✓ {name} obtenida"})
                except asyncio.TimeoutError:
                    print(f"[generate_manual] TIMEOUT en fuente '{name}' tras {SOURCE_TIMEOUT}s")
                    evidencia[name] = "Sin respuesta (timeout)"
                    refs_dict[name] = []
                    seccion = _fuente_a_seccion(name, evidencia[name], refs_dict[name])
                    if seccion:
                        seccion["estado"] = "error"
                        yield event("seccion", seccion)
                    yield event("progreso", {"mensaje": f"⏱ {name} — sin respuesta (timeout)"})
                except Exception as e:
                    print(f"[generate_manual] ERROR en fuente '{name}': {e}")
                    evidencia[name] = f"Error: {str(e)[:60]}"
                    refs_dict[name] = []
                    seccion = _fuente_a_seccion(name, evidencia[name], refs_dict[name])
                    if seccion:
                        seccion["estado"] = "error"
                        yield event("seccion", seccion)
                    yield event("progreso", {"mensaje": f"✗ {name} — error"})

        evidencia["refs"] = refs_dict

        cima_refs = refs_dict.get("CIMA", [])
        ft_url_out = cima_refs[0].get("ft_url", "") if cima_refs else ""
        datos["ft_url"] = ft_url_out
        cima_texto_ev = evidencia.get("CIMA", "")
        if isinstance(cima_texto_ev, str) and cima_texto_ev.lower().startswith("sí aparece"):
            datos["ram_resultado"] = "si"
        elif isinstance(cima_texto_ev, str) and "no aparece" in cima_texto_ev.lower():
            datos["ram_resultado"] = "no"
        else:
            datos["ram_resultado"] = "desconocido"
        yield event("referencias", {
            "ft_url":   ft_url_out,
            "pubmed":   refs_dict.get("PubMed", []),
            "pathway_commons": refs_dict.get("PathwayCommons", []),
            "fda":      refs_dict.get("FDA FAERS", {}),
            "prac":     refs_dict.get("PRAC/EMA", []),
        })

        # Paso 5: secciones analíticas con paso5_streaming (timeout 5 min)
        yield event("progreso", {"mensaje": "Generando secciones del análisis con IA..."})
        try:
            secciones = await asyncio.wait_for(
                loop.run_in_executor(None, generar_secciones, datos, evidencia),
                timeout=300,
            )
            for seccion in secciones:
                contenido = seccion["contenido"]
                if isinstance(contenido, str):
                    try:
                        inicio = contenido.find("{")
                        fin = contenido.rfind("}") + 1
                        contenido = _json.loads(contenido[inicio:fin])
                    except Exception:
                        contenido = {"resumen": contenido}
                yield event("seccion", {
                    "id":        seccion["id"],
                    "titulo":    seccion["titulo"],
                    "estado":    seccion["estado"],
                    "contenido": contenido,
                    "fuentes":   seccion["fuentes"],
                })
        except Exception as e:
            yield event("progreso", {"mensaje": f"⚠️ No se pudo generar análisis IA: {str(e)[:80]}"})

        # Guardar evidencia completa en caché para el chatbot
        session_id = str(uuid.uuid4())
        _evidencia_cache[session_id] = {"datos": datos, "evidencia": evidencia}

        yield event("fin", {"mensaje": "Análisis completado", "session_id": session_id})

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/generate_interacciones")
async def generate_interacciones(
    farmacos_json:  str = Form(...),        # JSON array of {inn, name_es}
    cod_dedra:      str = Form(...),
    ram_en:         str = Form(default=""),
    ram_es:         str = Form(default=""),
    llts_en_json:   str = Form(default="[]"),
    edad:           str = Form(default=""),
    sexo:           str = Form(default=""),
    etnia:          str = Form(default=""),
):
    from pipeline.paso4_evidencia import (
        buscar_openfda, buscar_pubmed, buscar_prac,
        buscar_drugbank, buscar_pharmgkb, buscar_pathway_commons, buscar_cima,
        buscar_cima_seccion45, buscar_drugbank_interacciones, buscar_pubmed_interacciones,
    )
    from pipeline.paso5_streaming import generar_informe_interacciones

    try:
        farmacos = _json.loads(farmacos_json)
        if not isinstance(farmacos, list) or len(farmacos) < 2:
            raise HTTPException(status_code=400, detail="Se necesitan al menos 2 fármacos")
    except _json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="farmacos_json inválido")

    try:
        llts_en = _json.loads(llts_en_json or "[]")
        if not isinstance(llts_en, list):
            llts_en = []
        llts_en = [str(x).strip() for x in llts_en if str(x).strip()]
    except Exception:
        llts_en = []

    farmacos_inn = [f["inn"] for f in farmacos]

    def event(tipo: str, payload: dict) -> str:
        return f"data: {_json.dumps({'tipo': tipo, **payload}, ensure_ascii=False)}\n\n"

    async def stream():
        loop = asyncio.get_event_loop()
        SOURCE_TIMEOUT = 45

        yield event("progreso", {"mensaje": f"Iniciando análisis: {', '.join(farmacos_inn)}"})
        yield event("cabecera_int", {
            "farmacos":  farmacos,
            "ram":       ram_es or ram_en or cod_dedra,
            "ram_en":    ram_en,
            "cod_dedra": cod_dedra,
        })

        # ── Por cada fármaco: pipeline individual (sin LLM) ─────────────────
        for idx, farmaco in enumerate(farmacos):
            inn     = farmaco["inn"]
            name_es = farmaco.get("name_es", inn)

            yield event("progreso", {"mensaje": f"Fármaco {idx+1}/{len(farmacos)}: {name_es} ({inn})"})

            source_defs = [
                ("CIMA",      buscar_cima,     (name_es, inn, ram_es or ram_en, False)),
                ("DrugBank",  buscar_drugbank, (inn, True)),
                ("PharmGKB",  buscar_pharmgkb, (inn, True)),
                ("PathwayCommons", buscar_pathway_commons, (inn, True)),
                ("FDA FAERS", buscar_openfda,  (inn, ram_en, cod_dedra, True, llts_en)),
                ("PubMed",    buscar_pubmed,   (inn, ram_en, sexo, etnia, edad, cod_dedra, True, llts_en)),
                ("PRAC/EMA",  buscar_prac,     (inn, ram_en, cod_dedra, True, llts_en, False)),
            ]

            task_to_name = {}
            for src_name, fn, args in source_defs:
                fut  = loop.run_in_executor(None, fn, *args)
                task = asyncio.ensure_future(asyncio.wait_for(fut, timeout=SOURCE_TIMEOUT))
                task_to_name[task] = src_name

            remaining = set(task_to_name.keys())
            while remaining:
                done, remaining = await asyncio.wait(remaining, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    src_name = task_to_name[task]
                    try:
                        texto, ref_data = task.result()
                        seccion = _fuente_a_seccion(src_name, texto, ref_data)
                        if seccion:
                            seccion["farmaco_idx"] = idx
                            seccion["farmaco_inn"] = inn
                            yield event("seccion_farmaco", seccion)
                        yield event("progreso", {"mensaje": f"  ✓ {src_name} ({name_es})"})
                    except asyncio.TimeoutError:
                        yield event("progreso", {"mensaje": f"  ⏱ {src_name} ({name_es}) — timeout"})
                    except Exception as exc:
                        yield event("progreso", {"mensaje": f"  ✗ {src_name} ({name_es}) — {str(exc)[:60]}"})

        # ── Búsquedas específicas de interacción ────────────────────────────
        yield event("progreso", {"mensaje": "Buscando interacciones entre fármacos..."})

        # CIMA 4.5 — recoger todos los fármacos y emitir UN evento combinado
        cima_farmacos = []
        for idx, farmaco in enumerate(farmacos):
            inn     = farmaco["inn"]
            name_es = farmaco.get("name_es", inn)
            try:
                fut = loop.run_in_executor(None, buscar_cima_seccion45, name_es, inn)
                texto, refs = await asyncio.wait_for(fut, timeout=SOURCE_TIMEOUT)
                ft_url = refs[0].get("ft_url", "") if refs else ""
                cima_farmacos.append({"inn": inn, "name_es": name_es, "ft_url": ft_url, "texto": texto})
                yield event("progreso", {"mensaje": f"  ✓ CIMA 4.5 ({name_es})"})
            except asyncio.TimeoutError:
                cima_farmacos.append({"inn": inn, "name_es": name_es, "ft_url": "", "texto": "Timeout al consultar CIMA."})
                yield event("progreso", {"mensaje": f"  ⏱ CIMA 4.5 ({name_es}) — timeout"})
            except Exception as exc:
                cima_farmacos.append({"inn": inn, "name_es": name_es, "ft_url": "", "texto": f"Error: {str(exc)[:120]}"})
                yield event("progreso", {"mensaje": f"  ✗ CIMA 4.5 ({name_es}) — {str(exc)[:60]}"})

        # Detectar menciones cruzadas: ¿menciona la 4.5 de A al fármaco B?
        menciones_cruzadas = []
        for i, fa in enumerate(cima_farmacos):
            for j, fb in enumerate(cima_farmacos):
                if i == j:
                    continue
                texto_a = fa["texto"].lower()
                terminos_b = {fb["inn"].lower(), fb["name_es"].lower()}
                for term in terminos_b:
                    if len(term) >= 4 and term in texto_a:
                        # Extraer fragmento de contexto (~200 chars alrededor)
                        pos = texto_a.find(term)
                        start = max(0, pos - 80)
                        end   = min(len(fa["texto"]), pos + len(term) + 120)
                        fragmento = ("…" if start > 0 else "") + fa["texto"][start:end].strip() + ("…" if end < len(fa["texto"]) else "")
                        menciones_cruzadas.append({
                            "farmaco_origen":   fa["name_es"],
                            "farmaco_mencionado": fb["name_es"],
                            "fragmento":        fragmento,
                        })
                        break  # una mención por par es suficiente

        yield event("seccion_interaccion", {
            "id":       "cima_int",
            "titulo":   "CIMA 4.5 — Interacciones",
            "estado":   "ok",
            "contenido": {"farmacos": cima_farmacos, "menciones_cruzadas": menciones_cruzadas},
            "fuentes":  ["CIMA"],
            "subtipo":  "cima_int",
        })

        # DrugBank interacciones
        drugbank_int_data = {}
        try:
            fut = loop.run_in_executor(None, buscar_drugbank_interacciones, farmacos_inn)
            drugbank_int_data, _ = await asyncio.wait_for(fut, timeout=SOURCE_TIMEOUT)
            yield event("seccion_interaccion", {
                "id":        "drugbank_int",
                "titulo":    "DrugBank — Interacciones",
                "estado":    "ok",
                "contenido": drugbank_int_data,
                "fuentes":   ["DrugBank"],
                "subtipo":   "drugbank_int",
            })
            yield event("progreso", {"mensaje": "  ✓ DrugBank interacciones"})
        except asyncio.TimeoutError:
            yield event("progreso", {"mensaje": "  ⏱ DrugBank interacciones — timeout"})
        except Exception as exc:
            yield event("progreso", {"mensaje": f"  ✗ DrugBank interacciones — {str(exc)[:60]}"})

        # PubMed interacciones combinadas
        pubmed_int_data = {}
        try:
            fut = loop.run_in_executor(None, buscar_pubmed_interacciones, farmacos_inn, ram_en, llts_en)
            pubmed_int_data, _ = await asyncio.wait_for(fut, timeout=SOURCE_TIMEOUT)
            yield event("seccion_interaccion", {
                "id":        "pubmed_int",
                "titulo":    "PubMed — Combinaciones de Fármacos",
                "estado":    "ok",
                "contenido": pubmed_int_data,
                "fuentes":   ["PubMed"],
                "subtipo":   "pubmed_int",
            })
            yield event("progreso", {"mensaje": "  ✓ PubMed interacciones"})
        except asyncio.TimeoutError:
            yield event("progreso", {"mensaje": "  ⏱ PubMed interacciones — timeout"})
        except Exception as exc:
            yield event("progreso", {"mensaje": f"  ✗ PubMed interacciones — {str(exc)[:60]}"})

        # Informe de interacción con LLM
        yield event("progreso", {"mensaje": "Generando informe de interacción con IA..."})
        try:
            evidencia_int = {
                "cima_45":           cima_farmacos,
                "menciones_cruzadas": menciones_cruzadas,
                "drugbank":          drugbank_int_data,
                "pubmed":            pubmed_int_data,
            }
            informe = await asyncio.wait_for(
                loop.run_in_executor(
                    None, generar_informe_interacciones,
                    farmacos, ram_es or ram_en, evidencia_int,
                ),
                timeout=300,
            )
            yield event("informe_int", {"contenido": informe})
            yield event("progreso", {"mensaje": "  ✓ Informe de interacción generado"})
        except Exception as exc:
            yield event("progreso", {"mensaje": f"  ✗ Informe interacción — {str(exc)[:80]}"})

        yield event("fin_int", {"mensaje": "Análisis de interacciones completado"})

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/chat")
async def chat(
    message: str = Form(...),
    history: str = Form(default="[]"),
    session_id: str = Form(default=""),
    files: List[UploadFile] = File(default=[]),
):
    # Construir system prompt desde caché de evidencia
    cached = _evidencia_cache.get(session_id)
    if cached:
        system = _build_chat_system(cached["datos"], cached["evidencia"])
    else:
        system = ""

    # Reconstruir historial
    try:
        messages = _json.loads(history)
        if not isinstance(messages, list):
            messages = []
    except Exception:
        messages = []

    # Procesar ficheros adjuntos
    doc_context = ""
    for file in files:
        if file.filename.endswith(".pdf"):
            pdf_bytes = await file.read()
            try:
                doc_text = extract_pdf_text(pdf_bytes, file.filename)
                doc_context += f"Documento adjunto ({file.filename}):\n\n{doc_text}\n\n---\n\n"
            except Exception as e:
                doc_context += f"[Error al procesar {file.filename}: {e}]\n\n"
        else:
            doc_context += f"[Fichero adjunto: {file.filename}]\n\n"

    llm_content = f"{doc_context}{message}" if doc_context else message
    messages.append({"role": "user", "content": llm_content})

    try:
        loop = asyncio.get_event_loop()
        response = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: get_response(messages, system=system)),
            timeout=70,
        )
    except asyncio.TimeoutError:
        response = "❌ Tiempo de espera agotado al consultar el modelo (70s). Intenta de nuevo."
    except Exception as e:
        response = f"❌ Error al contactar con el modelo: {e}"

    return JSONResponse({"response": response})
