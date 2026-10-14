import uvicorn
import os

if __name__ == "__main__":
    host = os.environ.get("PLACET_HOST", "0.0.0.0")
    port = int(os.environ.get("PLACET_PORT", "8000"))
    print(f"Starting Placet Server on http://{host}:{port} ...")
    uvicorn.run("backend.app.main:app", host=host, port=port, reload=False)
