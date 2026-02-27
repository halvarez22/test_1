import httpx
import json
import os
import logging
from .base import BaseAgent
from typing import Callable, List, Dict

logger = logging.getLogger("licitai-agents")

class BaseProposalAgent(BaseAgent):
    def __init__(self, name: str, description: str, docx_url: str, ollama_host: str, ollama_model: str):
        super().__init__(name, description)
        self.docx_url = docx_url
        self.ollama_host = ollama_host
        self.ollama_model = ollama_model

    async def generate_document(self, client, anexo_name: str, tender_data: Dict, company_data: Dict, system_role: str):
        # 1. Pedir al LLM el contenido del documento basado en las bases
        prompt = f"""ERES UN {system_role}.
        Genera el contenido formal y detallado para el documento: '{anexo_name}'.
        
        CONTEXTO DE LA LICITACIÓN:
        - Institución Convocante: {tender_data.get('convocante')}
        - Número de Licitación: {tender_data.get('numero_licitacion')}
        - Objeto del Contrato: {tender_data.get('objeto')}
        - Plazo de Entrega/Ejecución: {tender_data.get('fechas_clave', {}).get('fallo', 'N/D')}
        
        DATOS DEL LICITANTE (MI EMPRESA):
        - Razón Social: {company_data.get('razon_social')}
        - RFC: {company_data.get('rfc')}
        - Domicilio Fiscal: {company_data.get('domicilio')}
        - Representante Legal: {company_data.get('representante')}
        
        REQUISITOS TÉCNICOS LEGALES:
        1. Usa un tono estrictamente formal, legal y jurídico mexicano.
        2. Menciona explícitamente que se actúa "BAJO PROTESTA DE DECIR VERDAD".
        3. Cita que se cumple con los artículos aplicables de la Ley de Adquisiciones, Arrendamientos y Servicios del Sector Público (o la ley estatal equivalente).
        4. Incluye declaraciones sobre: no encontrarse en los supuestos de inhabilitación, integridad de la propuesta y compromiso de cumplimiento.
        
        INSTRUCCIONES DE FORMATO:
        - Divide el contenido en una lista de al menos 4 a 6 párrafos bien redactados.
        - No incluyas el encabezado ni la firma (el sistema los pondrá después).
        - Si falta algún dato específico (como número de escritura), usa [DATOS POR COMPLETAR].
        
        Responde ÚNICAMENTE con un JSON con esta estructura exacta:
        {{
            "parrafos": [
                "En mi carácter de representante legal de...",
                "Manifiesto bajo protesta de decir verdad que...",
                "...",
                "..."
            ]
        }}
        """

        try:
            resp_llm = await client.post(
                f"{self.ollama_host}/api/generate",
                json={
                    "model": self.ollama_model,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json"
                },
                timeout=180.0
            )
            
            if resp_llm.status_code != 200:
                return None
                
            raw_res = resp_llm.json()['response'].strip()
            if raw_res.startswith("```json"): raw_res = raw_res.split("```json")[-1].split("```")[0].strip()
            elif raw_res.startswith("```"): raw_res = raw_res.split("```")[-1].split("```")[0].strip()
            
            llm_data = json.loads(raw_res)
            parrafos = llm_data.get("parrafos", [])
            
            if not parrafos:
                parrafos = [f"Documento {anexo_name} generado automáticamente. Por favor revise el contenido."]
            
            # 2. Llamar al servicio de DOCX
            docx_payload = {
                "convocante": tender_data.get("convocante", ""),
                "licitacion_no": tender_data.get("numero_licitacion", ""),
                "objeto": tender_data.get("objeto", ""),
                "empresa_nombre": company_data.get("razon_social", ""),
                "empresa_rfc": company_data.get("rfc", ""),
                "representante_legal": company_data.get("representante", ""),
                "cargo_representante": company_data.get("cargo", "Representante Legal"),
                "tipo_persona": (company_data.get("tipo_persona") or "PM"),
                "titulo_documento": anexo_name,
                "logo_path": company_data.get("logo_path"),
                "contenido": [{"tipo": "parrafo", "texto": p} for p in parrafos],
                "domicilio_fiscal": company_data.get("domicilio")
            }
            
            resp_docx = await client.post(f"{self.docx_url}/docx/generate", json=docx_payload, timeout=30.0)
            
            if resp_docx.status_code == 200:
                return {
                    "name": f"{anexo_name}.docx",
                    "content": resp_docx.content
                }
        except Exception as e:
            logger.error(f"Error in {self.name}: {str(e)}")
        return None

class TechnicalProposalAgent(BaseProposalAgent):
    def __init__(self, docx_url: str, ollama_host: str, ollama_model: str):
        super().__init__("TechnicalAgent", "Especialista en estructuración de propuestas técnicas.", docx_url, ollama_host, ollama_model)

    async def execute(self, technical_anexos: List[str], tender_data: Dict, company_data: Dict, progress_callback: Callable = None, start_prog: int = 10, total_prog: int = 40):
        results = []
        if not technical_anexos: return results
        
        async with httpx.AsyncClient() as client:
            for i, name in enumerate(technical_anexos):
                prog = start_prog + int((i / len(technical_anexos)) * total_prog)
                await self.emit_progress(progress_callback, prog, f"Técnico: Redactando {name}...")
                doc = await self.generate_document(client, name, tender_data, company_data, "ABOGADO EXPERTO EN LICITACIONES TÉCNICAS")
                if doc: results.append(doc)
        return results

class EconomicProposalAgent(BaseProposalAgent):
    def __init__(self, docx_url: str, ollama_host: str, ollama_model: str):
        super().__init__("EconomicAgent", "Especialista en estructuración de propuestas económicas y financieras.", docx_url, ollama_host, ollama_model)

    async def execute(self, economic_anexos: List[str], tender_data: Dict, company_data: Dict, progress_callback: Callable = None, start_prog: int = 50, total_prog: int = 40):
        results = []
        if not economic_anexos: return results
        
        async with httpx.AsyncClient() as client:
            for i, name in enumerate(economic_anexos):
                prog = start_prog + int((i / len(economic_anexos)) * total_prog)
                await self.emit_progress(progress_callback, prog, f"Económico: Redactando {name}...")
                doc = await self.generate_document(client, name, tender_data, company_data, "EXPERTO EN FINANZAS Y LICITACIONES ECONÓMICAS")
                if doc: results.append(doc)
        return results

# Legacy wrapper for backward compatibility if needed
class DocumentGeneratorAgent(TechnicalProposalAgent):
    async def execute(self, tender_data: Dict, company_data: Dict, progress_callback: Callable = None):
        anexos = tender_data.get("anexos_requeridos", [])
        return await super().execute(anexos, tender_data, company_data, progress_callback)
