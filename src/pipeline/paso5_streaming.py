"""
Paso 5 — Generacion de informe en una sola llamada LLM.
Las 3 dimensiones (mecanistica, clinica, regulatoria) se generan juntas
para minimizar tokens: cada fuente se envia una sola vez.
"""
import json
import os
import re
import threading
import boto3
from botocore.config import Config
from dotenv import load_dotenv


BEDROCK_CONFIG = Config(
    connect_timeout=10,
    read_timeout=180,
    retries={"max_attempts": 1, "mode": "standard"},
)

_log_lock = threading.Lock()


# ── Utilidades ────────────────────────────────────────────────────────────────

def _raw(valor) -> str:
    if valor is None:
        return "No disponible"
    if isinstance(valor, str):
        return valor.strip() or "No disponible"
    try:
        return json.dumps(valor, ensure_ascii=False, indent=2)
    except Exception:
        return str(valor)


def _llamar_llm(system: str, user: str, max_tokens: int = 8192) -> str:
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
        "messages": [{"role": "user", "content": user}],
    })

    with _log_lock:
        print("\n" + "="*60)
        print(">>> [paso5] SYSTEM PROMPT")
        print(system[:500] + ("..." if len(system) > 500 else ""))
        print("--- [paso5] USER PROMPT ---")
        print(user[:800] + ("..." if len(user) > 800 else ""))
        print("="*60)
        print("    [paso5] >>> invoke_model START")

    try:
        response = client.invoke_model(modelId=MODEL_HAIKU, body=body)
    except Exception as e:
        with _log_lock:
            print(f"    [paso5] !!! invoke_model ERROR: {type(e).__name__}: {e}")
        raise

    texto = json.loads(response["body"].read())["content"][0]["text"].strip()

    with _log_lock:
        print(f"\n>>> [paso5] RESPUESTA LLM")
        print(texto[:800] + ("..." if len(texto) > 800 else ""))
        print("="*60 + "\n")

    return texto


def _parse_json(raw: str) -> dict:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
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


# ── Normalización CIMA / PRAC ─────────────────────────────────────────────────

_PASO4_SIN_INFO_PREFIXES = (
    "sin resultados en cima",
    "ficha técnica no disponible",
    "la ram ",
    "error al consultar",
    "datos prac/ema no disponibles",
    "sin respuesta (timeout)",
    "no disponible",
)


def _es_sin_info(texto: str) -> bool:
    if not texto or not texto.strip():
        return True
    return texto.strip().lower().startswith(_PASO4_SIN_INFO_PREFIXES)


def _preparar_cima(farmaco: str, ram: str, texto: str) -> str:
    if _es_sin_info(texto):
        return (
            f"En la ficha técnica de CIMA/AEMPS no se ha encontrado información "
            f"relacionada con la RAM '{ram}' para el fármaco '{farmaco}'."
        )
    return texto


def _preparar_prac(farmaco: str, ram: str, texto: str) -> str:
    if _es_sin_info(texto):
        return (
            f"No se ha identificado evaluación regulatoria formal de la RAM '{ram}' "
            f"para el fármaco '{farmaco}' en las actas PRAC/EMA disponibles."
        )
    return texto


# ── Llamada única con las 3 dimensiones ──────────────────────────────────────

SYSTEM = """
Eres un experto en Farmacologia Molecular, Toxicologia Clinica, Farmacogenetica y Farmacovigilancia Regulatoria.

OBJETIVO:
Redactar las 3 dimensiones de un informe tecnico de farmacovigilancia en una sola respuesta JSON.

ESTILO OBLIGATORIO:
- Tecnico, conservador y basado en evidencia
- Maximo 1-2 frases por campo
- Sin prosa extensa
- Tono de informe regulatorio real

REGLAS CRITICAS (aplican a las 3 dimensiones):
1. NO inventar mecanismos, genes, niveles de evidencia, senales regulatorias ni datos clinicos.
2. Usa EXCLUSIVAMENTE la informacion proporcionada en la entrada. No añadas nada externo.
3. Si no hay soporte suficiente: "Desconocida", "No documentada", "Hipotetico" o listas vacias [].
4. Los niveles 1A/1B/2A/2B SOLO si aparecen explicitamente en la entrada.
5. NO incluir genes solo por ser dianas farmacologicas; necesitan evidencia farmacogenetica explicita.
6. NO usar PubMed de casos clinicos para inferir mecanismo biologico.
7. FAERS por si solo NO justifica "senal reconocida".
8. "escalar a PRAC" solo si hay soporte regulatorio suficientemente consistente.
9. Si la RAM no consta en CIMA, marcarla como "Desconocida" o "Parcialmente documentada".
10. Si la relacion farmaco-RAM es especulativa, decirlo explicitamente.

Responde EXCLUSIVAMENTE con este JSON valido (sin texto adicional, sin markdown):
{
  "mecanistica": {
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
      "dianas_off_target": [{"gen": "", "relacion_ram": ""}],
      "mecanismo_toxicidad": "..."
    },
    "tabla_genes_evidencia": [
      {"gen": "", "nivel_evidencia": "1A|1B|2A|2B", "efecto": "..."}
    ],
    "validacion_farmacogenetica": "..."
  },
  "clinica": {
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
  },
  "regulatoria": {
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
}
""".strip()


def _generar_informe_unico(datos: dict, evidencia: dict) -> list:
    farmaco = datos.get("Fármaco", datos.get("Farmaco", ""))
    ram     = datos.get("RAM", "")

    # Fuentes — cada una se prepara UNA sola vez
    drugbank        = _raw(evidencia.get("DrugBank"))
    pharmgkb        = _raw(evidencia.get("PharmGKB"))
    pathway_commons = _raw(evidencia.get("PathwayCommons"))
    pubmed          = _raw(evidencia.get("PubMed"))
    fda             = _raw(evidencia.get("FDA FAERS"))
    cima            = _preparar_cima(farmaco, ram, _raw(evidencia.get("CIMA")))
    prac            = _preparar_prac(farmaco, ram, _raw(evidencia.get("PRAC/EMA")))

    user = f"""
Farmaco: {farmaco}
RAM evaluada: {ram}
Sexo: {datos.get('sex_id', '')} | Edad: {datos.get('Edad', '')} | Etnia: {datos.get('natio_dic_id', '')}
RAM en ficha tecnica (validacion previa): {datos.get('ram_resultado', 'desconocido')}
URL Ficha Tecnica: {datos.get('ft_url', '')}

--- DrugBank (mecanismo, dianas, CYP, toxicidad) ---
{drugbank}

--- PharmGKB + CPIC (farmacogenetica) ---
{pharmgkb}

--- Pathway Commons (interacciones gen-farmaco, pathways moleculares) ---
{pathway_commons}

--- PubMed (literatura: casos, factores riesgo, recomendaciones) ---
{pubmed}

--- FDA FAERS / openFDA (notificaciones espontaneas) ---
{fda}

--- CIMA / AEMPS (ficha tecnica espanola) ---
{cima}

--- PRAC / EMA (actas de senales regulatorias) ---
{prac}

INSTRUCCIONES:
- dimension mecanistica: basa el mecanismo en DrugBank + PharmGKB + PathwayCommons. PubMed es solo contexto, no base mecanistica.
- dimension clinica: usa PubMed, FAERS y CIMA. PharmGKB solo como contexto de susceptibilidad individual.
- dimension regulatoria: usa PRAC, FAERS y CIMA. No uses fuentes mecanisticas para inferir reconocimiento regulatorio.
- Si no hay evidencia suficiente en alguna dimension, usa los valores vacios o "Desconocida" indicados.
""".strip()

    try:
        raw = _llamar_llm(SYSTEM, user, max_tokens=8192)
        resultado = _parse_json(raw)
    except Exception as e:
        resultado = {}
        print(f"    [paso5] ERROR llamada LLM: {e}")

    # Refs para cada sección
    refs_db   = evidencia.get("refs", {}).get("DrugBank", []) or []
    refs_pgkb = evidencia.get("refs", {}).get("PharmGKB", []) or []
    refs_prac = evidencia.get("refs", {}).get("PRAC/EMA", []) or []
    pubmed_val = evidencia.get("PubMed", {})
    todas_urls_pubmed = []
    if isinstance(pubmed_val, dict):
        for k in ["casos_reportados", "factores_riesgo", "recomendaciones"]:
            todas_urls_pubmed += (pubmed_val.get(k) or {}).get("urls", []) or []

    def _seccion(id_, titulo, clave, fuentes, refs):
        contenido = resultado.get(clave, {"error": "Sin datos"})
        return {
            "id":        id_,
            "titulo":    titulo,
            "estado":    "ok" if clave in resultado else "error",
            "contenido": contenido,
            "fuentes":   fuentes,
            "refs":      refs,
        }

    return [
        _seccion(
            "mecanistica",
            "1. Dimension Mecanistica",
            "mecanistica",
            ["DrugBank", "PharmGKB", "CPIC", "PathwayCommons", "PubMed"],
            (todas_urls_pubmed
             + [{"url": r.get("url", "")} for r in refs_db]
             + [{"url": r.get("url", "")} for r in refs_pgkb]),
        ),
        _seccion(
            "clinica",
            "2. Dimension Clinica",
            "clinica",
            ["PubMed", "FDA FAERS", "CIMA", "PharmGKB"],
            todas_urls_pubmed,
        ),
        _seccion(
            "regulatoria",
            "3. Dimension Regulatoria",
            "regulatoria",
            ["PRAC/EMA", "FDA FAERS", "CIMA"],
            [{"filename": r.get("filename", ""), "url": ""} for r in refs_prac if r.get("ram_encontrada")],
        ),
    ]


# ── Informe de interacción multi-fármaco ─────────────────────────────────────

SYSTEM_INT = """
Eres un experto en Farmacología Molecular, Toxicología Clínica y Farmacovigilancia.

OBJETIVO:
Analizar si la interacción entre los fármacos indicados puede explicar la RAM y redactar un informe técnico.
El sospechoso NO es un fármaco individual: es la INTERACCIÓN en sí.

REGLAS:
1. Usa EXCLUSIVAMENTE la información proporcionada. No añadas datos externos.
2. Distingue interacción farmacocinética (PK: metabolismo CYP, transportadores) de farmacodinámica (PD: efectos aditivos, sinérgicos, antagonistas).
3. Si no hay evidencia de interacción que explique la RAM, usa "interaccion_detectada": false.
4. No inventar mecanismos ni niveles de gravedad no documentados en las fuentes.

Responde EXCLUSIVAMENTE con este JSON válido (sin texto adicional):
{
  "interaccion_detectada": true,
  "tipo_interaccion": "Farmacocinética|Farmacodinámica|Mixta|No documentada",
  "mecanismo": "...",
  "gravedad": "Contraindicada|Mayor|Moderada|Menor|No documentada",
  "evidencia_cima": "...",
  "evidencia_drugbank": "...",
  "evidencia_pubmed": "...",
  "conclusion": "..."
}
""".strip()


def generar_informe_interacciones(farmacos: list, ram: str, evidencia_int: dict) -> dict:
    nombres = " + ".join(f.get("name_es", f.get("inn", "")) for f in farmacos)

    cima_45     = evidencia_int.get("cima_45", [])
    menciones   = evidencia_int.get("menciones_cruzadas", [])
    drugbank    = evidencia_int.get("drugbank", {})
    pubmed      = evidencia_int.get("pubmed", {})

    cima_txt = ""
    for m in menciones:
        cima_txt += (
            f"ALERTA: {m['farmaco_origen']} menciona a {m['farmaco_mencionado']} "
            f"en sección 4.5: \"{m['fragmento']}\"\n"
        )
    for f in cima_45:
        cima_txt += f"\n[{f.get('name_es', f.get('inn', ''))} — sección 4.5]:\n{f.get('texto', 'Sin datos')}\n"

    user = f"""
Fármacos en combinación: {nombres}
RAM evaluada: {ram}

--- CIMA sección 4.5 (interacciones en ficha técnica) ---
{cima_txt.strip() or 'Sin datos disponibles'}

--- DrugBank (interacciones entre fármacos) ---
{_raw(drugbank)}

--- PubMed (literatura sobre esta combinación + RAM) ---
{_raw(pubmed)}
""".strip()

    try:
        raw = _llamar_llm(SYSTEM_INT, user, max_tokens=2048)
        return _parse_json(raw)
    except Exception as e:
        print(f"    [paso5-int] ERROR: {e}")
        return {"error": str(e)}


# ── Función principal (misma interfaz que antes) ──────────────────────────────

def generar_secciones(datos: dict, evidencia: dict) -> list:
    return _generar_informe_unico(datos, evidencia)


def generar_informe(datos: dict, evidencia: dict, ram_resultado: str = "", ft_url: str = "") -> dict:
    datos["ram_resultado"] = ram_resultado or datos.get("ram_resultado", "")
    datos["ft_url"]        = ft_url or datos.get("ft_url", "")
    secciones = generar_secciones(datos, evidencia)
    return {"json": {s["id"]: s["contenido"] for s in secciones}, "secciones": secciones}
