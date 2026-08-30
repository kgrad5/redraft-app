"""FastAPI application entrypoint."""

from importlib.metadata import PackageNotFoundError, version

from fastapi import FastAPI

try:
    __version__ = version("redraft")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.0.0+dev"

app = FastAPI(title="redraft")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}
