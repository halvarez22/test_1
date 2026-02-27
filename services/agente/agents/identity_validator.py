import os
import json
import httpx
import logging
import unicodedata
import re
from difflib import SequenceMatcher
from .base import BaseAgent

logger = logging.getLogger("licitai-agents")

class IdentityValidatorAgent(BaseAgent):
    def __init__(self, db_url: str, workspace_dir: str):
        super().__init__("IdentityValidator", "Validador de identidad entre CIF y Acta.")
        self.db_url = db_url
        self.workspace_dir = workspace_dir

    def _normalize_name(self, s: str) -> str:
        s = s or ""
        s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
        s = s.lower()
        patterns = [
            r"\bs\.?a\.?\s+de\s+c\.?v\.?\b",
            r"\bsociedad\s+anonima\b",
            r"\bsociedad\s+de\s+responsabilidad\s+limitada\b",
            r"\bs\.?\s+de\s+r\.?l\.?\s+de\s+c\.?v\.?\b",
            r"\bsapi\s+de\s+c\.?v\.?\b",
            r"\bde\s+c\.?v\.?\b",
            r"\bs\.?a\.?\b",
            r"\bs\.?\s+de\s+r\.?l\.?\b",
        ]
        for p in patterns:
            s = re.sub(p, "", s, flags=re.IGNORECASE)
        s = re.sub(r"[^a-z0-9]+", " ", s)
        s = re.sub(r"\s+", " ", s).strip()
        return s

    def _similarity(self, a: str, b: str) -> float:
        return SequenceMatcher(None, a or "", b or "").ratio()

    async def execute(self, workspace_id: str):
        async with httpx.AsyncClient() as client:
            ws = {}
            try:
                r = await client.get(f"{self.db_url}/db/workspaces/{workspace_id}", timeout=5.0)
                if r.status_code == 200:
                    ws = r.json()
            except Exception as e:
                logger.warning(f"Workspace fetch error: {e}")
            cif = {}
            acta = {}
            try:
                cif = json.loads(ws.get("cif_data") or "{}")
            except:
                cif = {}
            try:
                acta = json.loads(ws.get("acta_data") or "{}")
            except:
                acta = {}

        rfc_cif = str(cif.get("rfc") or "").strip().upper()
        rfc_acta = str(acta.get("rfc") or "").strip().upper()
        rs_cif = str(cif.get("razon_social") or "").strip()
        rs_acta = str(acta.get("razon_social") or "").strip()
        rep_cif = str(cif.get("representante_legal") or "").strip()
        rep_acta = str(acta.get("representante") or acta.get("representante_legal") or "").strip()
        cargo_acta = str(acta.get("cargo") or "").strip()

        norm_cif = self._normalize_name(rs_cif)
        norm_acta = self._normalize_name(rs_acta)
        sim = self._similarity(norm_cif, norm_acta) if (norm_cif and norm_acta) else None

        inconsistencias = []
        if rfc_cif and rfc_acta and rfc_cif != rfc_acta:
            inconsistencias.append({
                "tipo": "identidad",
                "severidad": "alta",
                "detalle": f"RFC distinto entre CIF ({rfc_cif}) y Acta ({rfc_acta}).",
                "evidencia": {"cif_rfc": rfc_cif, "acta_rfc": rfc_acta},
                "recomendacion": "Editar identidad; priorizar RFC del CIF."
            })
        if sim is not None and sim < 0.85:
            inconsistencias.append({
                "tipo": "identidad",
                "severidad": "media",
                "detalle": f"Razón social con baja similitud ({sim:.2f}) entre CIF y Acta.",
                "evidencia": {"cif_razon_social": rs_cif, "acta_razon_social": rs_acta},
                "recomendacion": "Verificar denominación; ajustar para coherencia."
            })
        if not rep_acta and rep_cif:
            inconsistencias.append({
                "tipo": "identidad",
                "severidad": "baja",
                "detalle": "Acta sin representante, CIF sí lo indica.",
                "evidencia": {"cif_representante": rep_cif},
                "recomendacion": "Cargar acta con nombramiento o confirmar cargo."
            })

        estado = "ok"
        if any(i.get("severidad") == "alta" for i in inconsistencias):
            estado = "block"
        elif inconsistencias:
            estado = "warning"

        result = {
            "comparacion": {
                "rfc_cif": rfc_cif,
                "rfc_acta": rfc_acta,
                "razon_social_cif": rs_cif,
                "razon_social_acta": rs_acta,
                "similitud_razon_social": sim,
                "representante_cif": rep_cif,
                "representante_acta": rep_acta,
                "cargo_acta": cargo_acta,
            },
            "inconsistencias": inconsistencias,
            "estado": estado
        }

        try:
            path = os.path.join(self.workspace_dir, workspace_id)
            os.makedirs(path, exist_ok=True)
            with open(os.path.join(path, "inconsistencias.json"), "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"Write inconsistencias.json error: {e}")

        async with httpx.AsyncClient() as client:
            try:
                await client.post(f"{self.db_url}/db/workspaces", json={
                    "id": workspace_id,
                    "name": workspace_id,
                    "inconsistencias": json.dumps(result, ensure_ascii=False)
                })
            except Exception as e:
                logger.warning(f"Persist inconsistencias error: {e}")

        return result
