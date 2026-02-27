# LicitAI – Servicios (Agente, Memoria, Dashboard)

## Estructura

- services/agente
  - main.py (API del agente)
  - agents/
    - parser.py (OCR → Prompt LLM → analysis.json)
    - coordinator.py (orquestación)
    - identity_validator.py, profile.py, legal_extractor.py
- services/memoria
  - main.py (DB API – SQLite)
- services/dashboard
  - public/ (UI)

## Puesta en marcha (Docker)

1. docker compose up -d
2. Agente: http://localhost:8080/health
3. Memoria DB: http://localhost:8083/health
4. Dashboard local: services/dashboard (node index.js)

## Flujo de análisis

1) Subes bases (PDF/TXT) → OCR → extraccion_*.txt
2) parser.py crea analysis.json (convocante, fechas, anexos…)
3) Dashboard representa Studio con tarjetas

## Prompt y “puntos_criticos”

El prompt en agents/parser.py está restringido para no inventar campos físicos en procedimientos electrónicos (ComprasMX). Si no aparece en el texto, marca “NO APLICA” o “NO ESPECIFICADO”.

## Notas

- data/workspaces está excluido del repo por .gitignore
- Revisa services/agente/agents/parser.py para ver el prompt actualizado

## Mapeo tipo_entidad → Requisitos principales del checklist

| tipo_entidad        | Requisitos base (siempre)                                 | Requisitos adicionales específicos                                      | Ítems que NO aplican                                  |
|---------------------|-----------------------------------------------------------|--------------------------------------------------------------------------|-------------------------------------------------------|
| federal_electronica | SAT 32-D, IMSS, INFONAVIT, Acta, CIF, Idioma, Fianzas     | RUPC vigente, Firma electrónica, Envío por ComprasMX                     | Firma física en fojas, Entrega en sobre físico        |
| local_presencial    | SAT 32-D, IMSS, INFONAVIT, Acta, CIF, Idioma, Fianzas     | Firma de todas las fojas, Entrega física (dirección explícita), Sobre    | RUPC (si no es federal), Firma electrónica obligatoria |
| mixta               | SAT 32-D, IMSS, INFONAVIT, Acta, CIF, Idioma, Fianzas     | Combinación según texto (detectar ambos modos)                           | Ítems contradictorios según clasificación              |

Notas:
- La clasificación se realiza en services/agente/agents/template_selector.py.
- El parser fija puntos_criticos.tipo_procedimiento y, si es electrónico, fuerza “NO APLICA” en dirigido_a/firma_requerida/lugar_entrega.
- El endpoint /api/compliance/apply usa tipo_entidad para derivar el checklist.

## Ejemplos de salida (resumen)

Ejemplo federal_electronica:

```json
{
  "tipo_entidad": "federal_electronica",
  "puntos_criticos": {
    "tipo_procedimiento": "electrónico",
    "dirigido_a": "NO APLICA - Procedimiento 100% electrónico vía ComprasMX",
    "firma_requerida": "NO APLICA - Procedimiento 100% electrónico vía ComprasMX",
    "lugar_entrega": "NO APLICA - Procedimiento 100% electrónico vía ComprasMX",
    "advertencias": ["Proposiciones exclusivamente en idioma español", "Cotizaciones en pesos mexicanos"]
  }
}
```

Ejemplo local_presencial (simulado):

```json
{
  "tipo_entidad": "local_presencial",
  "puntos_criticos": {
    "tipo_procedimiento": "presencial",
    "dirigido_a": "C. Presidente Municipal de X",
    "firma_requerida": "Firmar todas las fojas y el sobre",
    "lugar_entrega": "Casa de la Cultura, Calle Y #123, Municipio Z",
    "advertencias": ["No tachaduras", "Sobre sellado"]
  }
}
```
