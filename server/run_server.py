"""Programmatic server launcher to work around WSL2 Dl process issue."""
import uvicorn

if __name__ == "__main__":
    from src.core.logging import setup_logging
    setup_logging()
    uvicorn.run("src.main:app", host="0.0.0.0", port=8000, reload=False)
