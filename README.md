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

