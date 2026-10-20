# 🗄️ Oracle Schema Diff & DDL Sync Generator

![Python](https://img.shields.io/badge/Python-3.8%2B-blue?logo=python&logoColor=white)
![Oracle](https://img.shields.io/badge/Oracle%20DB-19c%20%7C%2021c-F80000?logo=oracle&logoColor=white)
![python-oracledb](https://img.shields.io/badge/Driver-python--oracledb-orange)
![Output](https://img.shields.io/badge/Report-Markdown%20%2B%20DDL-success)

Herramienta de automatización para **DBAs e Ingenieros Backend** desarrollada en Python que analiza, audita y compara estructuralmente dos esquemas de base de datos Oracle (`Origen / Maestro` vs. `Destino / Réplica`). 

Detecta discrepancias en objetos del catálogo, tipos de datos de columnas y código fuente PL/SQL, generando automáticamente un **informe técnico en Markdown** junto con el **script DDL de sincronización** necesario para homologar ambos entornos.

---

## 🚀 Características Principales

* **🔍 Auditoría Multi-Objeto (`ALL_OBJECTS`):** Detecta tablas, vistas, procedimientos almacenados, funciones, paquetes (`SPEC` y `BODY`) y triggers que existen en el entorno maestro pero faltan o sobran en el entorno destino.
* **🧠 Comparación Inteligente de Código PL/SQL (`DBMS_METADATA.GET_DDL`):** 
  * Extrae el DDL nativo de los objetos lógicos en memoria.
  * **Motor de Normalización con RegEx:** Limpia referencias explícitas a nombres de esquemas (`"ESQUEMA".`), saltos de línea, tabulaciones y diferencias de mayúsculas/minúsculas antes de comparar, evitando falsos positivos por formato.
* **📊 Inspección Profunda de Columnas (`ALL_TAB_COLUMNS`):** Analiza las tablas comunes entre ambos esquemas para identificar columnas ausentes o diferencias en tipos de datos, longitudes (`DATA_LENGTH`), precisión (`DATA_PRECISION`) y escala (`DATA_SCALE`).
* **⚡ Generador Automático de Scripts DDL:** Construye sentencias `CREATE OR REPLACE`, `ALTER TABLE ... ADD / MODIFY / DROP COLUMN` y `DROP` adaptadas dinámicamente con el nombre del esquema destino, listas para su ejecución[cite: 2].
* **📄 Reporte Técnico en Markdown:** Exporta el resultado completo en un documento estructurado (`reporte_diferencias.md`) fácil de integrar en flujos CI/CD, wikis o revisiones de código[cite: 2].

---

## 🏗️ Estructura del Proyecto

```text
├── comparador.py               # Motor principal de análisis, normalización y generación DDL
├── requirements.txt            # Dependencias del proyecto
├── module/
│   └── oracle_client.py        # Wrapper de conexión con Context Manager y ejecución JSON
└── config/
    ├── credenciales.json       # Parámetros de conexión (BBDD Origen y Destino)
    └── config.json             # Reglas de negocio, esquemas y tipos de objetos a auditar