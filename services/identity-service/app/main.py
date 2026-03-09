from fastapi import FastAPI

app = FastAPI(title="Identity Service")

@app.get("/")
def read_root():
    return {"message": "Identity Service is online"}

@app.get("/healthz")
def liveness_probe():
    return {"status": "alive"}