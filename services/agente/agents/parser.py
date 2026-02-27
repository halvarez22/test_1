import os
import httpx
import json
import logging
from .base import BaseAgent
from typing import Callable

logger = logging.getLogger("licitai-agents")

class TenderParserAgent(BaseAgent):
    def __init__(self, ocr_url: str, ollama_host: str, ollama_model: str):
        super().__init__("Parser", "Analista experto en lectura y estructuración de bases de licitación.")
        self.ocr_url = ocr_url
        self.ollama_host = ollama_host
        self.ollama_model = ollama_model

    async def execute(self, file_content: bytes, filename: str, content_type: str, progress_callback: Callable = None, workspace_id: str = None):
        await self.emit_progress(progress_callback, 10, "Iniciando análisis documental...")
        workspace_dir = os.environ.get("WORKSPACE_DIR", "/app/data/workspaces")
        
        async with httpx.AsyncClient() as client:
            # 1. Herramienta OCR (Llamada al servicio VLM)
            files = {'file': (filename, file_content, content_type)}
            ocr_text = ""
            
            # Soporte para archivos .txt directos (evita OCR)
            if filename.lower().endswith(".txt"):
                try:
                    ocr_text = file_content.decode("utf-8")
                except:
                    ocr_text = file_content.decode("latin-1", errors="ignore")
            
            if not ocr_text:
                await self.emit_progress(progress_callback, 20, "Detectando tipo de PDF y extrayendo texto...")
                
                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        async with client.stream("POST", f"{self.ocr_url}/ocr/process", files=files, timeout=900.0) as resp:
                            if resp.status_code != 200:
                                raise Exception(f"Error en servicio OCR: {resp.status_code}")

                            async for line in resp.aiter_lines():
                                if not line: continue
                                chunk = json.loads(line)
                                if chunk["status"] == "complete":
                                    ocr_data = chunk["data"]
                                    ocr_text = "\n".join([page['text'] for page in ocr_data])
                                    
                                    # PERSISTENCIA DEL TEXTO EXTRAÍDO
                                    if workspace_id:
                                        try:
                                            path = os.path.join(workspace_dir, workspace_id)
                                            os.makedirs(path, exist_ok=True)
                                            with open(os.path.join(path, f"extraccion_{filename}.txt"), "w", encoding="utf-8") as f:
                                                f.write(ocr_text)
                                        except: pass
                                elif chunk["status"] == "progress":
                                    scaled_val = 20 + int(chunk["val"] * 0.65)
                                    await self.emit_progress(progress_callback, scaled_val, chunk.get("msg", "Procesando..."))
                                elif chunk["status"] == "info":
                                    await self.emit_progress(progress_callback, 20, chunk.get("msg"))
                            break # Exito, salir del loop de reintentos
                    except (httpx.ConnectError, httpx.ConnectTimeout) as e:
                        if attempt < max_retries - 1:
                            await self.emit_progress(progress_callback, 20, f"Servidor OCR ocupado, reintentando... ({attempt+1}/{max_retries})")
                            import asyncio
                            await asyncio.sleep(5)
                            continue
                        else:
                            raise Exception(f"Fallo en ocr tras varios intentos: {str(e)}")
                    except Exception as e:
                        raise Exception(f"Fallo en ocr: {str(e)}")

            if not ocr_text:
                raise Exception("El analista no pudo encontrar texto legible en el documento.")

            # 2. Herramienta de Inteligencia (Llamada a Ollama)
            # --- FASE 3: OLLAMA ANALYSIS ---
            await self.emit_progress(progress_callback, 90, "Estructurando inteligencia según normativas...")
            
            # Limpieza básica de texto espaciado (ej. B a s e s -> Bases)
            clean_text = ocr_text
            import re
            # Si detectamos muchas letras solas separadas por espacios, intentamos unir
            if len(re.findall(r'\b[a-zA-Z]\b\s\b[a-zA-Z]\b', clean_text[:1000])) > 20:
                clean_text = re.sub(r'(?<=\b[a-zA-Z])\s(?=[a-zA-Z]\b)', '', clean_text)

            prompt = f"""ERES UN ANALISTA EXPERTO EN LICITACIONES MEXICANAS. Tu única tarea es extraer datos y devolverlos en JSON.

TEXTO DE LAS BASES:
{clean_text[:90000]}

---
INSTRUCCIONES DE EXTRACCIÓN (ROL: AUDITOR LEGAL):

1. convocante: Quién convoca.

2. numero_licitacion: ID oficial.

3. objeto: Descripción de trabajos.

4. fecha_publicacion: Fecha del documento.

5. fianzas_requeridas: Objeto con garantías.

6. categorized_anexos: 
   - Clasifica documentos con ID (T1, E1, etc.).
   - GHOST DOCUMENTS: Busca documentos obligatorios que NO tengan ID (ej. 'Opinión SAT 32-D', 'Opinión IMSS', 'INFONAVIT', 'REPSE', 'Inhabilitaciones'). Inclúyelos con prefijo [ADM - Sin Anexo].

7. fechas_clave: Eventos con hora.

8. certificaciones_y_normas: ISO, NOM, etc.

9. puntos_criticos: DATOS SINE QUA NON (SOLO SI APAREZCAN EXPLÍCITAMENTE EN EL TEXTO).
   - Detecta si el procedimiento es electrónico (ComprasMX, CompraNet, etc.) o presencial.
   - Si es electrónico: "dirigido_a", "lugar_entrega" y "firma_requerida" deben ser exactamente: "NO APLICA - Procedimiento 100% electrónico vía ComprasMX".
   - Solo extrae lo que esté escrito literalmente. Si no aparece, pon "NO ESPECIFICADO EN LA CONVOCATORIA".
   - Nunca inventes direcciones, alcaldes, casas de cultura ni protocolos de firma física.

10. checklist_cumplimiento: PLAN MAESTRO DE AUDITORÍA.
    Busca requisitos de fondo: 'Opinión SAT 32-D', 'Opinión IMSS', 'INFONAVIT', 'REPSE', 'Inhabilitaciones'. 
    Identifica si es causa de descalificación no presentarlos.

FORMATO DE RESPUESTA — SOLO JSON, SIN TEXTO ADICIONAL:
{{
  "convocante": "...",
  "numero_licitacion": "...",
  "objeto": "...",
  "fecha_publicacion": "...",
  "fianzas_requeridas": {{"garantia_seriedad": "...", "cumplimiento": "...", "vicios_ocultos": "..."}},
  "categorized_anexos": {{"technical": ["DOCUMENTO T1 - ...", "DOCUMENTO T2 - ..."], "economic": ["DOCUMENTO E1 - ...", "DOCUMENTO E2 - ..."]}},
  "fechas_clave": {{"visita": "...", "aclaraciones": "...", "apertura": "...", "fallo": "..."}},
  "certificaciones_y_normas": "...",
  "puntos_criticos": {{
    "dirigido_a": "...",
    "firma_requerida": "...",
    "lugar_entrega": "...",
    "advertencias": ["...", "..."],
    "tipo_procedimiento": "electrónico" | "presencial" | "mixto"
  }},
  "checklist_cumplimiento": [
    {{
      "punto": "...",
      "motivo_riesgo": "...",
      "accion_preventiva": "..."
    }}
  ]
}}"""

            payload = {
                "model": self.ollama_model,
                "prompt": prompt,
                "stream": False,
                "format": "json"
            }
            
            resp = await client.post(f"{self.ollama_host}/api/generate", json=payload, timeout=600.0)
            if resp.status_code == 200:
                raw_response = resp.json().get('response', '{}').strip()
                logger.info(f"Ollama Raw Analysis: {raw_response[:500]}...")
                
                # Limpieza de backticks
                if "```" in raw_response:
                    raw_response = raw_response.split("```")[1]
                    if raw_response.startswith("json"): raw_response = raw_response[4:]
                
                try:
                    analysis = json.loads(raw_response)
                except Exception as e:
                    logger.error(f"JSON Parse Error: {e}")
                    analysis = {}

                # Asegurar todas las llaves necesarias con valores por defecto
                defaults = {
                    "convocante": "N/D",
                    "numero_licitacion": "N/D",
                    "objeto": "N/D",
                    "fecha_publicacion": "N/D",
                    "fianzas_requeridas": {"garantia_seriedad": "N/D", "cumplimiento": "N/D", "otros": "N/D"},
                    "categorized_anexos": {"technical": [], "economic": []},
                    "fechas_clave": {"visita": "N/D", "aclaraciones": "N/D", "apertura": "N/D", "fallo": "N/D"},
                    "certificaciones_y_normas": "N/D",
                    "puntos_criticos": {
                        "dirigido_a": "N/D",
                        "firma_requerida": "N/D",
                        "lugar_entrega": "N/D",
                        "advertencias": []
                    }
                }
                
                final_analysis = {**defaults, **analysis}
                try:
                    from .template_selector import classify_procedure
                    sel = classify_procedure(clean_text)
                    tp = sel.get("tipo_procedimiento")
                    te = sel.get("tipo_entidad")
                    if tp:
                        pc = final_analysis.get("puntos_criticos") or {}
                        pc["tipo_procedimiento"] = tp
                        if tp == "electrónico":
                            pc["dirigido_a"] = "NO APLICA - Procedimiento 100% electrónico vía ComprasMX"
                            pc["firma_requerida"] = "NO APLICA - Procedimiento 100% electrónico vía ComprasMX"
                            pc["lugar_entrega"] = "NO APLICA - Procedimiento 100% electrónico vía ComprasMX"
                        final_analysis["puntos_criticos"] = pc
                    if te:
                        final_analysis["tipo_entidad"] = te
                except Exception:
                    pass
                
                await self.emit_progress(progress_callback, 100, "Análisis documental completado.")
                return final_analysis
            else:
                raise Exception("Error en Ollama")
