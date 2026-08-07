"""Optional import + registar nedostajućih featurea."""
import importlib
_missing: dict[str, str] = {}

def need(module: str, feature: str):
    try:
        return importlib.import_module(module)
    except ImportError:
        _missing[feature] = f"pip install {module.replace('_','-').split('.')[0]}"
        return None

def missing() -> dict[str, str]:
    return dict(_missing)
