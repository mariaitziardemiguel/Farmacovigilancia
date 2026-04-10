# PharmaGenIA — Farmacovigilancia con IA

Plataforma web que automatiza la búsqueda de evidencia sobre reacciones adversas a medicamentos (RAM) y genera resultados estructurados mediante Claude (AWS Bedrock).

## Flujo

```
Fármaco + RAM (MedDRA) + datos paciente
        │
        ▼
Búsqueda paralela en 7 fuentes
OpenFDA · PubMed · CIMA · PRAC/EMA · DrugBank · PharmGKB · Reactome
        │
        ▼
Análisis en 3 dimensiones (LLM en paralelo)
D1 Mecanística · D2 Clínica · D3 Regulatoria
        │
        ▼
Chat interactivo sobre los resultados generados
```

## Stack

- **Backend**: FastAPI + Python 3.10+
- **LLM**: Claude Sonnet 4.6 vía AWS Bedrock (`eu-west-1`)
- **Terminología**: MedDRA v28.1 (local) · RxNorm · DrugBank (JSON)
- **Frontend**: HTML/CSS/JS vanilla

## Instalación


pip install -r requirements.txt
uvicorn src.app.app_fastapi:app --reload --port 8000


Requiere credenciales AWS con acceso a Bedrock y los datos locales en `data/` (MedDRA v28.1, DrugBank JSON).

## Estructura relevante

```
src/
  app/app_fastapi.py            # Servidor y endpoints
  app/static/form.html          # Interfaz web
  app/utils/llm_client.py       # Cliente Bedrock
  pipeline/paso4_evidencia.py   # Búsqueda en 7 fuentes
  pipeline/paso5_streaming.py   # Generación del análisis
  pipeline/terminology.py       # MedDRA y RxNorm
data/
  MedDRA v28.1/                 # Terminología local
  drugbank/                     # Perfiles farmacológicos
  prac_signals.json             # Actas PRAC procesadas
scripts/                        # Tests de integración por fuente
```
