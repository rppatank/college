import importlib

for mod in ["streamlit", "pandas", "pyarrow", "requests", "httpx",
            "bs4", "lxml", "pytest", "ollama", "numpy", "sqlite3"]:
    try:
        m = importlib.import_module(mod)
        v = getattr(m, "__version__", "?")
        print(f"OK   {mod:14s} {v}")
    except ImportError:
        print(f"MISS {mod:14s}")