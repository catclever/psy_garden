import os
import re
import json
import urllib.request
import urllib.error

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ai_config.json")

DEFAULT_GOOGLE_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_GOOGLE_INTERACTIONS_URL = "https://generativelanguage.googleapis.com/v1/interactions"
DEFAULT_ANTHROPIC_BASE_URL = "https://api.anthropic.com/v1"


def load_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
    return {}


def build_prompt(plant_name: str, display_name: str = None) -> str:
    return f"""You are an expert botanist. Analyze the following plant: {display_name or plant_name} (Code: {plant_name}).
Provide a structured JSON response with the following keys exactly:
- "zh_name": Chinese name of the plant
- "scientific_name": Latin botanical name
- "botanical_height_m": A list of two floats representing [min_meters, max_meters]
- "plant_type": Must be one of ["tree", "shrub", "flower", "groundcover"]
- "growth_habit": Must be one of ["slender", "canopy", "bush", "carpet"]
- "suggested_game_height_px": A list of two integers representing [min_px, max_px], mapped to a 35 ~ 220 px scale based on the plant's typical height.
- "brief": A concise one-sentence morphological summary in Chinese.

Return ONLY a valid JSON object without markdown formatting, code blocks, or any other text."""


def resolve_provider(config: dict) -> str:
    """Resolve provider format; defaults to 'openai' when omitted."""
    raw = (config.get("provider") or config.get("format") or "openai").strip().lower()
    if raw in ("", "openai", "openai-compatible", "openai_compatible"):
        return "openai"
    if raw in ("google", "gemini", "google-genai"):
        return "google"
    if raw in ("google-interactions", "gemini-interactions", "interactions"):
        return "google-interactions"
    if raw in ("anthropic", "claude"):
        return "anthropic"
    return raw


def build_http_request(config: dict, prompt: str):
    """
    Build (endpoint, headers, payload_dict, provider) for the configured provider.
    Returns (None, None, None, provider) if required URL is missing.
    """
    provider = resolve_provider(config)
    raw_url = (config.get("url") or "").strip().rstrip("/")
    api_key = (config.get("key") or "").strip()

    headers = {"Content-Type": "application/json"}

    if provider == "openai":
        if not raw_url:
            return None, None, None, provider
        model = (config.get("model") or "gpt-4o-mini").strip()
        endpoint = raw_url if raw_url.endswith("/chat/completions") else f"{raw_url}/chat/completions"
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
        }
        return endpoint, headers, payload, provider

    if provider == "google" and raw_url.endswith("/interactions"):
        provider = "google-interactions"

    if provider == "google":
        model = (config.get("model") or "gemini-3.5-flash-lite").strip()
        clean_model = model[len("models/"):] if model.startswith("models/") else model
        base_url = raw_url or DEFAULT_GOOGLE_BASE_URL
        if ":generateContent" in base_url:
            endpoint = base_url
        else:
            if not re.search(r"/v1(?:beta)?$", base_url) and "/models" not in base_url:
                base_url = f"{base_url}/v1beta"
            if base_url.endswith("/models"):
                endpoint = f"{base_url}/{clean_model}:generateContent"
            else:
                endpoint = f"{base_url}/models/{clean_model}:generateContent"

        if api_key:
            headers["x-goog-api-key"] = api_key
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "responseMimeType": "application/json",
            },
        }
        return endpoint, headers, payload, provider

    if provider == "google-interactions":
        model = (config.get("model") or "gemini-4-hydrogen").strip()
        clean_model = model[len("models/"):] if model.startswith("models/") else model
        if not raw_url:
            endpoint = DEFAULT_GOOGLE_INTERACTIONS_URL
        elif raw_url.endswith("/interactions"):
            endpoint = raw_url
        elif re.search(r"/v1(?:beta)?$", raw_url):
            endpoint = f"{raw_url}/interactions"
        else:
            endpoint = f"{raw_url}/v1/interactions"

        if api_key:
            headers["x-goog-api-key"] = api_key
        payload = {
            "model": clean_model,
            "input": prompt,
            "store": False,
        }
        return endpoint, headers, payload, provider

    if provider == "anthropic":
        model = (config.get("model") or "claude-sonnet-4-5").strip()
        base_url = raw_url or DEFAULT_ANTHROPIC_BASE_URL
        if base_url.endswith("/messages"):
            endpoint = base_url
        elif base_url.endswith("/v1"):
            endpoint = f"{base_url}/messages"
        else:
            endpoint = f"{base_url}/v1/messages"

        if api_key:
            headers["x-api-key"] = api_key
        headers["anthropic-version"] = config.get("anthropic_version", "2023-06-01")
        payload = {
            "model": model,
            "max_tokens": 1024,
            "temperature": 0.2,
            "messages": [{"role": "user", "content": prompt}],
        }
        return endpoint, headers, payload, provider

    return None, None, None, provider


def extract_response_text(result_json: dict, provider: str) -> str:
    """Extract raw assistant text across OpenAI, Google Gemini, and Anthropic payloads."""
    if provider == "openai":
        return result_json["choices"][0]["message"]["content"].strip()

    if provider == "google":
        candidates = result_json.get("candidates") or []
        if not candidates:
            raise ValueError(f"Empty candidates in Google response: {result_json}")
        parts = (candidates[0].get("content") or {}).get("parts") or []
        # Skip thinking parts ("thought": True) emitted by Gemini reasoning models
        text_chunks = [
            p["text"]
            for p in parts
            if isinstance(p, dict) and not p.get("thought") and isinstance(p.get("text"), str)
        ]
        if not text_chunks:
            text_chunks = [p["text"] for p in parts if isinstance(p, dict) and isinstance(p.get("text"), str)]
        if not text_chunks:
            raise ValueError("No text parts found in Google candidate response")
        return "\n".join(text_chunks).strip()

    if provider == "google-interactions":
        if isinstance(result_json.get("output_text"), str) and result_json["output_text"].strip():
            return result_json["output_text"].strip()
        steps = result_json.get("steps") or []
        text_chunks = []
        for step in steps:
            if isinstance(step, dict) and step.get("type") == "model_output":
                for item in step.get("content") or []:
                    if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str):
                        text_chunks.append(item["text"])
        if not text_chunks:
            raise ValueError("No model_output text found in Google Interactions response")
        return "\n".join(text_chunks).strip()

    if provider == "anthropic":
        blocks = result_json.get("content") or []
        text_chunks = [
            b["text"] for b in blocks if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)
        ]
        if not text_chunks:
            raise ValueError("No text block found in Anthropic response")
        return "\n".join(text_chunks).strip()

    raise ValueError(f"Unsupported provider: {provider}")


def parse_json_content(content: str) -> dict:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        cleaned = cleaned.strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(cleaned[start : end + 1])
        raise


def analyze_plant_botany(plant_name: str, display_name: str = None, config_override: dict = None) -> dict:
    fallback = {
        "status": "skipped",
        "zh_name": display_name or plant_name,
        "scientific_name": "Unknown",
        "botanical_height_m": [0.5, 1.5],
        "plant_type": "shrub",
        "growth_habit": "bush",
        "suggested_game_height_px": [100, 150],
        "brief": "AI analysis skipped or failed.",
    }

    config = config_override if isinstance(config_override, dict) else load_config()
    if not config:
        return fallback

    prompt = build_prompt(plant_name, display_name)
    endpoint, headers, payload, provider = build_http_request(config, prompt)
    if not endpoint:
        if provider not in ("openai", "google", "google-interactions", "anthropic"):
            fallback["status"] = "failed"
            fallback["brief"] = f"Unsupported AI provider '{provider}'"
        return fallback

    timeout = config.get("timeout", 30)
    proxy_url = (config.get("proxy") or "").strip()

    req = urllib.request.Request(endpoint, data=json.dumps(payload).encode("utf-8"), method="POST")
    for k, v in headers.items():
        req.add_header(k, v)

    opener = (
        urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url}))
        if proxy_url
        else urllib.request.build_opener()
    )

    try:
        with opener.open(req, timeout=timeout) as response:
            if response.status == 200:
                body = response.read().decode("utf-8")
                result_json = json.loads(body)
                content = extract_response_text(result_json, provider)
                parsed = parse_json_content(content)
                parsed["status"] = "success"
                return parsed
            fallback["status"] = "failed"
            fallback["brief"] = f"API returned status {response.status}"
            return fallback
    except urllib.error.HTTPError as e:
        err_detail = ""
        try:
            err_body = e.read().decode("utf-8", errors="ignore").strip()
            if err_body:
                err_detail = f" ({err_body[:180]})"
        except Exception:
            pass
        fallback["status"] = "failed"
        fallback["brief"] = f"HTTP {e.code}: {e.reason}{err_detail}"
        return fallback
    except urllib.error.URLError as e:
        fallback["status"] = "failed"
        fallback["brief"] = f"Network or API error: {e.reason}"
        return fallback
    except Exception as e:
        fallback["status"] = "failed"
        fallback["brief"] = f"Unexpected error: {str(e)}"
        return fallback
