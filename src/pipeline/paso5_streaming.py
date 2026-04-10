"""
Paso 5 — Generacion de informe en 3 dimensiones en paralelo.
Cada dimension agrupa sus subsecciones en una sola llamada LLM
y devuelve JSON estructurado con subsecciones anidadas.
"""
import json
import os
import re
import threading
import boto3
from botocore.config import Config
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv


BEDROCK_CONFIG = Config(
    connect_timeout=10,
    read_timeout=120,
    retries={"max_attempts": 1, "mode": "standard"},
)

_log_lock = threading.Lock()


# ── Utilidades ────────────────────────────────────────────────────────────────

def _raw(valor) -> str:
    """Serializa cualquier valor de evidencia a texto sin recortar nada."""
    if valor is None:
        return "No disponible"
    if isinstance(valor, str):
        return valor.strip() or "No disponible"
    try:
        return json.dumps(valor, ensure_ascii=False, indent=2)
    except Exception:
        return str(valor)


def _llamar_llm(system: str, user: str, max_tokens: int = 1024, label: str = "") -> str:
    load_dotenv(override=True)
    INFERENCE_PROFILE_ARN = os.environ["INFERENCE_PROFILE_ARN"]
    MODEL_HAIKU = INFERENCE_PROFILE_ARN
    REGION = INFERENCE_PROFILE_ARN.split(":")[3]
    client = boto3.client(
        "bedrock-runtime",
        region_name=REGION,
        aws_access_key_id=os.environ["aws_access_key_id"],
        aws_secret_access_key=os.environ["aws_secret_access_key"],
        aws_session_token=os.environ.get("aws_session_token"),
        config=BEDROCK_CONFIG,
    )
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}]
    })

    tag = f"paso5/{label}" if label else "paso5"
    with _log_lock:
        print("\n" + "="*60)
        print(f">>> [{tag}] SYSTEM PROMPT")
        print(system[:500] + ("..." if len(system) > 500 else ""))
        print(f"--- [{tag}] USER PROMPT ---")
        print(user[:800] + ("..." if len(user) > 800 else ""))
        print("="*60)

    with _log_lock:
        print(f"    [{tag}] >>> invoke_model START (modelId={MODEL_HAIKU[:60]}...)")
    try:
        response = client.invoke_model(modelId=MODEL_HAIKU, body=body)
    except Exception as bedrock_err:
        with _log_lock:
            print(f"    [{tag}] !!! invoke_model ERROR: {type(bedrock_err).__name__}: {bedrock_err}")
        raise
    texto = json.loads(response["body"].read())["content"][0]["text"].strip()

    with _log_lock:
        print(f"\n>>> [{tag}] RESPUESTA LLM")
        print(texto[:600] + ("..." if len(texto) > 600 else ""))
        print("="*60 + "\n")

    return texto


def _parse_json(raw: str) -> dict:
    """Parsea JSON de la respuesta LLM con fallbacks robustos."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # Eliminar bloques markdown ```json ... ```
    cleaned = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw.strip(), flags=re.MULTILINE)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    match = re.search(r'\{[\s\S]*\}', cleaned)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    return {"texto_libre": raw}


def _seccion(id_seccion: str, titulo: str, system: str, user: str,
             fuentes: list, refs: list = None, max_tokens: int = 2048) -> dict:
    try:
        contenido_raw = _llamar_llm(system, user, max_tokens, label=id_seccion)
        contenido = _parse_json(contenido_raw)
        return {
            "id":        id_seccion,
            "titulo":    titulo,
            "estado":    "ok",
            "contenido": contenido,
            "fuentes":   fuentes,
            "refs":      refs or [],
        }
    except Exception as e:
        return {
            "id":        id_seccion,
            "titulo":    titulo,
            "estado":    "error",
            "contenido": {"error": str(e)},
            "fuentes":   fuentes,
            "refs":      refs or [],
        }


# ── Normalizacion de CIMA y PRAC (paso4 ya hizo el filtrado) ─────────────────
# paso4 buscar_cima  → si RAM no encontrada devuelve: "La RAM '...' no aparece en la ficha técnica de CIMA."
# paso4 buscar_prac  → si RAM no encontrada devuelve: texto vacío o "La RAM '...' no aparece en las actas PRAC analizadas."
# Aquí solo estandarizamos la frase negativa para el LLM de informe.

# Prefijos exactos de los mensajes "no encontrado" que genera paso4.
# El contenido real de CIMA/PRAC nunca empieza por ninguno de estos.
_PASO4_SIN_INFO_PREFIXES = (
    "sin resultados en cima",
    "ficha técnica no disponible",
    "la ram ",           # "La RAM '...' no aparece en la ficha técnica..."
    "error al consultar",
    "datos prac/ema no disponibles",
    "sin respuesta (timeout)",
    "no disponible",
)


def _es_sin_info(texto: str) -> bool:
    """Devuelve True si el texto es un mensaje de 'no encontrado' de paso4."""
    if not texto or not texto.strip():
        return True
    return texto.strip().lower().startswith(_PASO4_SIN_INFO_PREFIXES)


def _preparar_cima(farmaco: str, ram: str, texto: str) -> str:
    """paso4 ya filtró CIMA (regex + LLM). Solo normaliza la frase negativa si procede."""
    if _es_sin_info(texto):
        return (
            f"En la ficha técnica de CIMA/AEMPS no se ha encontrado información "
            f"relacionada con la RAM '{ram}' para el fármaco '{farmaco}'."
        )
    return texto


def _preparar_prac(farmaco: str, ram: str, texto: str) -> str:
    """paso4 ya filtró PRAC (regex + LLM por subsección). Solo normaliza la frase negativa si procede."""
    if _es_sin_info(texto):
        return (
            f"No se ha identificado evaluación regulatoria formal de la RAM '{ram}' "
            f"para el fármaco '{farmaco}' en las actas PRAC/EMA disponibles."
        )
    return texto


# ── Dimensión 1: Mecanística ─────────────────────────────────────────────────

def _d1_mecanistica(datos, evidencia):
    farmaco = datos.get("Fármaco", datos.get("Farmaco", ""))
    ram     = datos.get("RAM", "")

    drugbank = _raw(evidencia.get("DrugBank"))
    pharmgkb = _raw(evidencia.get("PharmGKB"))
    pubmed   = _raw(evidencia.get("PubMed"))

    # Refs para el informe
    refs_db   = evidencia.get("refs", {}).get("DrugBank", []) or []
    refs_pgkb = evidencia.get("refs", {}).get("PharmGKB", []) or []
    pubmed_val = evidencia.get("PubMed", {})
    casos_urls = []
    if isinstance(pubmed_val, dict):
        casos_urls = (pubmed_val.get("casos_reportados") or {}).get("urls", []) or []

    system = """
Eres un experto en Farmacologia Molecular, Toxicologia Clinica y Farmacogenetica con enfoque en farmacovigilancia regulatoria.

OBJETIVO:
Redactar EXCLUSIVAMENTE la DIMENSION MECANISTICA de un informe tecnico de farmacovigilancia.

ESTILO OBLIGATORIO:
- Tecnico, conservador y basado en evidencia
- Maximo 1-2 frases por campo
- Sin prosa extensa
- Tono de informe regulatorio real
- Priorizar precision frente a completitud

REGLAS CRITICAS:
1. NO inventar mecanismos, genes, niveles de evidencia ni relaciones farmacogeneticas.
2. Usa EXCLUSIVAMENTE la informacion proporcionada en la entrada. No añadas nada que no este ahi.
3. Diferenciar siempre entre mecanismo documentado, plausibilidad biologica indirecta, y ausencia de evidencia.
4. Si no hay soporte suficiente, usar "Desconocida", "No documentada", "Hipotetico" o listas vacias [].
5. Los niveles 1A/1B/2A/2B SOLO pueden usarse si aparecen explicitamente en la entrada.
6. NO incluir genes solo por ser dianas farmacologicas.
7. Si no hay genes farmacogeneticos explicitos en la entrada, devolver "tabla_genes_evidencia": [].
8. NO usar PubMed de casos clinicos para "subir" artificialmente la plausibilidad mecanistica.
9. Si la relacion farmaco-RAM es especulativa, decirlo explicitamente.

CRITERIOS plausibilidad:
- Alta: mecanismo directo bien documentado
- Media: mecanismo indirecto plausible con soporte parcial
- Baja: hipotesis debil o inespecifica
- Desconocida: sin base mecanistica suficiente

Responde EXCLUSIVAMENTE en JSON valido:
{
    "plausibilidad": {
        "nivel": "Alta|Media|Baja|Desconocida",
        "justificacion": "..."
    },
    "ruta_biologica": {
        "estado": "Documentada|Parcialmente documentada|No documentada",
        "diana_principal": "... o null",
        "via": "...",
        "cyp_implicado": "... o null"
    },
    "rutas_no_seguridad": {
        "dianas_off_target": [
            {"gen": "", "relacion_ram": ""}
        ],
        "mecanismo_toxicidad": "..."
    },
    "tabla_genes_evidencia": [
        {"gen": "", "nivel_evidencia": "1A|1B|2A|2B", "efecto": "..."}
    ],
    "validacion_farmacogenetica": "..."
}
""".strip()

    user = f"""
Farmaco: {farmaco}
RAM evaluada: {ram}
Sexo: {datos.get('sex_id', '')} | Edad: {datos.get('Edad', '')} | Etnia: {datos.get('natio_dic_id', '')}

--- DrugBank (mecanismo, dianas, CYP, toxicidad) ---
{drugbank}

--- PharmGKB + CPIC (farmacogenetica completa) ---
{pharmgkb}

--- PubMed (casos reportados y literatura — solo contexto, NO asumir mecanismo por ello) ---
{pubmed}

INSTRUCCIONES ADICIONALES:
- Basa tu analisis UNICAMENTE en la informacion de las secciones anteriores, sin añadir datos externos.
- Si no hay evidencia farmacogenetica explicita y valida para esta relacion farmaco-RAM, devuelve "tabla_genes_evidencia": [].
- Si la via hacia la RAM no esta documentada, usa "estado": "No documentada" o "Parcialmente documentada".
- No rellenes off-targets por defecto; si no hay base razonable, usa [].
""".strip()

    return _seccion(
        "mecanistica",
        "1. Dimension Mecanistica",
        system,
        user,
        fuentes=["DrugBank", "PharmGKB", "CPIC", "PubMed"],
        refs=(
            casos_urls
            + ([{"url": r.get("url", "")} for r in refs_db] if refs_db else [])
            + ([{"url": r.get("url", "")} for r in refs_pgkb] if refs_pgkb else [])
        ),
        max_tokens=2048,
    )


# ── Dimensión 2: Clínica ─────────────────────────────────────────────────────

def _d2_clinica(datos, evidencia):
    farmaco = datos.get("Fármaco", datos.get("Farmaco", ""))
    ram     = datos.get("RAM", "")

    pubmed  = _raw(evidencia.get("PubMed"))
    pharmgkb = _raw(evidencia.get("PharmGKB"))
    fda     = _raw(evidencia.get("FDA FAERS"))
    cima    = _preparar_cima(farmaco, ram, _raw(evidencia.get("CIMA")))

    # Refs PubMed
    pubmed_val = evidencia.get("PubMed", {})
    todas_urls = []
    if isinstance(pubmed_val, dict):
        for k in ["casos_reportados", "factores_riesgo", "recomendaciones"]:
            todas_urls += (pubmed_val.get(k) or {}).get("urls", []) or []

    system = """
Eres un experto en Farmacovigilancia Clinica.

OBJETIVO:
Redactar EXCLUSIVAMENTE la DIMENSION CLINICA de un informe tecnico de farmacovigilancia.

ESTILO OBLIGATORIO:
- Tecnico, conciso y prudente
- Maximo 1-2 frases por campo
- Sin prosa extensa
- Tono de informe regulatorio real

REGLAS CRITICAS:
1. NO inventar comorbilidades, dosis, duracion, medicacion concomitante ni antecedentes si no estan en la entrada.
2. Usa EXCLUSIVAMENTE la informacion proporcionada. No añadas nada que no este ahi.
3. Si faltan datos del paciente, indicarlo como limitacion explicita.
4. La interpretacion de FAERS/openFDA debe ser descriptiva; NO prueba causalidad.
5. NO usar reglas rigidas simplistas tipo ">500 = senal establecida".
6. La senal clinica/epidemiologica debe graduarse segun consistencia global, no solo por recuento bruto.
7. Si la RAM no consta claramente en la ficha tecnica segun la informacion de CIMA, marcarla como "Desconocida" o "Parcialmente documentada".

CRITERIOS riesgo clinico:
- Alto: factores predisponentes claros y/o contexto compatible bien documentado
- Moderado: factores parciales o informacion incompleta
- Bajo: pocos elementos de susceptibilidad identificables
- Indeterminado: datos insuficientes

CRITERIOS senal:
- Establecida: multiples fuentes consistentes
- Incipiente: indicios descriptivos, sin validacion suficiente
- No identificada: sin patron relevante
- Indeterminada: datos insuficientes o no comparables

Responde EXCLUSIVAMENTE en JSON valido:
{
    "analisis_factores_riesgo": {
        "perfil_paciente": "...",
        "factores_adicionales": ["..."],
        "nivel_riesgo": "Alto|Moderado|Bajo|Indeterminado",
        "justificacion": "..."
    },
    "volumen_casos": {
        "total_faers": "numero o No disponible",
        "interpretacion": "...",
        "senal": "Establecida|Incipiente|No identificada|Indeterminada"
    },
    "ficha_tecnica": {
        "ram_recogida": true,
        "estado": "Conocida|Desconocida|Parcialmente documentada",
        "fuente": "CIMA/AEMPS"
    }
}
""".strip()

    user = f"""
Farmaco: {farmaco}
RAM evaluada: {ram}
Edad: {datos.get('Edad', '')} | Sexo: {datos.get('sex_id', '')} | Etnia: {datos.get('natio_dic_id', '')}
RAM en ficha tecnica (validacion automatica previa): {datos.get('ram_resultado', 'desconocido')}

--- CIMA / AEMPS (ficha tecnica) ---
{cima}

--- FDA FAERS / openFDA ---
{fda}

--- PubMed (literatura cientifica completa) ---
{pubmed}

--- PharmGKB + CPIC (contexto farmacogenetico — solo referencia, no asumir riesgo clinico si no aplica) ---
{pharmgkb}

INSTRUCCIONES ADICIONALES:
- Basa tu analisis UNICAMENTE en la informacion de las secciones anteriores, sin añadir datos externos.
- Si edad, sexo o etnia no modifican claramente el riesgo para esta RAM, decirlo de forma explicita.
- Si faltan datos como comorbilidades, dosis, duracion o concomitantes, incluirlos como limitaciones en "factores_adicionales".
- Interpreta FAERS como recuento descriptivo, no como prueba causal.
- Para "ficha_tecnica", basate en la seccion CIMA/AEMPS proporcionada arriba.
""".strip()

    return _seccion(
        "clinica",
        "2. Dimension Clinica",
        system,
        user,
        fuentes=["Datos paciente", "PubMed", "FDA FAERS", "CIMA", "PharmGKB"],
        refs=todas_urls,
        max_tokens=2048,
    )


# ── Dimensión 3: Regulatoria ─────────────────────────────────────────────────

def _d3_regulatoria(datos, evidencia):
    farmaco = datos.get("Fármaco", datos.get("Farmaco", ""))
    ram     = datos.get("RAM", "")

    prac = _preparar_prac(farmaco, ram, _raw(evidencia.get("PRAC/EMA")))
    fda  = _raw(evidencia.get("FDA FAERS"))
    cima = _preparar_cima(farmaco, ram, _raw(evidencia.get("CIMA")))

    refs_prac = evidencia.get("refs", {}).get("PRAC/EMA", []) or []

    system = """
Eres un experto en Farmacovigilancia Regulatoria (EMA/PRAC, AEMPS y FDA).

OBJETIVO:
Redactar EXCLUSIVAMENTE la DIMENSION REGULATORIA de un informe tecnico de RAM.

ESTILO OBLIGATORIO:
- Tecnico, conservador y basado en evidencia
- Maximo 1-3 frases por campo
- Tono de informe regulatorio real
- Sin prosa extensa

REGLAS CRITICAS:
1. NO inventar senales formales PRAC, codigos EPITT, procedimientos regulatorios ni cambios de ficha tecnica.
2. Usa EXCLUSIVAMENTE la informacion proporcionada en la entrada. No añadas nada que no este ahi.
3. La ausencia en PRAC/EPITT/ficha tecnica significa ausencia de reconocimiento formal, NO ausencia definitiva de riesgo.
4. FAERS por si solo NO justifica "senal reconocida".
5. La accion recomendada debe ser proporcional a la evidencia disponible.
6. No sobredimensionar la imputabilidad si faltan datos clinicos clave.
7. Si no hay EPITT explicito en la entrada, devolver "epitt": null.

CRITERIOS estado senal:
- Senal reconocida: existe reconocimiento regulatorio formal explicito en la entrada
- En evaluacion regulatoria: existe revision/procedimiento identificable en la entrada
- Sin reconocimiento formal: no consta evaluacion formal con la informacion disponible
- Datos insuficientes: base insuficiente para clasificar

Acciones posibles:
- notificar a farmacovigilancia
- escalar a PRAC
- monitorizacion rutinaria
- sin accion recomendada

Responde EXCLUSIVAMENTE en JSON valido:
{
    "estado_senal": {
        "estado": "Senal reconocida|En evaluacion regulatoria|Sin reconocimiento formal|Datos insuficientes",
        "epitt": null,
        "detalle": "..."
    },
    "accion": {
        "recomendada": "notificar a farmacovigilancia|escalar a PRAC|monitorizacion rutinaria|sin accion recomendada",
        "justificacion": "..."
    },
    "conclusion_regulatoria": "..."
}
""".strip()

    user = f"""
Farmaco: {farmaco}
RAM evaluada: {ram}
RAM en ficha tecnica (validacion automatica previa): {datos.get('ram_resultado', 'desconocido')}
URL Ficha Tecnica: {datos.get('ft_url', '')}

--- PRAC / EMA (actas de senales) ---
{prac}

--- FDA FAERS / openFDA ---
{fda}

--- CIMA / AEMPS (ficha tecnica) ---
{cima}

INSTRUCCIONES ADICIONALES:
- Basa tu analisis UNICAMENTE en la informacion de las secciones anteriores, sin añadir datos externos.
- Usa "Sin reconocimiento formal" si no consta senal PRAC/EPITT o inclusion en ficha tecnica.
- Solo usa "En evaluacion regulatoria" o "Senal reconocida" si aparece de forma explicita en la entrada.
- "escalar a PRAC" solo si hay soporte regulatorio o senal suficientemente consistente; si no, usa "notificar a farmacovigilancia" o "monitorizacion rutinaria".
- La conclusion regulatoria debe ser prudente y reflejar limitaciones de evidencia.
""".strip()

    return _seccion(
        "regulatoria",
        "3. Dimension Regulatoria",
        system,
        user,
        fuentes=["PRAC/EMA", "FDA FAERS", "CIMA", "Ficha Tecnica"],
        refs=(
            [{"filename": r.get("filename", ""), "url": ""} for r in refs_prac if r.get("ram_encontrada")]
            if refs_prac else []
        ),
        max_tokens=1800,
    )


# ── Función principal ────────────────────────────────────────────────────────

def generar_secciones(datos: dict, evidencia: dict) -> list:
    """
    Genera las 3 dimensiones en paralelo.
    Cada una agrupa sus subsecciones en una sola llamada LLM.
    """
    tareas = [
        ("mecanistica", lambda: _d1_mecanistica(datos, evidencia)),
        ("clinica",     lambda: _d2_clinica(datos, evidencia)),
        ("regulatoria", lambda: _d3_regulatoria(datos, evidencia)),
    ]

    resultados_por_id = {}
    with ThreadPoolExecutor(max_workers=3) as executor:
        futuros = {executor.submit(fn): id_sec for id_sec, fn in tareas}
        for futuro in as_completed(futuros):
            resultado = futuro.result()
            resultados_por_id[resultado["id"]] = resultado

    orden = ["mecanistica", "clinica", "regulatoria"]
    return [resultados_por_id[k] for k in orden if k in resultados_por_id]


# ── Compatibilidad con generar_informe (app antigua) ────────────────────────

def generar_informe(datos: dict, evidencia: dict, ram_resultado: str = "", ft_url: str = "") -> dict:
    datos["ram_resultado"] = ram_resultado or datos.get("ram_resultado", "")
    datos["ft_url"]        = ft_url or datos.get("ft_url", "")
    secciones = generar_secciones(datos, evidencia)
    return {"json": {s["id"]: s["contenido"] for s in secciones}, "secciones": secciones}
