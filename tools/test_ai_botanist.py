import os
import sys

# Ensure ai_botanist can be imported
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import ai_botanist
except ImportError as e:
    print(f"Failed to import ai_botanist: {e}")
    sys.exit(1)

def test_load_config():
    config = ai_botanist.load_config()
    assert isinstance(config, dict), "Config should be a dictionary"
    assert "url" in config, "Config must contain 'url'"
    assert "key" in config, "Config must contain 'key'"
    assert "model" in config, "Config must contain 'model'"
    assert "timeout" in config, "Config must contain 'timeout'"
    print("test_load_config passed")

def test_fallback_with_empty_key():
    # Because we're not providing a valid real key (or an API that works without one),
    # this will either fail network or hit the API and fail, returning a fallback.
    result = ai_botanist.analyze_plant_botany("test_plant_01", "Test Plant")

    assert isinstance(result, dict), "Result should be a dictionary"
    assert "status" in result, "Result must contain 'status'"

    if result["status"] == "success":
        # It successfully analyzed (e.g., using a local API mock or real valid key)
        assert "zh_name" in result
        assert "scientific_name" in result
        assert "botanical_height_m" in result
        assert "plant_type" in result
        assert "growth_habit" in result
        assert "suggested_game_height_px" in result
        assert "brief" in result
    else:
        # It correctly fell back on failure
        assert result["status"] in ["failed", "skipped"], "Fallback status should be 'failed' or 'skipped'"
        assert result["zh_name"] == "Test Plant", "Fallback zh_name should match display_name"
        assert result["botanical_height_m"] == [0.5, 1.5], "Fallback botanical_height_m should be default"
        assert result["plant_type"] == "shrub", "Fallback plant_type should be default"
        assert result["growth_habit"] == "bush", "Fallback growth_habit should be default"
    print("test_fallback_with_empty_key passed")

if __name__ == "__main__":
    test_load_config()
    test_fallback_with_empty_key()
    print("All tests passed!")
