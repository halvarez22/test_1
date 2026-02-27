import json
import logging
import httpx
from .base import BaseAgent
from typing import Dict

logger = logging.getLogger("licitai-agents")

class ProfileAgent(BaseAgent):
    def __init__(self, db_url: str):
        super().__init__("ProfileAgent", "Especialista en identificación de perfiles de empresa y datos fiscales.")
        self.db_url = db_url

    async def execute(self, workspace_id: str, current_analysis: Dict, company_data: Dict):
        """
        Recaba y unifica los datos de la licitación (Convocante) y del Licitante (Empresa).
        Si company_data viene con defaults, intenta buscar un perfil global en la DB.
        """
        logger.info(f"Recabando datos unificados para workspace {workspace_id}")
        
        # Validar si tenemos datos reales o solo defaults
        has_real_data = (company_data.get("rfc") != "XAXX010101000")
        
        final_company = company_data.copy()

        if not has_real_data:
            # Intentar recuperar el perfil más reciente de la tabla 'companies' o de cualquier otro workspace
            async with httpx.AsyncClient() as client:
                try:
                    # Buscamos en la lista de workspaces el que tenga cif_data
                    resp = await client.get(f"{self.db_url}/db/workspaces", timeout=2.0)
                    if resp.status_code == 200:
                        all_ws = resp.json()
                        for ws in all_ws:
                            cif = json.loads(ws.get("cif_data") or "{}")
                            acta = json.loads(ws.get("acta_data") or "{}")
                            
                            if (cif.get("rfc") and cif.get("rfc") != "XAXX010101000") or acta.get("representante"):
                                logger.info(f"Encontrado perfil global en workspace {ws['id']}")
                                final_company.update({
                                    "razon_social": cif.get("razon_social") or acta.get("razon_social") or final_company.get("razon_social"),
                                    "rfc": cif.get("rfc") or final_company.get("rfc"),
                                    "representante": acta.get("representante") or cif.get("representante_legal") or final_company.get("representante"),
                                    "domicilio": cif.get("domicilio_fiscal") or final_company.get("domicilio"),
                                    "logo_path": ws.get("logo_path") or final_company.get("logo_path"),
                                    "cargo": acta.get("cargo") or final_company.get("cargo") or "Representante Legal"
                                })
                                break
                except Exception as e:
                    logger.warning(f"Error buscando perfil global: {e}")

        def safe_str(v, default="N/D"):
            if not v or v == "None": return default
            if isinstance(v, dict): return " ".join([str(x) for x in v.values() if x])
            if isinstance(v, list): return " ".join([str(x) for x in v])
            return str(v)

        profile = {
            "tender": {
                "convocante": safe_str(current_analysis.get("convocante")),
                "numero_licitacion": safe_str(current_analysis.get("numero_licitacion")),
                "objeto": safe_str(current_analysis.get("objeto")),
                "fecha_publicacion": safe_str(current_analysis.get("fecha_publicacion"))
            },
            "licitante": {
                "empresa": safe_str(final_company.get("razon_social")),
                "rfc": safe_str(final_company.get("rfc"), "XAXX010101000"),
                "domicilio": safe_str(final_company.get("domicilio")),
                "representante": safe_str(final_company.get("representante")),
                "cargo": safe_str(final_company.get("cargo"), "Representante Legal"),
                "logo_path": final_company.get("logo_path", "")
            }
        }
        
        return profile
