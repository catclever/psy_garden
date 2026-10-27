import os
import json
import traceback
import urllib.request
import urllib.error

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ai_config.json')

def load_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def analyze_plant_botany(plant_name: str, display_name: str = None) -> dict:
    fallback = {
        "status": "skipped",
        "zh_name": display_name or plant_name,
        "scientific_name": "Unknown",
        "botanical_height_m": [0.5, 1.5],
        "plant_type": "shrub",
        "growth_habit": "bush",
        "suggested_game_height_px": [100, 150],
        "brief": "AI analysis skipped or failed."
    }

    config = load_config()
    if not config:
        return fallback

    url = config.get("url", "").rstrip("/")
    if not url:
        return fallback

    api_key = config.get("key", "")
    model = config.get("model", "gpt-4o-mini")
    timeout = config.get("timeout", 30)

    endpoint = f"{url}/chat/completions"

    prompt = f"""You are an expert botanist. Analyze the following plant: {display_name or plant_name} (Code: {plant_name}).
Provide a structured JSON response with the following keys exactly:
- "zh_name": Chinese name of the plant
- "scientific_name": Latin botanical name
- "botanical_height_m": A list of two floats representing [min_meters, max_meters]
- "plant_type": Must be one of ["tree", "shrub", "flower", "groundcover"]
- "growth_habit": Must be one of ["slender", "canopy", "bush", "carpet"]
- "suggested_game_height_px": A list of two integers representing [min_px, max_px], mapped to a 35 ~ 220 px scale based on the plant's typical height.
- "brief": A concise one-sentence morphological summary in Chinese.

Return ONLY a valid JSON object without markdown formatting, code blocks, or any other text."""

    data = {
        "model": model,
        "messages": [
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.2
    }

    req = urllib.request.Request(endpoint, data=json.dumps(data).encode('utf-8'))
    req.add_header('Content-Type', 'application/json')
    if api_key:
        req.add_header('Authorization', f'Bearer {api_key}')

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            if response.status == 200:
                body = response.read().decode('utf-8')
                result_json = json.loads(body)
                content = result_json['choices'][0]['message']['content'].strip()

                # Try to parse the content as JSON. Handle potential markdown code block wrappers.
                if content.startswith("```json"):
                    content = content[7:]
                if content.endswith("```"):
                    content = content[:-3]
                content = content.strip()

                parsed = json.loads(content)
                parsed["status"] = "success"
                return parsed
            else:
                fallback["status"] = "failed"
                fallback["brief"] = f"API returned status {response.status}"
                return fallback
    except urllib.error.URLError as e:
        fallback["status"] = "failed"
        fallback["brief"] = f"Network or API error: {e.reason}"
        return fallback
    except Exception as e:
        fallback["status"] = "failed"
        fallback["brief"] = f"Unexpected error: {str(e)}"
        return fallback
