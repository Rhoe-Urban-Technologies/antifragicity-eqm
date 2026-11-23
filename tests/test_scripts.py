import argparse
import json

import pytest

import luxembourg_example as lux
import luxembourg_example_eq_model as lux_eq


def test_parse_window():
    assert lux.parse_window("61200,64800") == (61200, 64800)
    assert lux.parse_window("none") is None
    for bad in ("64800,61200", "17h", "1,2,3"):
        with pytest.raises(argparse.ArgumentTypeError):
            lux.parse_window(bad)


def test_load_settings_overrides_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"max_iterations": 3, "stress_window": [3600, 7200], "entropy_window": None}))
    settings = lux_eq.load_settings(str(path))
    assert settings["max_iterations"] == 3 and settings["stress_window"] == (3600, 7200)
    assert settings["learning_rate"] == lux_eq.EQ_SETTINGS["learning_rate"]
    assert lux_eq.load_settings() == {**lux_eq.EQ_SETTINGS,
                                      "stress_window": tuple(lux_eq.EQ_SETTINGS["stress_window"])}


def test_load_settings_rejects_unknown_keys(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"max_iteration": 3}))
    with pytest.raises(SystemExit, match="max_iteration"):
        lux_eq.load_settings(str(path))
