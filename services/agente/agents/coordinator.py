import os
import json
import httpx
import logging
from .base import BaseAgent
from .parser import TenderParserAgent
from .generator import TechnicalProposalAgent, EconomicProposalAgent
from .profile import ProfileAgent
from .chat import ChatAgent
from typing import Callable, Dict

logger = logging.getLogger("licitai-agents")

class CoordinatorAgent(BaseAgent):
    def __init__(self, config: dict):
        super().__init__("Coordinator", "Gerente de proyecto encargado de orquestar el flujo de la licitación.")
        self.config = config
        self.workspace_dir = config.get("WORKSPACE_DIR", "/app/data/workspaces")
        self.db_url = config.get("DB_URL", "http://memoria-db:8083")
        
        self.parser = TenderParserAgent(
            ocr_url=config["OCR_URL"],
            ollama_host=config["OLLAMA_HOST"],
            ollama_model=config["OLLAMA_MODEL"]
        )
        
        self.tech_agent = TechnicalProposalAgent(
            docx_url=config["DOCX_URL"],
            ollama_host=config["OLLAMA_HOST"],
            ollama_model=config["OLLAMA_MODEL"]
        )
        
        self.econ_agent = EconomicProposalAgent(
            docx_url=config["DOCX_URL"],
            ollama_host=config["OLLAMA_HOST"],
            ollama_model=config["OLLAMA_MODEL"]
        )
        
        self.profile_agent = ProfileAgent(db_url=self.db_url)
        self.chat_agent = ChatAgent(
            ollama_host=config["OLLAMA_HOST"],
            ollama_model=config["OLLAMA_MODEL"],
            workspace_dir=self.workspace_dir
        )

    async def _get_company_data(self, workspace_id: str):
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(f"{self.db_url}/db/workspaces/{workspace_id}", timeout=2.0)
                if resp.status_code == 200:
                    ws_data = resp.json()
                    cif_json = json.loads(ws_data.get("cif_data") or "{}")
                    acta_json = json.loads(ws_data.get("acta_data") or "{}")
                    
                    tipo_raw = (cif_json.get("tipo_persona") or "").strip().lower()
                    tipo_mapped = "PM" if tipo_raw in ["moral", "persona moral", "empresa"] else ("PF" if tipo_raw in ["fisica", "persona física", "persona fisica"] else None)
                    
                    # Priorizamos el acta para representante y cargo
                    representante = acta_json.get("representante") or cif_json.get("representante_legal")
                    cargo = acta_json.get("cargo") or "Representante Legal"

                    return {
                        "razon_social": cif_json.get("razon_social") or acta_json.get("razon_social") or "EMPRESA S.A. DE C.V.",
                        "rfc": cif_json.get("rfc", "XAXX010101000"),
                        "representante": representante,
                        "cargo": cargo,
                        "domicilio": cif_json.get("domicilio_fiscal", "DOMICILIO"),
                        "logo_path": ws_data.get("logo_path"),
                        "tipo_persona": tipo_mapped
                    }
            except: pass
        return {"razon_social": "EMPRESA S.A.", "rfc": "XAXX010101000", "representante": None, "cargo": "Representante Legal", "domicilio": None, "tipo_persona": "PM"}

    async def run_tender_analysis(self, file_content: bytes, filename: str, content_type: str, progress_callback: Callable, workspace_id: str = "default"):
        try:
            # --- FASE 1: PERSISTIR FUENTE ORIGINAL ---
            path = os.path.join(self.workspace_dir, workspace_id)
            os.makedirs(path, exist_ok=True)
            source_path = os.path.join(path, filename)
            with open(source_path, "wb") as f:
                f.write(file_content)

            # --- FASE 2: PARSER ---
            analysis = await self.parser.execute(file_content, filename, content_type, progress_callback, workspace_id=workspace_id)
            
            # --- FASE 3: MEMORIA TRABAJO ---
            licit_id = str(analysis.get("numero_licitacion", "unknown")).replace("/", "_").replace("\\", "_")
            with open(os.path.join(path, "analysis.json"), "w", encoding="utf-8") as f:
                json.dump(analysis, f, indent=4)

            # Sincronizar DB Bids
            async with httpx.AsyncClient() as client:
                try:
                    await client.post(f"{self.db_url}/db/bids", json={
                        "convocante": str(analysis.get("convocante", "")),
                        "numero_licitacion": licit_id,
                        "objeto": str(analysis.get("objeto", "")),
                        "fianzas_requeridas": str(analysis.get("fianzas_requeridas", ""))
                    }, timeout=5.0)
                except: pass

            # --- FASE 4: PROFILE CONSOLIDATION ---
            company = await self._get_company_data(workspace_id)
            profile = await self.profile_agent.execute(workspace_id, analysis, company)

            await progress_callback(json.dumps({
                "status": "complete", 
                "agent": self.name,
                "analysis": analysis,
                "profile": profile,
                "msg": f"Análisis y perfilado completado para {licit_id}. Datos publicados en Dashboard."
            }) + "\n")

        except Exception as e:
            logger.error(f"Coordinator Error (Analysis): {str(e)}")
            await progress_callback(json.dumps({"status": "error", "agent": self.name, "msg": str(e)}) + "\n")

    async def run_document_generation(self, workspace_id: str, progress_callback: Callable):
        try:
            path = os.path.join(self.workspace_dir, workspace_id)
            analysis_path = os.path.join(path, "analysis.json")
            
            if not os.path.exists(analysis_path):
                raise Exception("No se encontró un análisis previo. Por favor analice las bases primero.")
            
            with open(analysis_path, "r", encoding="utf-8") as f:
                analysis = json.load(f)

            # --- FASE 4: PREPARACIÓN DE PERFIL UNIFICADO ---
            # Aseguramos que los documentos tengan el logo y datos fiscales correctos
            raw_company = await self._get_company_data(workspace_id)
            profile = await self.profile_agent.execute(workspace_id, analysis, raw_company)
            
            # Mapeamos el perfil de vuelta a la estructura que esperan los agentes de generación
            licitante = profile.get("licitante", {})
            company = {
                "razon_social": licitante.get("empresa"),
                "rfc": licitante.get("rfc"),
                "representante": licitante.get("representante"),
                "domicilio": licitante.get("domicilio"),
                "logo_path": licitante.get("logo_path"),
                "cargo": raw_company.get("cargo", "Representante Legal"),
                "tipo_persona": raw_company.get("tipo_persona", "PM")
            }
            
            ws_name = workspace_id
            async with httpx.AsyncClient() as client:
                try:
                    r = await client.get(f"{self.db_url}/db/workspaces/{workspace_id}", timeout=2.0)
                    if r.status_code == 200:
                        ws_name = r.json().get("name", workspace_id)
                except: pass
            
            folder_name = f"{ws_name}_Documentos_Generados".replace(" ", "_")
            gen_path = os.path.join(path, folder_name)
            os.makedirs(gen_path, exist_ok=True)

            # --- FASE 5: TECH AGENT (Propuesta Técnica) ---
            cat_anexos = analysis.get("categorized_anexos", {})
            tech_anexos = cat_anexos.get("technical", [])
            
            # Fallback a formato antiguo si está vacío
            if not tech_anexos and not cat_anexos.get("economic"):
                old_docs = analysis.get("documentos_requeridos") or analysis.get("anexos_requeridos") or []
                if isinstance(old_docs, list):
                    # Intentamos inferir si son técnicos
                    tech_anexos = [d.get("nombre") if isinstance(d, dict) else str(d) for d in old_docs]

            tech_docs = await self.tech_agent.execute(tech_anexos, analysis, company, progress_callback, start_prog=10, total_prog=40)
            
            # --- FASE 6: ECON AGENT (Propuesta Económica) ---
            econ_anexos = cat_anexos.get("economic", [])
            econ_docs = await self.econ_agent.execute(econ_anexos, analysis, company, progress_callback, start_prog=50, total_prog=40)
            
            docs = tech_docs + econ_docs
            
            num_docs = len(docs)
            for i, doc in enumerate(docs):
                file_path = os.path.join(gen_path, doc["name"])
                with open(file_path, "wb") as f:
                    f.write(doc["content"])
                
                await progress_callback(json.dumps({
                    "status": "doc_ready",
                    "name": doc["name"],
                    "folder_name": folder_name,
                    "val": int(90 + ((i + 1) / num_docs) * 10) if num_docs > 0 else 100,
                    "msg": f"Documento guardado: {doc['name']}"
                }) + "\n")

            await progress_callback(json.dumps({
                "status": "complete", 
                "agent": self.name,
                "generated_docs": [d["name"] for d in docs],
                "folder_name": folder_name,
                "msg": f"✅ {len(docs)} documento(s) generados (Técnicos: {len(tech_docs)}, Económicos: {len(econ_docs)})"
            }) + "\n")

        except Exception as e:
            logger.error(f"Coordinator Error (Generation): {str(e)}")
            await progress_callback(json.dumps({"status": "error", "agent": self.name, "msg": str(e)}) + "\n")

    async def answer_question(self, workspace_id: str, question: str, sources: list = []):
        try:
            path = os.path.join(self.workspace_dir, workspace_id)
            analysis = {}
            if os.path.exists(os.path.join(path, "analysis.json")):
                with open(os.path.join(path, "analysis.json"), "r", encoding="utf-8") as f:
                    analysis = json.load(f)
            
            company = await self._get_company_data(workspace_id)
            profile = await self.profile_agent.execute(workspace_id, analysis, company)
            
            q = (question or "").lower()
            if any(k in q for k in ["fecha", "cronograma", "apertura", "visita", "junta", "fallo", "publicación", "publicacion"]):
                fechas = analysis.get("fechas_clave") or analysis.get("fechas") or {}
                pub = analysis.get("fecha_publicacion") or "-"
                visita = fechas.get("visita") or "-"
                junta = fechas.get("aclaraciones") or "-"
                apertura = fechas.get("apertura") or "-"
                fallo = fechas.get("fallo") or "-"
                answer = (
                    f"Cronograma:\n"
                    f"- Publicación: {pub}\n"
                    f"- Visita: {visita}\n"
                    f"- Junta de aclaraciones: {junta}\n"
                    f"- Presentación y apertura: {apertura}\n"
                    f"- Fallo: {fallo}"
                )
                return {"status": "success", "answer": answer}
            
            if any(k in q for k in ["empresa", "licitante", "rfc", "representante", "domicilio", "razón", "razon"]):
                lic = profile.get("licitante", {})
                answer = (
                    f"Empresa licitante: {lic.get('empresa') or '-'}\n"
                    f"RFC: {lic.get('rfc') or '-'}\n"
                    f"Representante: {lic.get('representante') or '-'}\n"
                    f"Cargo: {lic.get('cargo') or 'Representante Legal'}\n"
                    f"Domicilio: {lic.get('domicilio') or '-'}"
                )
                return {"status": "success", "answer": answer}
            
            if any(k in q for k in ["convocante", "licitación", "licitacion", "id", "número", "numero"]):
                tender = profile.get("tender", {})
                answer = (
                    f"Convocante: {tender.get('convocante') or '-'}\n"
                    f"ID de licitación: {tender.get('numero_licitacion') or '-'}\n"
                    f"Objeto: {tender.get('objeto') or '-'}"
                )
                return {"status": "success", "answer": answer}
            
            if any(k in q for k in ["anexo", "formatos", "documentos requeridos"]):
                anexos = analysis.get("anexos_requeridos") or []
                cat = analysis.get("categorized_anexos") or {}
                if not anexos and cat:
                    anexos = (cat.get("technical") or []) + (cat.get("economic") or [])
                if isinstance(anexos, dict):
                    items = []
                    for v in anexos.values():
                        if isinstance(v, list):
                            items.extend(v)
                    anexos = items
                if isinstance(anexos, list) and all(isinstance(x, dict) and "nombre" in x for x in anexos):
                    anexos = [x.get("nombre") for x in anexos]
                lista = anexos if isinstance(anexos, list) else []
                if not lista:
                    return {"status": "success", "answer": "No se detectaron anexos o formatos en las bases actuales."}
                answer = "Formatos/Anexos detectados:\n- " + "\n- ".join([str(x) for x in lista])
                return {"status": "success", "answer": answer}
            
            if any(k in q for k in ["fianza", "garantía", "garantia"]):
                fz = analysis.get("fianzas_requeridas") or {}
                if not fz:
                    return {"status": "success", "answer": "No se detectaron fianzas en las bases actuales."}
                partes = []
                for k, v in fz.items():
                    partes.append(f"{k.replace('_',' ').title()}: {v}")
                answer = "Fianzas requeridas:\n- " + "\n- ".join(partes)
                return {"status": "success", "answer": answer}
            
            answer = await self.chat_agent.execute(workspace_id, question, profile, sources=sources)
            return {"status": "success", "answer": answer}
        except Exception as e:
            logger.error(f"Coordinator Error (Chat): {str(e)}")
            return {"status": "error", "msg": str(e)}
