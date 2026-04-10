"""
Test del paso5_streaming SIN llamadas LLM.
Muestra exactamente:
  1. Que datos de entrada recibe cada seccion (system prompt + user prompt)
  2. Como seria la estructura de salida (con contenido mock)
  3. Como se serializa como evento SSE
"""
import json
import sys
import os

# Datos de ejemplo (caso Warfarina + Hemorragia)
DATOS = {
    "Farmaco":        "Warfarina",
    "RAM":            "Hemorragia gastrointestinal",
    "Edad":           "72",
    "sex_id":         "Femenino",
    "natio_dic_id":   "Espanola",
    "cod_dedra":      "10017955",
    "desc_dedra":     "Haemorrhage",
    "ram_resultado":  "si",
    "ft_url":         "https://cima.aemps.es/cima/dochtml/ft/38414/FT_38414.html",
    "texto_ft":       "Hemorragia: reaccion adversa muy frecuente (>1/10). Se han notificado hemorragias graves incluyendo hemorragia intracraneal..."
}

EVIDENCIA = {
    "FDA":      "Se encontraron 45823 casos reportados en FAERS para Warfarin + Haemorrhage. Casos graves: 12341.",
    "Pubmed":   "PubMed: 847 articulos encontrados. Estudios muestran mayor riesgo en mayores de 65 anios con INR>3. "
                "Meta-analisis 2023 confirma asociacion causal entre warfarina y hemorragia GI (OR=2.3, IC95% 1.8-2.9).",
    "Reactome": "Rutas identificadas: R-HSA-159858 (Vitamin K metabolism), R-HSA-5621481 (Coagulation cascade). "
                "Diana molecular: VKORC1 (vitamina K epoxi reductasa). Inhibicion de factores II, VII, IX, X.",
    "PRACS":    "PRAC Meeting 2023-09: Warfarin - Signals assessment. Se mantiene en ficha tecnica. "
                "Recomendacion: monitoreo INR en mayores de 65 anios. Sin nuevas acciones regulatorias."
}


# ── Contenidos mock que devolveria el LLM ─────────────────────

MOCK_CONTENIDOS = {
    "mecanistica": {
        "plausibilidad": "Alta",
        "resumen": "La warfarina inhibe VKORC1, bloqueando la sintesis de factores de coagulacion dependientes de vitamina K (II, VII, IX, X). Esto produce hipoprotrombinemia que favorece hemorragias. La relacion mecanistica esta ampliamente documentada.",
        "contradiccion": None
    },
    "factores_riesgo": {
        "edad": "Mayor riesgo en >65 anos. La paciente (72 anos) esta en grupo de alto riesgo segun evidencia PubMed.",
        "sexo": "Sexo femenino asociado a mayor sensibilidad a warfarina; requiere dosis menores.",
        "etnia": "Polimorfismos en CYP2C9 y VKORC1 varian segun etnia. Poblacion europea: VKORC1 1639G>A frecuente.",
        "otros": ["INR elevado (>3.0)", "Uso concomitante de AINEs", "Antecedentes de ulcera peptica"],
        "contradiccion": None
    },
    "epidemiologia": {
        "volumen_casos": "45823 casos reportados en FDA FAERS. Incidencia estimada 1-3% anual en pacientes anticoagulados.",
        "ficha_tecnica": "RAM conocida y listada como muy frecuente (>1/10) en la ficha tecnica aprobada por AEMPS.",
        "url_ft": "https://cima.aemps.es/cima/dochtml/ft/38414/FT_38414.html",
        "contradiccion": None
    },
    "no_seguridad": {
        "diana": "VKORC1 (vitamina K epoxi reductasa complejo 1) - gen VKORC1",
        "mecanismo": "La inhibicion de VKORC1 impide la regeneracion de vitamina K, esencial para la carboxilacion de factores de coagulacion. El resultado es disminucion de factores activos II, VII, IX y X en cascada de coagulacion.",
        "contradiccion": None
    },
    "farmacogenetica": {
        "biomarcadores": [
            {"gen": "VKORC1", "proteina": "Vitamina K epoxi reductasa", "impacto": "Determina sensibilidad a warfarina; variante 1639G>A reduce dosis necesaria 30-40%", "evidencia": "Alta (nivel 1A PharmGKB)"},
            {"gen": "CYP2C9", "proteina": "Citocromo P450 2C9", "impacto": "Metabolismo lento (*2/*3) aumenta exposicion a warfarina y riesgo hemorragico", "evidencia": "Alta (nivel 1A PharmGKB)"}
        ],
        "validacion": "Genotipado VKORC1 y CYP2C9 recomendado antes de iniciar tratamiento. FDA actualizo etiqueta en 2010.",
        "contradiccion": None
    },
    "regulatorio": {
        "estado_senal": "Sin senal",
        "accion": "RAM ya conocida e incluida en ficha tecnica. Monitoreo periodico de INR recomendado.",
        "conclusion": "La hemorragia gastrointestinal por warfarina es una RAM conocida y bien caracterizada. No se identifican nuevas senales en PRAC ni FDA. La accion recomendada es optimizar el control del INR y revisar factores de riesgo del paciente.",
        "contradiccion": "PRAC no reporta nuevas acciones, pero FDA FAERS muestra volumen alto (45823 casos). Sin contradiccion real: ambos reconocen el riesgo pero consideran suficiente la informacion actual en ficha tecnica."
    }
}


# ── Funciones que REPLICAN paso5_streaming sin LLM ────────────

def _seccion_mock(id_seccion: str, titulo: str, system: str, user: str, fuentes: list, mock_contenido: dict) -> dict:
    print(f"\n{'='*60}")
    print(f"SECCION: {titulo}")
    print(f"{'='*60}")
    print(f"\n[SYSTEM PROMPT]:\n{system}")
    print(f"\n[USER PROMPT]:\n{user}")
    print(f"\n[FUENTES]: {fuentes}")
    print(f"\n[CONTENIDO MOCK (lo que devolveria el LLM)]:")
    print(json.dumps(mock_contenido, ensure_ascii=False, indent=2))

    return {
        "id":        id_seccion,
        "titulo":    titulo,
        "estado":    "ok",
        "contenido": mock_contenido,
        "fuentes":   fuentes
    }


def _s1_mock(datos, evidencia):
    return _seccion_mock(
        "mecanistica", "1. Evaluacion Mecanistica",
        system="""Eres experto en Farmacologia Molecular. Analiza la plausibilidad biologica de una RAM.
Responde en JSON con exactamente estas claves:
{"plausibilidad": "Alta|Media|Baja|Desconocida", "resumen": "texto conciso max 3 frases", "contradiccion": "null o texto si hay contradiccion entre fuentes"}
Responde SOLO el JSON, sin texto adicional.""",
        user=f"""Farmaco: {datos.get('Farmaco', datos.get('Farmaco',''))}
RAM: {datos.get('RAM','')}
Datos Reactome: {evidencia.get('Reactome','No disponible')}
Datos PubMed: {evidencia.get('Pubmed','No disponible')[:1000]}""",
        fuentes=["Reactome", "PubMed"],
        mock_contenido=MOCK_CONTENIDOS["mecanistica"]
    )


def _s2_mock(datos, evidencia):
    return _seccion_mock(
        "factores_riesgo", "2. Factores de Riesgo",
        system="""Eres experto en Farmacovigilancia. Analiza factores de riesgo del paciente para esta RAM.
Responde en JSON con exactamente estas claves:
{"edad": "texto", "sexo": "texto", "etnia": "texto", "otros": ["item1","item2"], "contradiccion": "null o texto"}
Responde SOLO el JSON, sin texto adicional.""",
        user=f"""Farmaco: {datos.get('Farmaco', '')}
RAM: {datos.get('RAM','')}
Edad: {datos.get('Edad','')}
Sexo: {datos.get('sex_id','')}
Etnia: {datos.get('natio_dic_id','')}
Evidencia PubMed: {evidencia.get('Pubmed','No disponible')[:800]}""",
        fuentes=["Datos paciente", "PubMed"],
        mock_contenido=MOCK_CONTENIDOS["factores_riesgo"]
    )


def _s3_mock(datos, evidencia):
    return _seccion_mock(
        "epidemiologia", "3. Vigilancia Epidemiologica",
        system="""Eres experto en Farmacovigilancia. Resume la evidencia epidemiologica de esta RAM.
Responde en JSON con exactamente estas claves:
{"volumen_casos": "texto", "ficha_tecnica": "texto", "url_ft": "url o vacio", "contradiccion": "null o texto si FDA y ficha tecnica se contradicen"}
Responde SOLO el JSON, sin texto adicional.""",
        user=f"""Farmaco: {datos.get('Farmaco', '')}
RAM: {datos.get('RAM','')}
FDA FAERS: {evidencia.get('FDA','No disponible')}
Ficha Tecnica (RAM conocida): {datos.get('ram_resultado','desconocido')}
URL Ficha Tecnica: {datos.get('ft_url','')}
Texto Ficha Tecnica (extracto): {datos.get('texto_ft','')[:500]}""",
        fuentes=["FDA FAERS", "CIMA"],
        mock_contenido=MOCK_CONTENIDOS["epidemiologia"]
    )


def _s4_mock(datos, evidencia):
    return _seccion_mock(
        "no_seguridad", "4. Rutas de No Seguridad",
        system="""Eres experto en Biologia de Sistemas. Identifica dianas moleculares y rutas de no seguridad.
Responde en JSON con exactamente estas claves:
{"diana": "texto", "mecanismo": "texto max 2 frases", "contradiccion": "null o texto"}
Responde SOLO el JSON, sin texto adicional.""",
        user=f"""Farmaco: {datos.get('Farmaco', '')}
RAM: {datos.get('RAM','')}
Reactome: {evidencia.get('Reactome','No disponible')}""",
        fuentes=["Reactome"],
        mock_contenido=MOCK_CONTENIDOS["no_seguridad"]
    )


def _s5_mock(datos, evidencia):
    return _seccion_mock(
        "farmacogenetica", "5. Contexto Farmacogenetico",
        system="""Eres experto en Farmacogenetica. Identifica biomarcadores relevantes.
Responde en JSON con exactamente estas claves:
{"biomarcadores": [{"gen":"","proteina":"","impacto":"","evidencia":""}], "validacion": "texto", "contradiccion": "null o texto"}
Responde SOLO el JSON, sin texto adicional.""",
        user=f"""Farmaco: {datos.get('Farmaco', '')}
RAM: {datos.get('RAM','')}
PubMed: {evidencia.get('Pubmed','No disponible')[:800]}
Reactome: {evidencia.get('Reactome','No disponible')[:500]}""",
        fuentes=["PubMed", "Reactome"],
        mock_contenido=MOCK_CONTENIDOS["farmacogenetica"]
    )


def _s6_mock(datos, evidencia):
    return _seccion_mock(
        "regulatorio", "6. Estado Regulatorio",
        system="""Eres experto en Farmacovigilancia regulatoria. Determina el estado de la senal.
Responde en JSON con exactamente estas claves:
{"estado_senal": "Con senal|Sin senal|En evaluacion|Desconocido", "accion": "texto", "conclusion": "texto max 3 frases", "contradiccion": "null o texto si PRAC y FDA se contradicen"}
Responde SOLO el JSON, sin texto adicional.""",
        user=f"""Farmaco: {datos.get('Farmaco', '')}
RAM: {datos.get('RAM','')}
PRAC/EMA: {evidencia.get('PRACS','No disponible')}
FDA FAERS: {evidencia.get('FDA','No disponible')}
RAM en ficha tecnica: {datos.get('ram_resultado','desconocido')}""",
        fuentes=["PRAC/EMA", "FDA FAERS", "CIMA"],
        mock_contenido=MOCK_CONTENIDOS["regulatorio"]
    )


# ── Simulacion de eventos SSE ─────────────────────────────────

def event(tipo: str, payload: dict) -> str:
    return f"data: {json.dumps({'tipo': tipo, **payload}, ensure_ascii=False)}\n\n"


def simular_stream():
    print("\n" + "#"*60)
    print("# SIMULACION DE EVENTOS SSE (generate_stream)")
    print("#"*60)

    # Cabecera
    cab = event("cabecera", {
        "farmaco":    DATOS["Farmaco"],
        "ram":        DATOS["RAM"],
        "cod_dedra":  DATOS["cod_dedra"],
        "desc_dedra": DATOS["desc_dedra"],
        "caso":       "C001"
    })
    print(f"\n[SSE cabecera]:\n{cab}")

    # Secciones mock
    secciones = [
        _s1_mock(DATOS, EVIDENCIA),
        _s2_mock(DATOS, EVIDENCIA),
        _s3_mock(DATOS, EVIDENCIA),
        _s4_mock(DATOS, EVIDENCIA),
        _s5_mock(DATOS, EVIDENCIA),
        _s6_mock(DATOS, EVIDENCIA),
    ]

    print("\n" + "#"*60)
    print("# EVENTOS SSE QUE SE ENVIARIAN AL FRONTEND")
    print("#"*60)
    for sec in secciones:
        ev = event("seccion", {
            "id":        sec["id"],
            "titulo":    sec["titulo"],
            "estado":    sec["estado"],
            "contenido": sec["contenido"],
            "fuentes":   sec["fuentes"]
        })
        print(f"\n[SSE seccion '{sec['id']}']:")
        # Mostrar bonito
        parsed = json.loads(ev.replace("data: ", "").strip())
        print(json.dumps(parsed, ensure_ascii=False, indent=2))

    ev_fin = event("fin", {"mensaje": "Informe completado"})
    print(f"\n[SSE fin]:\n{ev_fin}")


if __name__ == "__main__":
    simular_stream()
