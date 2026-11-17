import json
import os
import sys
from unittest import mock

# Ensure ai_botanist can be imported
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ai_botanist


def test_load_config():
    config = ai_botanist.load_config()
    assert isinstance(config, dict), "Config should be a dictionary"
    assert any(k in config for k in ("key", "api_key", "_key")), "Config must contain key or api_key"
    assert any(k in config for k in ("model", "default_model", "_model")), "Config must contain model or default_model"
    assert "timeout" in config, "Config must contain 'timeout'"
    print("test_load_config passed")


def test_default_openai_without_extra_param():
    # No 'provider' or 'format' key -> defaults cleanly to OpenAI-compatible
    cfg = {
        "url": "http://127.0.0.1:9999/v1",
        "key": "sk-test",
        "model": "deepseek-chat",
        "timeout": 10,
    }
    endpoint, headers, payload, provider = ai_botanist.build_http_request(cfg, "hello")
    assert provider == "openai"
    assert endpoint == "http://127.0.0.1:9999/v1/chat/completions"
    assert headers["Authorization"] == "Bearer sk-test"
    assert payload["model"] == "deepseek-chat"
    print("test_default_openai_without_extra_param passed")


def test_google_provider_and_thinking_extraction():
    # Google provider works with blank URL (official default) or custom relay URL
    cfg_official = {
        "provider": "google",
        "url": "",
        "key": "AIzaSyTestKey",
        "model": "gemini-3.5-flash-lite",
    }
    endpoint, headers, payload, provider = ai_botanist.build_http_request(cfg_official, "analyze")
    assert provider == "google"
    assert endpoint == "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent"
    assert headers["x-goog-api-key"] == "AIzaSyTestKey"
    assert "Authorization" not in headers
    assert payload["generationConfig"]["responseMimeType"] == "application/json"

    # Custom reverse-proxy base URL without /v1beta
    cfg_proxy = {
        "provider": "google",
        "url": "https://my-gemini-proxy.example.com",
        "key": "proxy-key",
        "model": "models/gemini-4-hydrogen",
    }
    ep2, _, _, _ = ai_botanist.build_http_request(cfg_proxy, "analyze")
    assert ep2 == "https://my-gemini-proxy.example.com/v1beta/models/gemini-4-hydrogen:generateContent"

    # Verify thinking parts ("thought": True) are ignored when extracting JSON
    mock_google_resp = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"thought": True, "text": "Let me think about Ginkgo biloba..."},
                        {
                            "text": json.dumps(
                                {
                                    "zh_name": "银杏",
                                    "scientific_name": "Ginkgo biloba",
                                    "botanical_height_m": [20.0, 35.0],
                                    "plant_type": "tree",
                                    "growth_habit": "canopy",
                                    "suggested_game_height_px": [180, 220],
                                    "brief": "落叶乔木，叶呈扇形。",
                                }
                            )
                        },
                    ]
                }
            }
        ]
    }

    class FakeResp:
        status = 200

        def read(self):
            return json.dumps(mock_google_resp).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    with mock.patch("urllib.request.OpenerDirector.open", return_value=FakeResp()):
        res = ai_botanist.analyze_plant_botany("plant_0134", "银杏", config_override=cfg_official)
        assert res["status"] == "success"
        assert res["scientific_name"] == "Ginkgo biloba"
        assert res["suggested_game_height_px"] == [180, 220]

    print("test_google_provider_and_thinking_extraction passed")


def test_google_interactions_and_anthropic_builders():
    cfg_inter = {
        "provider": "google-interactions",
        "url": "",
        "key": "AIzaSyInter",
        "model": "gemini-4-hydrogen",
    }
    ep, headers, payload, provider = ai_botanist.build_http_request(cfg_inter, "hi")
    assert provider == "google-interactions"
    assert ep == "https://generativelanguage.googleapis.com/v1/interactions"
    assert headers["x-goog-api-key"] == "AIzaSyInter"
    assert payload["store"] is False

    txt = ai_botanist.extract_response_text(
        {"steps": [{"type": "model_output", "content": [{"type": "text", "text": '{"ok":1}'}]}]},
        "google-interactions",
    )
    assert txt == '{"ok":1}'

    cfg_anth = {"provider": "anthropic", "url": "", "key": "sk-ant-123", "model": "claude-sonnet-4-5"}
    ep_a, h_a, _, p_a = ai_botanist.build_http_request(cfg_anth, "hi")
    assert p_a == "anthropic"
    assert ep_a == "https://api.anthropic.com/v1/messages"
    assert h_a["x-api-key"] == "sk-ant-123"
    print("test_google_interactions_and_anthropic_builders passed")


def test_fallback_on_skip():
    res = ai_botanist.analyze_plant_botany("test_plant_01", "Test Plant", config_override={"url": ""})
    assert res["status"] == "skipped"
    assert res["zh_name"] == "Test Plant"
    print("test_fallback_on_skip passed")


if __name__ == "__main__":
    test_load_config()
    test_default_openai_without_extra_param()
    test_google_provider_and_thinking_extraction()
    test_google_interactions_and_anthropic_builders()
    test_fallback_on_skip()
    print("All tests passed!")
