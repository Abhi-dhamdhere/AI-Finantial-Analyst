import requests

response = requests.post(
    "http://localhost:11434/api/generate",
    json={
        "model": "mistral",
        "prompt": "Explain revenue growth in simple terms",
        "stream": False
    }
)

print(response.json()["response"])