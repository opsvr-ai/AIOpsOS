"""Programmatic server launcher to work around WSL2 Dl process issue."""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("src.main:app", host="0.0.0.0", port=9789, reload=False)
