import json
import os
import boto3
from botocore.config import Config
from dotenv import load_dotenv

BEDROCK_CONFIG = Config(
    connect_timeout=8,
    read_timeout=55,
    retries={"max_attempts": 1, "mode": "standard"},
)


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


def get_response(messages: list[dict], system: str = "") -> str:
    """
    Envía el historial de mensajes a Claude Sonnet 4.6 via AWS Bedrock y devuelve la respuesta.

    Args:
        messages: lista de dicts {"role": "user"|"assistant", "content": str}
        system: prompt de sistema opcional (contexto del informe / instrucciones del asistente)

    Returns:
        Texto de respuesta del modelo.
    """
    load_dotenv(override=True)
    INFERENCE_PROFILE_ARN = os.environ["INFERENCE_PROFILE_ARN"]
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
        "max_tokens": 2048,
        **({"system": system} if system else {}),
        "messages": [
            {"role": m["role"], "content": m["content"]}
            for m in messages
        ],
    })

    print("\n" + "="*60)
    print(">>> [llm_client / chat] MENSAJES")
    for m in messages:
        contenido = str(m.get('content', ''))
        print(f"  [{m['role'].upper()}]: {contenido[:300]}{'...' if len(contenido) > 300 else ''}")
    print("="*60)

    response = client.invoke_model(modelId=INFERENCE_PROFILE_ARN, body=body)
    response_body = json.loads(response["body"].read())
    input_tokens, output_tokens = _extract_token_usage(response, response_body)

    content = response_body.get("content", [])
    result = content[0].get("text", "") if content else ""

    print("\n>>> [llm_client / chat] RESPUESTA LLM")
    print(result[:400] + ("..." if len(result) > 400 else ""))
    print(f">>> [llm_client / chat] TOKENS input={input_tokens} output={output_tokens}")
    print("="*60 + "\n")

    return result
