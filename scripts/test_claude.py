import boto3
import json

INFERENCE_PROFILE_ARN = "arn:aws:bedrock:eu-west-1:953246180205:inference-profile/eu.anthropic.claude-sonnet-4-6"
REGION="eu-west-1"
def test_claude( max_tokens: int = 1024) -> str:
    client = boto3.client("bedrock-runtime", region_name=REGION)
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "system": "contesta a la pregunta de forma breve y concisa",
        "messages": [{"role": "user", "content": "What is the capital of France?"}]
    })

    response = client.invoke_model(modelId=INFERENCE_PROFILE_ARN, body=body)
    texto = json.loads(response["body"].read())["content"][0]["text"].strip()
    
    return texto
if __name__ == "__main__":
    respuesta=test_claude()
    print(respuesta)