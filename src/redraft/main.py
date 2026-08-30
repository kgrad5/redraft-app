"""FastAPI application entrypoint."""

from importlib.metadata import version

from fastapi import FastAPI

app = FastAPI(title="redraft")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": version("redraft")}
