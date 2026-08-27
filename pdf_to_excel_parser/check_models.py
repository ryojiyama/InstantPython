import os
from google import genai
from dotenv import load_dotenv

load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=api_key)

print("利用可能なモデル一覧:")
for model in client.models.list():
    if "vision" in model.name or "flash" in model.name or "pro" in model.name:
        print(model.name)
