import os
import httpx
import logging
import json
import traceback
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Optional, Dict
from fastapi.middleware.cors import CORSMiddleware

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("agente-gateway")

app = FastAPI(title="LicitAI Agente Gateway")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from agents import CoordinatorAgent
from agents.identity_validator import IdentityValidatorAgent
from agents.legal_extractor import LegalExtractor

# Configuration for Agents
config = {
    "OLLAMA_HOST": os.getenv("OLLAMA_HOST", "http://ollama:11434"),
    "OLLAMA_MODEL": os.getenv("OLLAMA_MODEL", "llama3.1:8b"),
    "DB_URL": os.getenv("DB_URL", "http://memoria-db:8083"),
    "OCR_URL": os.getenv("OCR_URL", "http://ocr-vlm:8082"),
    "DOCX_URL": os.getenv("DOCX_URL", "http://generador-docx:8081"),
    "WORKSPACE_DIR": "/app/data/workspaces"
}

# Initialize the Gerente (Coordinator)
logger.info(f"Iniciando Gerente con OCR_URL: {config['OCR_URL']}")
gerente = CoordinatorAgent(config)
identity_validator = IdentityValidatorAgent(config["DB_URL"], config["WORKSPACE_DIR"])
legal_extractor = LegalExtractor()

@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "agente-gateway", "mode": "multi-agent"}

@app.post("/api/workspaces/sync")
async def sync_workspace(data: Dict):
    """Sincroniza el estado del cuaderno con la DB central"""
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{config['DB_URL']}/db/workspaces", json=data)
        return resp.json()

async def stream_orchestrator_agentic(file: UploadFile, workspace_id: str):
    content = await file.read()
    
    async def generator():
        # We use a simple trick to yield from the agent's callback
        import asyncio
        queue = asyncio.Queue()
        
        async def put_in_queue(msg):
            await queue.put(msg)

        # Run coordinator in background task
        task = asyncio.create_task(gerente.run_tender_analysis(
            content, file.filename, file.content_type, put_in_queue, workspace_id
        ))
        
        while not task.done() or not queue.empty():
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=1.0)
                yield msg
            except asyncio.TimeoutError:
                continue
        
        if task.exception():
            yield json.dumps({"status": "error", "msg": str(task.exception())}) + "\n"

    return generator()

class ChatRequest(BaseModel):
    workspace_id: str
    question: str
    sources: list = []

class HumanizeCard(BaseModel):
    workspace_id: str
    card_type: str = "critical_rules"

class NERRequest(BaseModel):
    workspace_id: str
    text: Optional[str] = None
    max_chars: int = 15000

class ComplianceRequest(BaseModel):
    workspace_id: str
    entidad: Optional[str] = None

class CriticalEvidenceRequest(BaseModel):
    workspace_id: str

class CriticalRecomputeRequest(BaseModel):
    workspace_id: str

@app.post("/api/chat")
async def chat_endpoint(req: ChatRequest):
    """Interfaz de chat con el Gerente General (Coordinator)"""
    return await gerente.answer_question(req.workspace_id, req.question, sources=req.sources)

@app.post("/api/humanize-card")
async def humanize_card(req: HumanizeCard):
    try:
        async with httpx.AsyncClient() as client:
            ws_resp = await client.get(f"{config['DB_URL']}/db/workspaces/{req.workspace_id}", timeout=5.0)
            if ws_resp.status_code != 200:
                raise HTTPException(status_code=404, detail="Workspace no encontrado")
            ws = ws_resp.json()
            analysis = json.loads(ws.get("analysis") or "{}")
            if req.card_type == "critical_rules":
                crit = analysis.get("puntos_criticos") or {}
                dirigido_a = crit.get("dirigido_a") or "N/D"
                firma = crit.get("firma_requerida") or "N/D"
                lugar = crit.get("lugar_entrega") or "N/D"
                advs = crit.get("advertencias") or []
                prompt = f"Eres un asesor legal. Explica en español, con tono humano y claro, en viñetas y frases cortas: DIRIGIR A: {dirigido_a}; FIRMA: {firma}; ENTREGA EN: {lugar}; ADVERTENCIAS: {', '.join(advs) if isinstance(advs, list) else advs}. Devuelve solo texto."
            else:
                prompt = "Explica de forma humana y clara el contenido de la tarjeta seleccionada."
            payload = {"model": config["OLLAMA_MODEL"], "prompt": prompt, "stream": False}
            gen = await client.post(f"{config['OLLAMA_HOST']}/api/generate", json=payload, timeout=60.0)
            if gen.status_code == 200:
                text = gen.json().get("response", "").strip()
                return {"status": "success", "data": {"text": text}}
            return {"status": "error", "msg": f"Ollama error {gen.status_code}"}
    except Exception as e:
        logger.error(f"Humanize card error: {e}")
        return {"status": "error", "msg": str(e)}

@app.post("/api/ner")
async def ner_endpoint(req: NERRequest):
    try:
        # Construir texto base
        text = (req.text or "").strip()
        if not text:
            async with httpx.AsyncClient() as client:
                ws_resp = await client.get(f"{config['DB_URL']}/db/workspaces/{req.workspace_id}", timeout=5.0)
                if ws_resp.status_code != 200:
                    raise HTTPException(status_code=404, detail="Workspace no encontrado")
                ws = ws_resp.json()
                analysis = {}
                try:
                    analysis = json.loads(ws.get("analysis") or "{}")
                except Exception:
                    analysis = {}
                fragments = [
                    analysis.get("convocante") or "",
                    analysis.get("objeto") or "",
                    analysis.get("numero_licitacion") or "",
                    json.dumps(analysis.get("puntos_criticos") or {}, ensure_ascii=False),
                ]
                text = "\n\n".join([f for f in fragments if f]).strip()
                # Fallback: intenta leer primer extraccion_*.txt del workspace
                if len(text) < 100:
                    wdir = os.path.join(config["WORKSPACE_DIR"], req.workspace_id)
                    if os.path.isdir(wdir):
                        for fn in os.listdir(wdir):
                            if fn.lower().startswith("extraccion_") and fn.lower().endswith(".txt"):
                                try:
                                    with open(os.path.join(wdir, fn), "r", encoding="utf-8") as f:
                                        text = f.read()
                                        break
                                except Exception:
                                    continue
        if not text:
            return {"status": "success", "data": {"entities": []}}
        text = text[:req.max_chars]
        prompt = f"""Extrae entidades del siguiente texto en formato JSON. Tipos: PER (persona), ORG (organización), LOC (lugar), DATE (fecha), MONEY (monto), LAW (término legal).
Responde SOLO un array JSON de objetos con campos: type, text.
Texto:
{text}"""
        async with httpx.AsyncClient() as client:
            payload = {"model": config["OLLAMA_MODEL"], "prompt": prompt, "stream": False}
            gen = await client.post(f"{config['OLLAMA_HOST']}/api/generate", json=payload, timeout=60.0)
            entities = []
            if gen.status_code == 200:
                raw = gen.json().get("response", "").strip()
                if raw.startswith("```json"):
                    raw = raw.split("```json")[-1].split("```")[0].strip()
                elif raw.startswith("```"):
                    raw = raw.split("```")[-1].split("```")[0].strip()
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, list):
                        for e in parsed:
                            if isinstance(e, dict) and e.get("text") and e.get("type"):
                                entities.append({"type": e.get("type"), "text": e.get("text")})
                except Exception:
                    pass
            # Fallback: construir entidades desde la DB si el modelo no devolvió nada util
            if not entities:
                try:
                    ws_resp = await client.get(f"{config['DB_URL']}/db/workspaces/{req.workspace_id}", timeout=5.0)
                    if ws_resp.status_code == 200:
                        ws = ws_resp.json()
                        cif = {}
                        acta = {}
                        analysis = {}
                        try: cif = json.loads(ws.get("cif_data") or "{}")
                        except: cif = {}
                        try: acta = json.loads(ws.get("acta_data") or "{}")
                        except: acta = {}
                        try: analysis = json.loads(ws.get("analysis") or "{}")
                        except: analysis = {}
                        if analysis.get("convocante"):
                            entities.append({"type": "ORG", "text": analysis["convocante"]})
                        if analysis.get("numero_licitacion"):
                            entities.append({"type": "LAW", "text": analysis["numero_licitacion"]})
                        fechas = analysis.get("fechas_clave") or {}
                        for k in ["visita", "aclaraciones", "apertura", "fallo"]:
                            if fechas.get(k):
                                entities.append({"type": "DATE", "text": fechas[k]})
                        if acta.get("representante_legal") or acta.get("representante"):
                            entities.append({"type": "PER", "text": acta.get("representante_legal") or acta.get("representante")})
                        if cif.get("razon_social"):
                            entities.append({"type": "ORG", "text": cif["razon_social"]})
                        if cif.get("domicilio_fiscal") or cif.get("domicilio"):
                            entities.append({"type": "LOC", "text": cif.get("domicilio_fiscal") or cif.get("domicilio")})
                        if cif.get("rfc"):
                            entities.append({"type": "LAW", "text": cif["rfc"]})
                except Exception:
                    pass
            return {"status": "success", "data": {"entities": entities}}
    except Exception as e:
        logger.error(f"NER error: {e}")
        return {"status": "error", "msg": str(e), "data": {"entities": []}}

@app.post("/api/compliance/apply")
async def compliance_apply(req: ComplianceRequest):
    try:
        async with httpx.AsyncClient() as client:
            ws_resp = await client.get(f"{config['DB_URL']}/db/workspaces/{req.workspace_id}", timeout=5.0)
            if ws_resp.status_code != 200:
                raise HTTPException(status_code=404, detail="Workspace no encontrado")
            ws = ws_resp.json()
            analysis = {}
            try:
                analysis = json.loads(ws.get("analysis") or "{}")
            except Exception:
                analysis = {}
            sources_raw = ws.get("sources") or "[]"
            try:
                sources = json.loads(sources_raw) if isinstance(sources_raw, str) else (sources_raw or [])
            except Exception:
                sources = []
            filenames = []
            for s in sources:
                nm = s.get("name") or s.get("filename") or ""
                if isinstance(nm, str):
                    filenames.append(nm.lower())
            def has_any(keys: list[str]) -> bool:
                for fn in filenames:
                    for k in keys:
                        if k in fn:
                            return True
                return False
            def evidence_for(keys: list[str]) -> Optional[str]:
                for s in sources:
                    nm = (s.get("name") or s.get("filename") or "")
                    lnm = nm.lower()
                    for k in keys:
                        if k in lnm:
                            return nm
                return None
            entidad = (req.entidad or analysis.get("tipo_entidad") or "federal").lower()
            crit = analysis.get("puntos_criticos") or {}
            advs = crit.get("advertencias") or []
            idioma_detectado = ("nota_idioma" in analysis and isinstance(analysis.get("nota_idioma"), str) and "español" in analysis.get("nota_idioma").lower()) or any(isinstance(a, str) and "español" in a.lower() for a in advs)
            fianzas_val = analysis.get("fianzas_requeridas")
            det_fianzas = bool(fianzas_val) or has_any(["fianza", "garantia", "garantía"])
            items = []
            items.append({
                "punto": "Presentar documentos obligatorios.",
                "motivo_riesgo": "No cumplir con requisitos de fondo.",
                "accion_preventiva": "Verificar y presentar documentos obligatorios.",
                "detectado": has_any(["base", "convocatoria", "anexo", "formato"]),
                "sugerido": False,
                "evidencia": evidence_for(["base", "convocatoria"])
            })
            items.append({
                "punto": "Entregar fianzas requeridas.",
                "motivo_riesgo": "No entregar fianzas requeridas.",
                "accion_preventiva": "Verificar y entregar fianzas requeridas.",
                "detectado": det_fianzas,
                "sugerido": not det_fianzas,
                "evidencia": evidence_for(["fianza", "garantia", "garantía"])
            })
            items.append({
                "punto": "Adjuntar Constancia de Situación Fiscal (CIF).",
                "motivo_riesgo": "Identidad fiscal no verificada.",
                "accion_preventiva": "Anexar la CIF vigente del licitante.",
                "detectado": has_any(["cif", "fiscal", "constancia"]),
                "sugerido": not has_any(["cif", "fiscal", "constancia"]),
                "evidencia": evidence_for(["cif", "fiscal", "constancia"])
            })
            items.append({
                "punto": "Adjuntar Acta Constitutiva y poderes.",
                "motivo_riesgo": "Representación legal no acreditada.",
                "accion_preventiva": "Anexar acta y poderes del representante.",
                "detectado": has_any(["acta", "constitutiva", "escritura"]),
                "sugerido": not has_any(["acta", "constitutiva", "escritura"]),
                "evidencia": evidence_for(["acta", "constitutiva", "escritura"])
            })
            items.append({
                "punto": "Firmar todas las fojas y el sobre.",
                "motivo_riesgo": "Descalificación por falta de firma.",
                "accion_preventiva": "Verificar firma en cada foja y sobre.",
                "detectado": False,
                "sugerido": True,
                "evidencia": None
            })
            lugar = crit.get("lugar_entrega")
            items.append({
                "punto": "Entrega física en el lugar y fecha señalados.",
                "motivo_riesgo": "Entrega fuera de tiempo o sede.",
                "accion_preventiva": "Confirmar sede y horario de entrega.",
                "detectado": bool(lugar),
                "sugerido": not bool(lugar),
                "evidencia": lugar
            })
            items.append({
                "punto": "Propuesta en idioma español.",
                "motivo_riesgo": "Rechazo por idioma incorrecto.",
                "accion_preventiva": "Redactar toda la propuesta en español.",
                "detectado": idioma_detectado,
                "sugerido": not idioma_detectado,
                "evidencia": None
            })
            analysis["checklist_cumplimiento"] = items
            payload = {
                "id": ws["id"],
                "name": ws.get("name") or ws["id"],
                "analysis": json.dumps(analysis, ensure_ascii=False)
            }
            upd = await client.post(f"{config['DB_URL']}/db/workspaces", json=payload, timeout=5.0)
            ok = upd.status_code in (200, 201)
            return {"status": "success" if ok else "error", "data": {"checklist_count": len(items), "entidad": entidad}}
    except Exception as e:
        logger.error(f"Compliance error: {e}")
        return {"status": "error", "msg": str(e)}

@app.post("/api/critical-rules/evidence")
async def critical_rules_evidence(req: CriticalEvidenceRequest):
    try:
        async with httpx.AsyncClient() as client:
            ws_resp = await client.get(f"{config['DB_URL']}/db/workspaces/{req.workspace_id}", timeout=5.0)
            if ws_resp.status_code != 200:
                raise HTTPException(status_code=404, detail="Workspace no encontrado")
            ws = ws_resp.json()
            analysis = {}
            try:
                analysis = json.loads(ws.get("analysis") or "{}")
            except Exception:
                analysis = {}
            crit = analysis.get("puntos_criticos") or {}
            dirigido_a = crit.get("dirigido_a") or ""
            firma = crit.get("firma_requerida") or ""
            lugar = crit.get("lugar_entrega") or ""
            advs = crit.get("advertencias") or []
            base_path = os.path.join(config["WORKSPACE_DIR"], req.workspace_id)
            texts = []
            files = []
            if os.path.isdir(base_path):
                for fn in os.listdir(base_path):
                    if fn.lower().startswith("extraccion_") and fn.lower().endswith(".txt"):
                        try:
                            with open(os.path.join(base_path, fn), "r", encoding="utf-8") as f:
                                txt = f.read()
                                texts.append((fn, txt))
                                files.append(fn)
                        except Exception:
                            continue
            def find_snippet(value: str):
                val = (value or "").strip()
                if not val:
                    return None
                low_val = val.lower()
                for name, txt in texts:
                    low_txt = txt.lower()
                    idx = low_txt.find(low_val[:80] if len(low_val) > 80 else low_val)
                    if idx >= 0:
                        start = max(0, idx - 80)
                        end = min(len(txt), idx + len(val) + 80)
                        return {"file": name, "snippet": txt[start:end].replace("\n", " ")}
                for name, txt in texts:
                    low_txt = txt.lower()
                    tokens = [t for t in low_val.split() if len(t) >= 5][:3]
                    hits = 0
                    for t in tokens:
                        if t in low_txt:
                            hits += 1
                    if hits >= max(1, len(tokens) - 1):
                        idx = -1
                        for t in tokens:
                            p = low_txt.find(t)
                            if p >= 0:
                                idx = p
                                break
                        if idx >= 0:
                            start = max(0, idx - 80)
                            end = min(len(txt), idx + 160)
                            return {"file": name, "snippet": txt[start:end].replace("\n", " ")}
                return None
            result = {
                "dirigido_a": None,
                "firma_requerida": None,
                "lugar_entrega": None,
                "advertencias": []
            }
            ev_dir = find_snippet(dirigido_a)
            if ev_dir:
                result["dirigido_a"] = {"found": True, "evidence": ev_dir}
            else:
                result["dirigido_a"] = {"found": False}
            ev_fir = find_snippet(firma)
            if ev_fir:
                result["firma_requerida"] = {"found": True, "evidence": ev_fir}
            else:
                result["firma_requerida"] = {"found": False}
            ev_lug = find_snippet(lugar)
            if ev_lug:
                result["lugar_entrega"] = {"found": True, "evidence": ev_lug}
            else:
                result["lugar_entrega"] = {"found": False}
            if isinstance(advs, list):
                for a in advs:
                    ev = find_snippet(a if isinstance(a, str) else "")
                    if ev:
                        result["advertencias"].append({"text": a, "found": True, "evidence": ev})
                    else:
                        result["advertencias"].append({"text": a, "found": False})
            return {"status": "success", "data": result}
    except Exception as e:
        logger.error(f"Critical rules evidence error: {e}")
        return {"status": "error", "msg": str(e)}

@app.post("/api/critical-rules/recompute")
async def critical_rules_recompute(req: CriticalRecomputeRequest):
    try:
        base_path = os.path.join(config["WORKSPACE_DIR"], req.workspace_id)
        texts = []
        if os.path.isdir(base_path):
            for fn in os.listdir(base_path):
                if fn.lower().startswith("extraccion_") and fn.lower().endswith(".txt"):
                    try:
                        with open(os.path.join(base_path, fn), "r", encoding="utf-8") as f:
                            txt = f.read()
                            texts.append(txt)
                    except Exception:
                        continue
        text_join = "\n".join(texts).lower()
        portal = ("comprasmx" in text_join) or ("buengobierno.gob.mx" in text_join) or ("plataforma" in text_join and "electr" in text_join)
        dirigido_detect = None
        if "instituto nacional de psiquiatría ramón de la fuente muñiz" in text_join:
            dirigido_detect = "Instituto Nacional de Psiquiatría Ramón de la Fuente Muñiz (vía ComprasMX)"
        elif "instituto nacional de" in text_join:
            dirigido_detect = "Convocante (vía ComprasMX)"
        async with httpx.AsyncClient() as client:
            ws_resp = await client.get(f"{config['DB_URL']}/db/workspaces/{req.workspace_id}", timeout=5.0)
            if ws_resp.status_code != 200:
                raise HTTPException(status_code=404, detail="Workspace no encontrado")
            ws = ws_resp.json()
            analysis = {}
            try:
                analysis = json.loads(ws.get("analysis") or "{}")
            except Exception:
                analysis = {}
            crit = analysis.get("puntos_criticos") or {}
            if portal:
                if dirigido_detect:
                    crit["dirigido_a"] = dirigido_detect
                else:
                    crit["dirigido_a"] = "Dependencia convocante (vía ComprasMX)"
                crit["firma_requerida"] = "Firma electrónica del representante legal"
                crit["lugar_entrega"] = ""
                advs = crit.get("advertencias") or []
                advs = [a for a in advs if isinstance(a, str)]
                analysis["puntos_criticos"] = {**crit, "advertencias": advs}
                payload = {
                    "id": ws["id"],
                    "name": ws.get("name") or ws["id"],
                    "analysis": json.dumps(analysis, ensure_ascii=False)
                }
                await client.post(f"{config['DB_URL']}/db/workspaces", json=payload, timeout=5.0)
            # Devuelve siempre el bloque actualizado (o vigente) de puntos_criticos
            return {"status": "success", "data": {"portal_electronico": portal, "puntos_criticos": analysis.get("puntos_criticos") or crit}}
    except Exception as e:
        logger.error(f"Critical rules recompute error: {e}")
        return {"status": "error", "msg": str(e)}

@app.post("/api/analyze-base")
async def analyze_base_endpoint(file: UploadFile = File(...), workspace_id: str = "default"):
    gen = await stream_orchestrator_agentic(file, workspace_id)
    return StreamingResponse(gen, media_type="application/x-ndjson")

@app.post("/api/generate-docs")
async def generate_docs_endpoint(workspace_id: str = "default"):
    async def generator():
        import asyncio
        queue = asyncio.Queue()
        async def put_in_queue(msg): await queue.put(msg)
        
        task = asyncio.create_task(gerente.run_document_generation(workspace_id, put_in_queue))
        
        while not task.done() or not queue.empty():
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=1.0)
                yield msg
            except asyncio.TimeoutError: continue
            
        if task.exception():
            yield json.dumps({"status": "error", "msg": str(task.exception())}) + "\n"

    return StreamingResponse(generator(), media_type="application/x-ndjson")

@app.post("/api/validate-identity")
async def validate_identity_endpoint(workspace_id: str = "default"):
    try:
        result = await identity_validator.execute(workspace_id)
        return {"status": "success", "data": result}
    except Exception as e:
        logger.error(f"Identity validation error: {e}")
        return {"status": "error", "msg": str(e)}

@app.post("/api/process-context")
async def process_context(file: UploadFile = File(...), workspace_id: str = "default", type: str = "cif", force: bool = False):
    """Procesa Logo, CIF o Precios y los vincula al Workspace"""
    # Robustez contra URLs mal formadas desde el cliente (?type=cif?workspace_id=...)
    if "?" in type:
        parts = type.split("?")
        type = parts[0]
        for p in parts[1:]:
            if "workspace_id=" in p:
                workspace_id = p.split("workspace_id=")[-1]

    content = await file.read()
    filename = file.filename.lower()
    
    # Soporte para archivos .txt directos (evita OCR)
    direct_text = None
    if filename.endswith(".txt"):
        try:
            direct_text = content.decode("utf-8")
        except:
            try:
                direct_text = content.decode("latin-1")
            except:
                pass

    if type == "logo":
        # Guardar logo en carpeta del workspace
        path = os.path.join(config["WORKSPACE_DIR"], workspace_id)
        os.makedirs(path, exist_ok=True)
        logo_filename = file.filename
        logo_path_host = os.path.join(path, logo_filename)
        with open(logo_path_host, "wb") as f:
            f.write(content)
        
        # Guardar ruta interna del contenedor (usada por docx-gen)
        container_workspace = "/app/data/workspaces"
        logo_path_container = f"{container_workspace}/{workspace_id}/{logo_filename}"
        
        # Actualizar DB con ruta del contenedor
        async with httpx.AsyncClient() as client:
            await client.post(f"{config['DB_URL']}/db/workspaces", json={
                "id": workspace_id,
                "name": workspace_id,
                "logo_path": logo_path_container
            })
        return {"status": "success", "msg": "Logo guardado y vinculado.", "logo_filename": logo_filename}

    elif type == "cif":
        # Guardar archivo original para persistencia/re-análisis
        path = os.path.join(config["WORKSPACE_DIR"], workspace_id)
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, file.filename), "wb") as f:
            f.write(content)

        async with httpx.AsyncClient() as client:
            try:
                if not force:
                    ws_resp = await client.get(f"{config['DB_URL']}/db/workspaces/{workspace_id}", timeout=5.0)
                    if ws_resp.status_code == 200:
                        ws = ws_resp.json()
                        cif_prev = ws.get("cif_data")
                        if cif_prev:
                            try:
                                cif_json = json.loads(cif_prev)
                                try:
                                    validation = await identity_validator.execute(workspace_id)
                                except Exception:
                                    validation = None
                                return {"status": "success", "msg": "CIF reutilizado desde cache.", "data": cif_json, "validation": validation}
                            except:
                                pass
            except Exception:
                pass

            if direct_text:
                ocr_text = direct_text
            else:
                ex_name = f"extraccion_{file.filename}.txt"
                ex_path = os.path.join(path, ex_name)
                if os.path.exists(ex_path) and not force:
                    try:
                        with open(ex_path, "r", encoding="utf-8") as ef:
                            ocr_text = ef.read()
                    except:
                        ocr_text = ""
                else:
                    files = {'file': (file.filename, content, file.content_type)}
                    ocr_text = ""
                    async with client.stream("POST", f"{config['OCR_URL']}/ocr/process", files=files, timeout=300.0) as resp:
                        async for line in resp.aiter_lines():
                            if not line: continue
                            chunk = json.loads(line)
                            if chunk["status"] == "complete":
                                ocr_text = "\n".join([page['text'] for page in chunk["data"]])
                    try:
                        with open(ex_path, "w", encoding="utf-8") as ef:
                            ef.write(ocr_text)
                    except:
                        pass
            
            if not ocr_text:
                return {"status": "error", "msg": "No se pudo leer el CIF."}

            # Extraer JSON con el Parser
            prompt = f"""Eres un experto en documentos fiscales mexicanos. Analiza este texto del CIF (Constancia de Situación Fiscal) y extrae un JSON con los siguientes campos exactos:
- rfc: El RFC de la empresa o persona
- razon_social: La denominación o razón social
- rfc: El RFC de la empresa o persona
- razon_social: La denominación o razón social
- representante_legal: Nombre del representante legal o socio administrador (BÚSCALO con prioridad)
- domicilio: Dirección fiscal completa (Calle, No, CP, Municipio, Estado)
- tipo_persona: 'Moral' (si es SA, SRL, etc) o 'Fisica'
- regimen_fiscal: El régimen fiscal actual

Texto del CIF:
{ocr_text[:10000]}

Responde ÚNICAMENTE con el objeto JSON."""
            payload = {"model": config["OLLAMA_MODEL"], "prompt": prompt, "stream": False, "format": "json"}
            resp_ollama = await client.post(f"{config['OLLAMA_HOST']}/api/generate", json=payload, timeout=60.0)
            
            if resp_ollama.status_code == 200:
                raw_cif = resp_ollama.json()['response'].strip()
                if raw_cif.startswith("```json"): raw_cif = raw_cif.split("```json")[-1].split("```")[0].strip()
                elif raw_cif.startswith("```"): raw_cif = raw_cif.split("```")[-1].split("```")[0].strip()
                
                try:
                    cif_json = json.loads(raw_cif)
                    # Normalización agresiva de campos para el Dashboard
                    if "representante" in cif_json and "representante_legal" not in cif_json:
                        cif_json["representante_legal"] = cif_json["representante"]
                    
                    # Limpieza: Si Ollama devuelve objetos en lugar de strings (ej. domicilio como dict)
                    for k, v in cif_json.items():
                        if isinstance(v, dict):
                            cif_json[k] = " ".join([str(val) for val in v.values() if val])
                except:
                    cif_json = {"razon_social": "Error en formato CIF", "rfc": "ERROR"}

                # Guardar en DB vinculado al Workspace
                await client.post(f"{config['DB_URL']}/db/workspaces", json={
                    "id": workspace_id,
                    "name": workspace_id,
                    "cif_data": json.dumps(cif_json)
                })
                try:
                    validation = await identity_validator.execute(workspace_id)
                except Exception:
                    validation = None
                return {"status": "success", "msg": "CIF procesado y vinculado.", "data": cif_json, "validation": validation}

    elif type == "acta":
        # Guardar archivo original para persistencia/re-análisis
        path = os.path.join(config["WORKSPACE_DIR"], workspace_id)
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, file.filename), "wb") as f:
            f.write(content)

        if direct_text:
            ocr_text = direct_text
        else:
            async with httpx.AsyncClient() as client:
                try:
                    if not force:
                        ws_resp = await client.get(f"{config['DB_URL']}/db/workspaces/{workspace_id}", timeout=5.0)
                        if ws_resp.status_code == 200:
                            ws = ws_resp.json()
                            acta_prev = ws.get("acta_data")
                            if acta_prev:
                                try:
                                    acta_json = json.loads(acta_prev)
                                    try:
                                        validation = await identity_validator.execute(workspace_id)
                                    except Exception:
                                        validation = None
                                    return {"status": "success", "msg": "Acta reutilizada desde cache.", "data": acta_json, "validation": validation}
                                except:
                                    pass
                except Exception:
                    pass
                files = {'file': (file.filename, content, file.content_type)}
                ex_name = f"extraccion_{file.filename}.txt"
                ex_path = os.path.join(path, ex_name)
                if os.path.exists(ex_path) and not force:
                    try:
                        with open(ex_path, "r", encoding="utf-8") as ef:
                            ocr_text = ef.read()
                    except:
                        ocr_text = ""
                else:
                    ocr_text = ""
                    async with client.stream("POST", f"{config['OCR_URL']}/ocr/process", files=files, timeout=600.0) as resp:
                        async for line in resp.aiter_lines():
                            if not line: continue
                            chunk = json.loads(line)
                            if chunk["status"] == "complete":
                                ocr_text = "\n".join([page['text'] for page in chunk["data"]])
                    try:
                        with open(ex_path, "w", encoding="utf-8") as ef:
                            ef.write(ocr_text)
                    except:
                        pass
            
        # Limpieza básica
        import re
        clean_acta = ocr_text
        if len(re.findall(r'\b[a-zA-Z]\b\s\b[a-zA-Z]\b', clean_acta[:1000])) > 20:
            clean_acta = re.sub(r'(?<=\b[a-zA-Z])\s(?=[a-zA-Z]\b)', '', clean_acta)

        # === EXTRACCIÓN QUIRÚRGICA ===
        # Buscar con Python los párrafos que mencionan nombramientos / resoluciones
        # Este método es INDEPENDIENTE del modelo y garantiza que el contexto sea correcto.
        key_patterns = [
            r'RESOLUCIONES?\s+TRANSITORIAS?.{0,2000}',
            r'NOMBRAMIENTO.{0,1500}',
            r'recayendo.{0,500}',
            r'se\s+designa?.{0,500}',
            r'nombramiento\s+de\s+(?:Administrador|Representante|Apoderado|Gerente|Presidente).{0,800}',
            r'ADMINISTRADOR\s+[ÚU]NICO.{0,600}',
            r'Administrador\s+[ÚU]nico.{0,600}',
            r'REPRESENTANTE\s+LEGAL.{0,600}',
        ]
        
        surgical_context = ""
        for pattern in key_patterns:
            matches = re.findall(pattern, clean_acta, re.IGNORECASE | re.DOTALL)
            for m in matches:
                snippet = m[:2000].strip()
                if snippet and snippet not in surgical_context:
                    surgical_context += snippet + "\n\n---\n\n"
            if len(surgical_context) > 6000:
                break
        
        # Si no encontramos nada con búsqueda quirúrgica, usamos el sandwich
        if len(surgical_context.strip()) < 100:
            logger.warning("Extracción quirúrgica no encontró secciones clave. Usando sandwich.")
            surgical_context = clean_acta[:15000] + "\n\n[...]\n\n" + clean_acta[-10000:]

        logger.info(f"Contexto quirúrgico extraído: {len(surgical_context)} chars")
        logger.info(f"Primeros 500 chars del contexto: {surgical_context[:500]}")

        # === PROMPT DIRECTO ===
        prompt = f"""Eres un notario experto en actas constitutivas mexicanas. Del siguiente extracto de un Acta Constitutiva, extrae ÚNICAMENTE estos 4 campos en JSON:

EXTRACTO DEL ACTA (sección de nombramientos/resoluciones):
{surgical_context[:8000]}

Devuelve SOLO este JSON (sin explicaciones):
{{
  "razon_social": "Nombre completo de la sociedad con su tipo (S.A. de C.V., etc.)",
  "escritura_numero": "Número del instrumento notarial",
  "representante": "Nombre completo de la persona nombrada como Administrador Único o Representante Legal",
  "cargo": "Cargo exacto (Administrador Único, Representante Legal, etc.)"
}}"""

        async with httpx.AsyncClient() as client:
            payload = {"model": config["OLLAMA_MODEL"], "prompt": prompt, "stream": False, "format": "json"}
            resp_ollama = await client.post(f"{config['OLLAMA_HOST']}/api/generate", json=payload, timeout=180.0)
            
            if resp_ollama.status_code == 200:
                raw_acta = resp_ollama.json()['response'].strip()
                logger.info(f"Ollama Raw Acta Response: {raw_acta[:500]}")
                if raw_acta.startswith("```json"): raw_acta = raw_acta.split("```json")[-1].split("```")[0].strip()
                elif raw_acta.startswith("```"): raw_acta = raw_acta.split("```")[-1].split("```")[0].strip()
                
                try:
                    acta_json = json.loads(raw_acta)
                    # Normalización agresiva
                    if "representante" in acta_json and "representante_legal" not in acta_json:
                        acta_json["representante_legal"] = acta_json["representante"]
                    
                    # Limpieza: Evitar objetos en valores de campos
                    for k, v in acta_json.items():
                        if isinstance(v, dict):
                            acta_json[k] = " ".join([str(val) for val in v.values() if val])
                    logger.info(f"Acta extraída: representante={acta_json.get('representante_legal')} cargo={acta_json.get('cargo')}")
                except Exception as e:
                    logger.error(f"Error parseando JSON del acta: {e}. Raw: {raw_acta}")
                    acta_json = {"razon_social": "Error en formato", "representante_legal": "ERROR", "cargo": "ERROR"}

                try:
                    rx = legal_extractor.extract_from_text(clean_acta)
                    for k, v in rx.items():
                        if v and (k not in acta_json or not acta_json.get(k)):
                            acta_json[k] = v
                except Exception:
                    pass

                # Guardar en DB vinculado al Workspace
                await client.post(f"{config['DB_URL']}/db/workspaces", json={
                    "id": workspace_id,
                    "name": workspace_id,
                    "acta_data": json.dumps(acta_json)
                })
                try:
                    validation = await identity_validator.execute(workspace_id)
                except Exception:
                    validation = None
                return {"status": "success", "msg": "Acta Constitutiva procesada y vinculada.", "data": acta_json, "validation": validation}
            
        return {"status": "error", "msg": "Error procesando inteligencia del Acta."}

    return {"status": "error", "msg": "Tipo de contexto no soportado."}

@app.post("/api/process-excel")
async def process_excel(file: UploadFile = File(...), workspace_id: str = "default"):
    """Recibe un Excel de cotización, lo convierte en DOCUMENTO E2 Word con datos del perfil de empresa."""
    from fastapi.responses import StreamingResponse as SR
    import io

    content = await file.read()
    logger.info(f"[Excel→E2] Recibido: {file.filename} ({len(content)} bytes) workspace={workspace_id}")

    # Guardar Excel en el workspace para persistencia
    ws_path = os.path.join(config["WORKSPACE_DIR"], workspace_id)
    os.makedirs(ws_path, exist_ok=True)
    excel_save_path = os.path.join(ws_path, file.filename)
    with open(excel_save_path, "wb") as f:
        f.write(content)
    logger.info(f"[Excel→E2] Excel guardado en: {excel_save_path}")

    # Obtener datos del perfil de empresa desde la DB
    representante = ""
    cargo = ""
    empresa_nombre = ""
    empresa_rfc = ""
    domicilio = ""
    logo_path = ""
    convocante = ""
    licitacion_no = ""
    objeto = ""

    async with httpx.AsyncClient() as client:
        try:
            ws_resp = await client.get(f"{config['DB_URL']}/db/workspaces/{workspace_id}", timeout=5.0)
            if ws_resp.status_code == 200:
                ws = ws_resp.json()
                cif = json.loads(ws.get("cif_data") or "{}")
                acta = json.loads(ws.get("acta_data") or "{}")
                analysis = json.loads(ws.get("analysis") or "{}")

                empresa_nombre = cif.get("razon_social") or acta.get("razon_social") or ""
                empresa_rfc = cif.get("rfc") or ""
                domicilio = cif.get("domicilio_fiscal") or cif.get("domicilio") or ""
                representante = acta.get("representante") or cif.get("representante_legal") or ""
                cargo = acta.get("cargo") or "Representante Legal"
                logo_path = ws.get("logo_path") or ""
                convocante = analysis.get("convocante") or ""
                licitacion_no = analysis.get("numero_licitacion") or ""
                objeto = analysis.get("objeto") or ""

                logger.info(f"[Excel→E2] Perfil cargado: empresa={empresa_nombre}, rfc={empresa_rfc}, rep={representante}")
        except Exception as e:
            logger.warning(f"[Excel→E2] No se pudo obtener perfil: {e}")

        # Reenviar archivo Excel al servicio docx-gen
        try:
            files = {"file": (file.filename, content, file.content_type or "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
            params = {
                "empresa_nombre": empresa_nombre,
                "empresa_rfc": empresa_rfc,
                "representante_legal": representante,
                "cargo_representante": cargo,
                "convocante": convocante,
                "licitacion_no": licitacion_no,
                "objeto": objeto,
                "domicilio_fiscal": domicilio,
                "logo_path": logo_path,
                "workspace_id": workspace_id
            }
            logger.info(f"[Excel→E2] Enviando a docx-gen: {config['DOCX_URL']}/docx/from-excel")
            docx_resp = await client.post(
                f"{config['DOCX_URL']}/docx/from-excel",
                files=files,
                params=params,
                timeout=120.0
            )
            if docx_resp.status_code == 200:
                docx_bytes = docx_resp.content
                logger.info(f"[Excel→E2] Word generado exitosamente: {len(docx_bytes)} bytes")
                return SR(
                    io.BytesIO(docx_bytes),
                    media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers={"Content-Disposition": f"attachment; filename=DOCUMENTO_E2_Presupuesto.docx"}
                )
            else:
                err_detail = docx_resp.text[:500]
                logger.error(f"[Excel→E2] Error de docx-gen ({docx_resp.status_code}): {err_detail}")
                raise HTTPException(status_code=500, detail=f"Error en docx-gen: {err_detail}")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"[Excel→E2] Excepción: {traceback.format_exc()}")
            raise HTTPException(status_code=500, detail=f"Error procesando Excel: {str(e)}")



# Note: Maintaining legacy routes for now, will be agentized next
async def legacy_orchestrator(file: UploadFile, task_type: str):
    # (Existing legacy code for CIF/Acta temporarily here for backward compatibility)
    # Re-using the logic from main.py before refactor
    pass

@app.post("/api/process-cif")
async def process_cif(file: UploadFile = File(...), workspace_id: str = "default"):
    # Legacy for now
    from main_legacy import stream_orchestrator as legacy_stream
    return StreamingResponse(legacy_stream(file, "cif", workspace_id), media_type="application/x-ndjson")

@app.post("/api/process-acta")
async def process_acta(file: UploadFile = File(...), workspace_id: str = "default"):
    # Legacy for now
    from main_legacy import stream_orchestrator as legacy_stream
    return StreamingResponse(legacy_stream(file, "acta", workspace_id), media_type="application/x-ndjson") 

@app.get("/api/workspaces/{workspace_id}/files")
async def list_workspace_files(workspace_id: str):
    """Lista todos los archivos, recorriendo subcarpetas"""
    base_path = os.path.join(config["WORKSPACE_DIR"], workspace_id)
    if not os.path.exists(base_path):
        return []
    
    files = []
    for root, dirs, filenames in os.walk(base_path):
        for f in filenames:
            full_path = os.path.join(root, f)
            rel_path = os.path.relpath(full_path, base_path).replace("\\", "/")
            files.append({
                "name": rel_path,
                "size": os.path.getsize(full_path),
                "type": "docx" if f.endswith(".docx") else "txt" if f.endswith(".txt") else "pdf" if f.endswith(".pdf") else "other"
            })
    return files

@app.get("/api/workspaces/{workspace_id}/download-zip/{folder_name}")
async def download_zip(workspace_id: str, folder_name: str):
    """Descarga una subcarpeta completa como ZIP"""
    import shutil
    import tempfile
    from fastapi.responses import FileResponse
    
    folder_path = os.path.join(config["WORKSPACE_DIR"], workspace_id, folder_name)
    if not os.path.exists(folder_path):
        raise HTTPException(status_code=404, detail="Carpeta no encontrada")
    
    # Crear ZIP temporal
    temp_dir = tempfile.mkdtemp()
    zip_path = os.path.join(temp_dir, f"{folder_name}.zip")
    shutil.make_archive(zip_path.replace(".zip", ""), 'zip', folder_path)
    
    return FileResponse(zip_path, filename=f"{folder_name}.zip")

@app.get("/api/workspaces/{workspace_id}/download/{filename:path}")
async def download_file(workspace_id: str, filename: str):
    """Descarga un archivo (soporta rutas relativas como carpeta/archivo.docx)"""
    from fastapi.responses import FileResponse
    path = os.path.join(config["WORKSPACE_DIR"], workspace_id, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"Archivo no encontrado: {filename}")
    
    return FileResponse(path, filename=os.path.basename(filename))

@app.get("/api/workspaces/{workspace_id}/inconsistencias")
async def get_inconsistencias(workspace_id: str):
    """Devuelve inconsistencias del workspace (lee inconsistencias.json si existe)"""
    wdir = os.path.join(config["WORKSPACE_DIR"], workspace_id)
    fpath = os.path.join(wdir, "inconsistencias.json")
    if os.path.exists(fpath):
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)
            return {"status": "success", "data": data}
        except Exception as e:
            return {"status": "error", "msg": f"Error leyendo inconsistencias: {str(e)}"}
    # Fallback: intentar leer desde DB si se almacenó
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(f"{config['DB_URL']}/db/workspaces/{workspace_id}", timeout=3.0)
            if resp.status_code == 200:
                ws = resp.json()
                inc = ws.get("inconsistencias")
                if inc:
                    try:
                        return {"status": "success", "data": json.loads(inc)}
                    except Exception:
                        return {"status": "success", "data": {"inconsistencias": [], "estado": "ok"}}
        except Exception:
            pass
    return {"status": "success", "data": {"inconsistencias": [], "estado": "ok"}}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
