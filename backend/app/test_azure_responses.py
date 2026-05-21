import os

from openai import OpenAI

endpoint = "https://zeineb-louati-resource.services.ai.azure.com/openai/v1"
deployment_name = "grok-4-1-fast-reasoning"
api_key = os.getenv("AZURE_OPENAI_API_KEY")

if not api_key:
    raise SystemExit("Missing AZURE_OPENAI_API_KEY environment variable.")

client = OpenAI(
    base_url=endpoint,
    api_key=api_key
)

completion = client.chat.completions.create(
    model=deployment_name,
    messages=[
        {
            "role": "user",
            "content": "What is the capital of France?",
        }
    ],
)

print(completion.choices[0].message)
