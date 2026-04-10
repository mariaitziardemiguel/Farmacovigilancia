"""
Test del prompt de Evaluación Mecanística (Secciones 1, 4 y 5 del informe).
Llama a los scripts de cada fuente y consolida los datos para el LLM.
"""
import sys
import os
import json
import boto3
import requests

# Añadir carpeta scripts al path para importar los otros scripts
sys.path.insert(0, os.path.dirname(__file__))

from test_reactome             import obtener_datos_reactome
from test_pubchem_drugbank     import obtener_datos_pubchem
from test_pharmgkb             import obtener_datos_pharmgkb
from test_openfda_interactions import obtener_datos_interacciones

INFERENCE_PROFILE_ARN = "arn:aws:bedrock:eu-west-1:953246180205:inference-profile/eu.anthropic.claude-sonnet-4-6"
REGION       = INFERENCE_PROFILE_ARN.split(":")[3]
UMLS_API_KEY = "edef02c2-c71e-4208-8031-53ac10bc8c0f"

# ── PARÁMETROS ──────────────────────────────────────────────
CASO = {
    "farmaco":    "Metotrexato",
    "ram":        "Mucositis",
    "cod_meddra": "10033661",
}
# ────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────
# Normalización: fármaco → INN y RAM → término inglés via UMLS
# ─────────────────────────────────────────────────────────────

def normalizar_farmaco(farmaco: str) -> str:
    """Fármaco → INN en inglés via RxNorm."""
    try:
        resp = requests.get(
            "https://rxnav.nlm.nih.gov/REST/rxcui.json",
            params={"name": farmaco, "search": 2}, timeout=10
        )
        rxcui_list = resp.json().get("idGroup", {}).get("rxnormId", [])
        if rxcui_list:
            r2 = requests.get(
                f"https://rxnav.nlm.nih.gov/REST/rxcui/{rxcui_list[0]}/properties.json",
                timeout=10
            )
            inn = r2.json().get("properties", {}).get("name", "")
            if inn:
                print(f"[RxNorm] '{farmaco}' -> INN='{inn}'")
                return inn
    except Exception as e:
        print(f"[RxNorm] ERROR: {e}")
    print(f"[RxNorm] WARNING '{farmaco}' no encontrado, usando nombre original")
    return farmaco


def normalizar_ram(cod_meddra: str, ram_fallback: str) -> str:
    """Código MedDRA → término PT en inglés via UMLS."""
    try:
        resp = requests.get(
            f"https://uts-ws.nlm.nih.gov/rest/content/current/source/MDR/{cod_meddra}",
            params={"apiKey": UMLS_API_KEY}, timeout=10
        )
        if resp.ok:
            nombre = resp.json()["result"]["name"]
            print(f"[UMLS] código {cod_meddra} -> '{nombre}'")
            return nombre
        print(f"[UMLS] WARNING código {cod_meddra} no encontrado ({resp.status_code})")
    except Exception as e:
        print(f"[UMLS] ERROR: {e}")
    print(f"[UMLS] Usando nombre original: '{ram_fallback}'")
    return ram_fallback

SEP = "=" * 65

SYSTEM = """Eres un experto en Farmacovigilancia y Biología de Sistemas. Redacta las secciones mecanísticas de un informe técnico en Markdown.

REGLAS:
* Responde en castellano (España).
* Usa SOLO la información de los datos de entrada — sin conocimiento externo.
* Relación causal no documentada → "Hipotético — sin evidencia molecular estructurada".
* Sin evidencia suficiente → redacción breve y conservadora.
* Devuelve solo Markdown, sin texto introductorio.

## 1. Evaluación Mecanística

### Plausibilidad
Clasifica como Alta / Moderada / Baja / Desconocida. Justifica con los datos de mecanismo de acción, vías, interacciones y farmacogenética.

### Cadena mecanística
Secuencia: fármaco → diana/mecanismo → proceso biológico → RAM.
Pasos sin datos: "No disponible". Conexiones no documentadas: "Hipotético — sin evidencia molecular estructurada".

### Rutas biológicas implicadas
Lista las vías documentadas con su relación con la RAM: directa / indirecta / no evidente.
Sin datos de vías: "No se identifican vías moleculares documentadas relacionadas con esta RAM en los datos proporcionados."

## 4. Rutas de No Seguridad

### Diana o mecanismo primario
Solo la diana o mecanismo explícitamente descrito en los datos.

### Rutas de no seguridad
Mecanismos adversos documentados, distinguiendo on-target / off-target.
Sin evidencia: "No disponible" o "Hipotético — sin evidencia molecular estructurada".

### Interacciones farmacológicas relevantes
Para cada interacción incluye: tipo (farmacocinética / farmacodinámica / no especificado), efecto esperado y relación con la RAM (relevante / indirecta / no evidente).

## 5. Contexto Farmacogenético
Tabla: Gen | Tipo de relación | Nivel de evidencia | Accionable CPIC

### Interpretación
Evidencia 1A/1B/2A/2B: indica aplicabilidad clínica.
Solo nivel 3 o inferior: "Especulativo — sin validación clínica".

### Validación final
Concluye si la farmacogenética explica específicamente la RAM. No conviertas una asociación PK general en explicación de la RAM si esa conexión no está en los datos."""


def construir_user_prompt(caso: dict, reactome: dict, pubchem: dict, interacciones: dict, pharmgkb: dict) -> str:
    return f"""Genera las secciones 1, 4 y 5 del informe de farmacovigilancia con los siguientes datos:

=== CASO ===
Fármaco      : {caso['farmaco']} (INN normalizado via RxNorm)
RAM          : {caso.get('ram_en', caso['ram'])}
Código MedDRA: {caso['cod_meddra']}

=== MECANISMO DE ACCIÓN (PubChem/DrugBank) ===
{pubchem.get('mecanismo_accion') or 'No disponible'}

Metabolismo: {pubchem.get('metabolismo') or 'No disponible'}

=== VÍAS MOLECULARES (Reactome) ===
{reactome.get('mensaje', 'No disponible')}
{json.dumps(reactome.get('vias', []), indent=2, ensure_ascii=False) if reactome.get('vias') else ''}

=== INTERACCIONES FARMACOLÓGICAS (OpenFDA) ===
{interacciones.get('texto_interacciones') or interacciones.get('mensaje') or 'No disponible'}

=== FARMACOGENÉTICA (PharmGKB) ===
{json.dumps(pharmgkb.get('farmacogenetica', {}), indent=2, ensure_ascii=False)}"""


def llamar_llm(system: str, user: str) -> str:
    client = boto3.client("bedrock-runtime", region_name=REGION)
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 2048,
        "system": system,
        "messages": [{"role": "user", "content": user}]
    })

    print(f"\n{SEP}")
    print(">>> USER PROMPT")
    print(user)
    print(SEP)

    response = client.invoke_model(modelId=INFERENCE_PROFILE_ARN, body=body)
    texto = json.loads(response["body"].read())["content"][0]["text"].strip()

    print(f"\n{SEP}")
    print(">>> RESPUESTA LLM")
    print(SEP)
    print(texto)

    return texto


if __name__ == "__main__":
    print(f"\n{SEP}")
    print("NORMALIZACIÓN")
    print(SEP)
    farmaco     = normalizar_farmaco(CASO["farmaco"])
    ram_en      = normalizar_ram(CASO["cod_meddra"], CASO["ram"])
    print(f"  Fármaco (INN) : {farmaco}")
    print(f"  RAM (EN)      : {ram_en}")
    print(f"  RAM (ES)      : {CASO['ram']}")

    # ── Llamadas a cada fuente ──────────────────────────────
    print(f"\n{SEP}")
    print("1/4 — PubChem/DrugBank")
    print(SEP)
    pubchem = obtener_datos_pubchem(farmaco)
    print(f"  Disponible : {pubchem['disponible']}")
    print(f"  Mecanismo  : {pubchem.get('mecanismo_accion', '')[:300]}")
    print(f"  Metabolismo: {pubchem.get('metabolismo', '')[:200]}")
    print(f"  Genes diana: {pubchem.get('dianas_genes', [])}")

    print(f"\n{SEP}")
    print("2/4 — Reactome")
    print(SEP)
    reactome = obtener_datos_reactome(farmaco, dianas=pubchem.get("dianas_genes", []))
    print(f"  Disponible : {reactome['disponible']}")
    print(f"  Mensaje    : {reactome['mensaje']}")
    for v in reactome.get("vias", [])[:3]:
        print(f"  • [{v['tipo']}] [{v.get('fuente', '')}] {v['nombre']}")

    print(f"\n{SEP}")
    print("3/4 — OpenFDA Interacciones")
    print(SEP)
    interacciones = obtener_datos_interacciones(farmaco)
    print(f"  Disponible : {interacciones['disponible']}")
    if interacciones['disponible']:
        print(f"  URL        : {interacciones.get('url_ficha_fda', '')}")
        print(f"  Texto      : {interacciones.get('texto_interacciones', '')[:300]}")
    else:
        print(f"  Mensaje    : {interacciones.get('mensaje', '')}")

    print(f"\n{SEP}")
    print("4/4 — PharmGKB")
    print(SEP)
    pharmgkb = obtener_datos_pharmgkb(farmaco)
    fg = pharmgkb.get("farmacogenetica", {})
    print(f"  Evidencia fuerte 1A/1B   : {len(fg.get('evidencia_fuerte_1A_1B', []))} genes")
    print(f"  Evidencia secundaria 2A/2B: {len(fg.get('evidencia_secundaria_2A_2B', []))} genes")
    baja = fg.get("evidencia_baja_referencia")
    print(f"  Evidencia baja (fallback) : {len(baja['genes']) if baja else 0} genes")

    # ── Construir prompt y llamar al LLM ────────────────────
    caso_normalizado = {**CASO, "farmaco": farmaco, "ram_en": ram_en}
    user_prompt = construir_user_prompt(caso_normalizado, reactome, pubchem, interacciones, pharmgkb)
    resultado = llamar_llm(SYSTEM, user_prompt)

    # ── Guardar resultado ───────────────────────────────────
    output_path = os.path.join(os.path.dirname(__file__), "test_prompt_mecanistica_output.md")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(resultado)
    print(f"\n✅ Resultado guardado en {output_path}")
