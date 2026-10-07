import os
import requests
from dotenv import load_dotenv

load_dotenv()

api_key = os.environ.get("LLM_API_KEY")
url = "https://api.groq.com/openai/v1/models"

headers = {
    "Authorization": f"Bearer {api_key}",
    "Content-Type": "application/json"
}

response = requests.get(url, headers=headers)
data = response.json()

# Extraer solo la lista de IDs de modelos
available_models = [model["id"] for model in data.get("data", [])]

target_model = "llama-3.3-70b-versatile"

# 1. Verificación directa
if target_model in available_models:
    print(f"✅ El modelo '{target_model}' SÍ está disponible.")
else:
    print(f"❌ El modelo '{target_model}' NO está disponible.")

# 2. Imprimir la lista limpia de todos los modelos disponibles
print("\nLista de modelos disponibles:")
for model_id in sorted(available_models):
    print(f"- {model_id}")