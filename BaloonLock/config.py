"""
config.py  -  reading the project's JSON files SAFELY:
  - turret_config.json : distance calibration (K), written by --calibrate-distance
  - presets.json       : HSV color presets (checked by validate_presets(), used
                         by balloon_detect.load_presets())

A broken or hand-edited file gives a clear message saying WHAT is wrong and
WHERE, instead of a Python stack trace.
"""

import json
import os


# Auto-tuned (non-safety) parameters written by tune_params.py.
AUTO_TUNE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config", "auto_tune.json")

# Distance calibration (K) is saved here, next to this script.
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "turret_config.json")

# Allowed ranges for every HSV preset value (OpenCV: hue 0-179, sat/val 0-255).
PRESET_RANGES = {"h_min": (0, 179), "h_max": (0, 179), "s_min": (0, 255), "s_max": (0, 255),
                 "v_min": (0, 255), "v_max": (0, 255), "min_area": (0, 1_000_000)}


class ConfigError(Exception):
    """A config file is missing values or has bad ones. The message says what to fix."""


def read_json(path):
    """Read a JSON file. Raises ConfigError (with line/column) if it's broken."""
    try:
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)
    except json.JSONDecodeError as error:
        raise ConfigError(f"{os.path.basename(path)} is not valid JSON: {error.msg} "
                          f"(line {error.lineno}, column {error.colno}). "
                          f"Fix it, or delete it to start over.") from None


def validate_presets(data, path="presets.json"):
    """Check every preset in a presets.json dictionary. Returns the presets
    (without "_comment"-style notes) or raises ConfigError listing ALL problems."""
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: the file must contain {{\"name\": {{...}}, ...}}")
    problems, presets = [], {}
    for name, values in data.items():
        if name.startswith("_"):
            continue                                # notes like "_comment"
        if not isinstance(values, dict):
            problems.append(f"preset '{name}' must be a {{...}} of values")
            continue
        for key, (low, high) in PRESET_RANGES.items():
            if key not in values:
                problems.append(f"preset '{name}' is missing '{key}'")
            elif not isinstance(values[key], int) or isinstance(values[key], bool):
                problems.append(f"preset '{name}': '{key}' must be a whole number, "
                                f"got {values[key]!r}")
            elif not low <= values[key] <= high:
                problems.append(f"preset '{name}': '{key}' = {values[key]} is outside {low}-{high}")
        # (h_min > h_max is allowed: that's how red wraps around the hue circle)
        for low_key, high_key in (("s_min", "s_max"), ("v_min", "v_max")):
            low_val, high_val = values.get(low_key), values.get(high_key)
            if isinstance(low_val, int) and isinstance(high_val, int) and low_val > high_val:
                problems.append(f"preset '{name}': {low_key} ({low_val}) is bigger than "
                                f"{high_key} ({high_val})")
        presets[name] = values
    if problems:
        raise ConfigError(f"{path} has problems:\n  - " + "\n  - ".join(problems))
    return presets


def load_auto_tune(path=AUTO_TUNE_FILE):
    """Read the tuner's parameters. Returns (values, warnings).

    SAFETY: only the keys listed in control.TUNING_RANGES are accepted, and
    only inside their allowed ranges. Anything else - e.g. "LASER_ENABLED",
    "LOCK_FRAMES", "min_radius" - is IGNORED with a warning. So this file can
    change how the turret tracks, never a safety rule."""
    from control import TUNING_RANGES
    if not os.path.exists(path):
        return {}, [f"{os.path.basename(path)} not found - using built-in defaults"]
    data = read_json(path)
    values = data.get("parameters", data) if isinstance(data, dict) else {}
    accepted, warnings = {}, []
    for key, value in values.items():
        if key not in TUNING_RANGES:
            warnings.append(f"ignored '{key}': not a tunable parameter")
            continue
        low, high = TUNING_RANGES[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            warnings.append(f"ignored '{key}': not a number ({value!r})")
        elif not low <= value <= high:
            warnings.append(f"ignored '{key}' = {value}: outside its allowed range {low}-{high}")
        else:
            accepted[key] = float(value)
    return accepted, warnings


def validate_calibration(config):
    """Check turret_config.json's distance calibration. Raises ConfigError."""
    if not isinstance(config, dict):
        raise ConfigError("turret_config.json must contain {...}")
    calibration = config.get("distance_calibration", {})
    if not isinstance(calibration, dict):
        raise ConfigError("turret_config.json: 'distance_calibration' must be {...}")
    for detector, entry in calibration.items():
        if not isinstance(entry, dict):
            raise ConfigError(f"turret_config.json: calibration '{detector}' must be {{...}}")
        for key in ("k", "frame_width"):
            value = entry.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                raise ConfigError(f"turret_config.json: calibration '{detector}' needs a "
                                  f"positive number for '{key}', got {value!r}. "
                                  f"Re-run --calibrate-distance.")
    return config


def load_config():
    """Read turret_config.json (or an empty config if it doesn't exist yet).
    Raises ConfigError with a clear message if the file is broken."""
    if not os.path.exists(CONFIG_FILE):
        return {}
    return validate_calibration(read_json(CONFIG_FILE))


def save_config(config):
    with open(CONFIG_FILE, "w", encoding="utf-8") as file:
        json.dump(config, file, indent=2)


def distance_k(config, detector_name, frame_width):
    """The calibrated K for this detector, adjusted to the current picture
    width (a bigger picture makes everything more pixels wide), or None."""
    entry = config.get("distance_calibration", {}).get(detector_name)
    if entry is None:
        return None
    return entry["k"] * frame_width / entry["frame_width"]
