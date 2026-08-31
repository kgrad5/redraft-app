"""FastAPI application entrypoint."""

from importlib.metadata import PackageNotFoundError, version

from fastapi import FastAPI

from redraft.api.picks import router as picks_router

try:
    __version__ = version("redraft")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.0.0+dev"

app = FastAPI(title="redraft")
app.include_router(picks_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}
