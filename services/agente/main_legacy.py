import os
import httpx
import logging
import json
from fastapi import UploadFile, HTTPException

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://ollama:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
DB_URL = os.getenv("DB_URL", "http://memoria-db:8083")
OCR_URL = os.getenv("OCR_URL", "http://ocr-vlm:8082")

async def call_ollama(prompt: str, format_json=True):
    async with httpx.AsyncClient() as client:
        payload = {"model": OLLAMA_MODEL, "prompt": prompt, "stream": False}
        if format_json: payload["format"] = "json"
        resp = await client.post(f"{OLLAMA_HOST}/api/generate", json=payload, timeout=600.0)
    if resp.status_code != 200:
        raise HTTPException(status_code=500, detail="Ollama Service failed")
    data = resp.json()
    return json.loads(data['response']) if format_json else data['response']

async def stream_orchestrator(file: UploadFile, task_type: str, workspace_id: str = "default"):
    file_content = await file.read()
    workspace_dir = os.environ.get("WORKSPACE_DIR", "/app/data/workspaces")
    
    async with httpx.AsyncClient() as client:
        files = {'file': (file.filename, file_content, file.content_type)}
        ocr_text = ""
        try:
            async with client.stream("POST", f"{OCR_URL}/ocr/process", files=files, timeout=900.0) as resp:
                async for line in resp.aiter_lines():
                    if not line: continue
                    chunk = json.loads(line)
                    if chunk["status"] == "complete":
                        ocr_data = chunk["data"]
                        ocr_text = "\n".join([page['text'] for page in ocr_data])
                        
                        # Guardar el texto extraído para el usuario
                        try:
                            path = os.path.join(workspace_dir, workspace_id)
                            os.makedirs(path, exist_ok=True)
                            txt_filename = f"extracccion_{task_type}_{file.filename}.txt"
                            with open(os.path.join(path, txt_filename), "w", encoding="utf-8") as f:
                                f.write(ocr_text)
                        except: pass
                    else:
                        yield line + "\n"
        except Exception as e:
            yield json.dumps({"status": "error", "msg": str(e)}) + "\n"
            return

        if task_type == "cif":
            prompt = f"Extract tax data JSON: rfc, razon_social, representante_legal, domicilio_fiscal. Text: {ocr_text[:6000]}"
            data = await call_ollama(prompt)
            yield json.dumps({"status": "complete", "data": data}) + "\n"
        elif task_type == "acta":
            prompt = f"Summarize Acta Constitutiva JSON: escritura_numero, fecha_constitucion, socios, representantes. Text: {ocr_text[:15000]}"
            data = await call_ollama(prompt)
            yield json.dumps({"status": "complete", "data": data}) + "\n"
