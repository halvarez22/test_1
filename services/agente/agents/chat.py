import httpx
import json
import logging
import os
import glob
from .base import BaseAgent
from typing import Dict

logger = logging.getLogger("licitai-agents")

class ChatAgent(BaseAgent):
    def __init__(self, ollama_host: str, ollama_model: str, workspace_dir: str):
        super().__init__("AsistenteChat", "Especialista en responder preguntas sobre los documentos de la licitación.")
        self.ollama_host = ollama_host
        self.ollama_model = ollama_model
        self.workspace_dir = workspace_dir

    def _get_context(self, workspace_id: str):
        """Busca el texto extraído más relevante en el workspace"""
        path = os.path.join(self.workspace_dir, workspace_id)
        if not os.path.exists(path):
            return "No hay documentos analizados en este workspace."
        
        # Prioridad 1: analysis.json (resumen)
        # Prioridad 2: extraccion_*.txt (texto completo)
        context_parts = []
        
        analysis_path = os.path.join(path, "analysis.json")
        if os.path.exists(analysis_path):
            with open(analysis_path, "r", encoding="utf-8") as f:
                analysis = json.load(f)
                context_parts.append("RESUMEN DE LA LICITACIÓN:")
                context_parts.append(json.dumps(analysis, indent=2, ensure_ascii=False))
        
        # Cargar fragmentos de las extracciones (limitado para no desbordar el contexto)
        extractions = glob.glob(os.path.join(path, "extraccion_*.txt"))
        if extractions:
            context_parts.append("\nFRAGMENTOS DE LAS BASES:")
            for ext in extractions[:2]: # Máximo 2 archivos de fuente
                with open(ext, "r", encoding="utf-8") as f:
                    content = f.read()
                    # Tomar los primeros 4000 caracteres como contexto relevante básico
                    context_parts.append(content[:4000])
        
        return "\n".join(context_parts)

    async def execute(self, workspace_id: str, question: str, tender_profile: Dict, sources: list = []):
        """Responde una pregunta usando el contexto del workspace"""
        context = self._get_context(workspace_id)
        
        # Formatear lista de fuentes para el Chat
        sources_txt = "\n".join([f"- {s}" for s in sources]) if sources else "No especificadas."
        
        licitante = tender_profile.get('licitante', {})

        prompt = f"""ERES EL ASISTENTE LEGAL DE LICITAI.
        Tu objetivo es responder preguntas con precisión basándote en la información proporcionada.
        
        DATOS DE LA EMPRESA (LICITANTE):
        - Nombre/Razon Social: {licitante.get('empresa', 'N/D')}
        - RFC: {licitante.get('rfc', 'N/D')}
        - Representante Legal: {licitante.get('representante', 'N/D')}
        - Cargo: {licitante.get('cargo', 'Representante Legal')}
        - Domicilio Fiscal: {licitante.get('domicilio', 'N/D')}
        
        DOCUMENTOS FUENTE CARGADOS EN ESTE CUADERNO:
        {sources_txt}

        DATOS DE LA LICITACIÓN:
        - Número: {tender_profile.get('tender', {}).get('numero_licitacion', 'N/D')}
        - Convocante: {tender_profile.get('tender', {}).get('convocante', 'N/D')}
        - Objeto: {tender_profile.get('tender', {}).get('objeto', 'N/D')}

        CONTEXTO EXTRAÍDO DE LOS DOCUMENTOS (BASES):
        {context[:10000]}
        
        PREGUNTA DEL USUARIO:
        {question}
        
        REGLAS DE RESPUESTA:
        1. Responde de forma profesional, clara y directa.
        2. Si preguntan sobre la empresa o domicilio, usa los DATOS DE LA EMPRESA arriba proporcionados.
        3. Si preguntan por cuántos o cuáles archivos hay, usa la lista de DOCUMENTOS FUENTE.
        4. Si la información no está en ninguna parte, di amablemente que no se encuentra en las fuentes actuales.
        5. No inventes datos que no estén aquí.
        
        Responde en español.
        """

        try:
            async with httpx.AsyncClient() as client:
                payload = {
                    "model": self.ollama_model,
                    "prompt": prompt,
                    "stream": False
                }
                resp = await client.post(f"{self.ollama_host}/api/generate", json=payload, timeout=60.0)
                if resp.status_code == 200:
                    return resp.json().get("response", "No pude generar una respuesta.")
        except Exception as e:
            logger.error(f"Error en ChatAgent: {e}")
            return f"Lo siento, tuve un error técnico al procesar tu pregunta: {str(e)}"
        
        return "Lo siento, el servicio de inteligencia no respondió."
