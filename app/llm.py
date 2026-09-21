import json

import requests

from app.config import OLLAMA_MODEL, OLLAMA_URL

GENERATE_TIMEOUT = 120


class OllamaError(Exception):
    pass


def generate(prompt: str, json_format: bool = True, num_predict: int | None = None) -> str:
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
    }
    if json_format:
        payload["format"] = "json"
    if num_predict is not None:
        # Ollama's default num_predict can be low enough on some models to
        # truncate a multi-paragraph email mid-sentence — callers writing
        # longer prose pass an explicit floor here.
        payload["options"] = {"num_predict": num_predict}

    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/generate", json=payload, timeout=GENERATE_TIMEOUT
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        raise OllamaError(f"Could not reach Ollama at {OLLAMA_URL}: {e}") from e

    data = resp.json()
    response = data.get("response", "")
    if not response:
        raise OllamaError("Ollama returned an empty response.")
    return response


def parse_json_response(raw: str) -> dict:
    """Ollama's JSON mode is usually clean, but this tolerates stray text
    around the JSON object that some models still emit."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(raw[start : end + 1])
        raise
