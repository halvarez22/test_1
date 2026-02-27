import re
from typing import Dict

class LegalExtractor:
    def __init__(self):
        pass

    def _match(self, pattern: str, text: str, flags=0):
        m = re.search(pattern, text, flags)
        if not m:
            return None
        return m.group(1).strip() if m.lastindex else m.group(0).strip()

    def _find_all(self, pattern: str, text: str, flags=0):
        return re.findall(pattern, text, flags)

    def extract_from_text(self, text: str) -> Dict[str, str]:
        t = text or ""
        rfc = self._match(r"\b([A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3})\b", t, re.IGNORECASE)
        escritura_numero = None
        escritura_numero = escritura_numero or self._match(r"ESCRITURA\s*(?:P[ÚU]BLICA)?\s*(?:N[ÚU]MERO|NO\.?)\s*[:#]?\s*([\d,]+)", t, re.IGNORECASE)
        escritura_numero = escritura_numero or self._match(r"\bN[ÚU]MERO\s+([\d,]+)\b", t, re.IGNORECASE)
        tomo = self._match(r"\bTOMO\s*[:#]?\s*([IVXLCDM]+|\d+)\b", t, re.IGNORECASE)
        libro = self._match(r"\bLIBRO\s*[:#]?\s*([IVXLCDM]+|\d+)\b", t, re.IGNORECASE)
        notario_numero = self._match(r"Notario\s+P[úu]blico\s+(?:No\.?|n[úu]mero)\s*[:#]?\s*(\d+)", t, re.IGNORECASE)
        notario_nombre = None
        lic_matches = self._find_all(r"\bLic\.?\s*([A-ZÁÉÍÓÚÑ\s]+)\b", t)
        if lic_matches:
            cand = lic_matches[0].strip()
            if len(cand.split()) >= 2:
                notario_nombre = cand
        if not notario_nombre:
            nn = self._match(r"Notario\s+P[úu]blico\s*(?:No\.?|n[úu]mero)\s*\d+\s*,?\s*([A-ZÁÉÍÓÚÑ\s]+)", t, re.IGNORECASE)
            if nn:
                notario_nombre = nn
        ciudad_estado = None
        ce = self._match(r"\b([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)?)\s*,\s*([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)\b", t)
        if ce:
            ciudad_estado = ce
        fecha_constitucion = None
        fecha_constitucion = fecha_constitucion or self._match(r"\b(\d{1,2}\s+de\s+[A-Za-zÁÉÍÓÚáéíóú]+(?:\s+de)?\s+\d{4})\b", t, re.IGNORECASE)
        denom = None
        denom = denom or self._match(r"\b([A-ZÁÉÍÓÚÑ0-9\s\.\-]+S\.?\s*DE\s*[A-Z\.]+\s*DE\s*C\.?\s*V\.?)\b", t)
        denom = denom or self._match(r"\b([A-ZÁÉÍÓÚÑ0-9\s\.\-]+S\.?\s*A\.?\s*DE\s*C\.?\s*V\.?)\b", t)
        out = {
            "razon_social": denom or "",
            "rfc": rfc or "",
            "escritura_numero": escritura_numero or "",
            "tomo": tomo or "",
            "libro": libro or "",
            "notario_nombre": notario_nombre or "",
            "notario_numero": notario_numero or "",
            "ciudad_estado": ciudad_estado or "",
            "fecha_constitucion": fecha_constitucion or ""
        }
        return out
