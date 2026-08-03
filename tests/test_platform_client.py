# Created by yuanrui on 2026/08/01

from src.config.platform_client import extract_catalog_runtime


def test_extract_catalog_runtime():
    catalog = {
        "providers": {
            "google": {
                "name": "Google",
                "enabled": True,
                "priority": 5,
                "models": [
                    {"id": "gemini-2.5-flash", "name": "Flash", "enabled": True, "priority": 3},
                ],
            }
        }
    }
    runtime = extract_catalog_runtime(catalog)
    assert runtime["providers"]["google"]["enabled"] is True
    assert runtime["providers"]["google"]["priority"] == 5
    assert "name" not in runtime["providers"]["google"]
    assert runtime["providers"]["google"]["models"][0]["id"] == "gemini-2.5-flash"
    assert runtime["providers"]["google"]["models"][0]["enabled"] is True
