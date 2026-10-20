import json
import re
from datetime import datetime
from module.oracle_client import OracleClient

def cargar_json(ruta):
    with open(ruta, 'r') as f:
        return json.load(f)

def obtener_objetos(cliente, esquema, tipo_objeto):
    query = """
        SELECT object_name 
        FROM all_objects 
        WHERE owner = :owner AND object_type = :obj_type
    """
    resultado = cliente.execute_query_json(query, {"owner": esquema, "obj_type": tipo_objeto})
    if not resultado["success"]:
        return set()
    return {fila["OBJECT_NAME"] for fila in resultado["rows"]}

def obtener_columnas(cliente, esquema):
    query = """
        SELECT table_name, column_name, data_type, data_length, data_precision, data_scale
        FROM all_tab_columns 
        WHERE owner = :owner
    """
    resultado = cliente.execute_query_json(query, {"owner": esquema})
    if not resultado["success"]:
        return {}
    
    columnas_dict = {}
    for fila in resultado["rows"]:
        tabla = fila["TABLE_NAME"]
        col = fila["COLUMN_NAME"]
        tipo = f"{fila['DATA_TYPE']}({fila.get('DATA_LENGTH', '')},{fila.get('DATA_PRECISION', '')},{fila.get('DATA_SCALE', '')})"
        tipo = tipo.replace("(None,None)", "").replace("(,)", "")
        columnas_dict[f"{tabla}.{col}"] = tipo
    return columnas_dict

def obtener_codigo_fuente(cliente, esquema, tipo_objeto, nombre_objeto):
    tipo_meta = tipo_objeto.replace(" ", "_") 
    query = """
        SELECT DBMS_METADATA.GET_DDL(:obj_type, :obj_name, :owner) AS DDL 
        FROM DUAL
    """
    try:
        res = cliente.execute_query_json(query, {"obj_type": tipo_meta, "obj_name": nombre_objeto, "owner": esquema})
        if res["success"] and res["rows"]:
            return res["rows"][0]["DDL"].strip()
    except Exception as e:
        pass
    return ""

def normalizar_codigo(codigo, esquema):
    if not codigo: return ""
    codigo = re.sub(f'"{esquema}"\.', '', codigo, flags=re.IGNORECASE)
    codigo = re.sub(f'{esquema}\.', '', codigo, flags=re.IGNORECASE)
    codigo = re.sub(r'\s+', ' ', codigo)
    return codigo.strip().upper()

def main():
    creds = cargar_json('config/credenciales.json')
    config = cargar_json('config/config.json')

    esquema1 = config["esquema_origen"].upper()
    esquema2 = config["esquema_destino"].upper()

    informe = []
    ddl_sync = []
    
    informe.append(f"# Reporte de Diferencias de Esquemas (Formato Lista Mejorado)")
    informe.append(f"**Fecha:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    informe.append(f"- **Origen (Maestro):** {esquema1}")
    informe.append(f"- **Destino (A sincronizar):** {esquema2}\n")
    informe.append("---\n")

    print("Analizando esquemas y generando reporte en formato vertical...")

    with OracleClient(cfg=creds["BBDD_ORIGEN"]) as cliente_origen, \
         OracleClient(cfg=creds["BBDD_DESTINO"]) as cliente_destino:
        
        tipos_codigo = ["VIEW", "PROCEDURE", "FUNCTION", "PACKAGE", "PACKAGE BODY", "TRIGGER"]

        # 1. Comparación de Objetos y Código
        for tipo in config.get("objetos_a_comparar", []):
            informe.append(f"## Comparación de {tipo}S")
            obj_esq1 = obtener_objetos(cliente_origen, esquema1, tipo)
            obj_esq2 = obtener_objetos(cliente_destino, esquema2, tipo)

            solo_en_1 = obj_esq1 - obj_esq2
            solo_en_2 = obj_esq2 - obj_esq1
            comunes = obj_esq1.intersection(obj_esq2)

            if solo_en_1:
                informe.append(f"**Solo en {esquema1} ({len(solo_en_1)}):**")
                for obj in sorted(solo_en_1):
                    informe.append(f"- {obj}")
                
                ddl_sync.append(f"\n-- Faltan {tipo}S en {esquema2}")
                for obj in sorted(solo_en_1):
                    if tipo in tipos_codigo:
                        ddl_obj = obtener_codigo_fuente(cliente_origen, esquema1, tipo, obj)
                        if ddl_obj:
                            ddl_obj = re.sub(f'"{esquema1}"\.', f'"{esquema2}".', ddl_obj, flags=re.IGNORECASE)
                            ddl_sync.append(f"{ddl_obj}\n/")
                    else:
                        ddl_sync.append(f"-- EXTRAER DDL DE: {esquema1}.{obj} y ejecutar en {esquema2};")
            
            if solo_en_2:
                informe.append(f"**Solo en {esquema2} ({len(solo_en_2)}):**")
                for obj in sorted(solo_en_2):
                    informe.append(f"- {obj}")
                
                ddl_sync.append(f"\n-- Sobran {tipo}S en {esquema2} (Opcional)")
                for obj in sorted(solo_en_2):
                    ddl_sync.append(f"DROP {tipo} {esquema2}.{obj};")

            # Análisis de código fuente
            if tipo in tipos_codigo and comunes:
                diferencias_codigo = []
                for obj in sorted(comunes):
                    cod1 = obtener_codigo_fuente(cliente_origen, esquema1, tipo, obj)
                    cod2 = obtener_codigo_fuente(cliente_destino, esquema2, tipo, obj)
                    
                    if normalizar_codigo(cod1, esquema1) != normalizar_codigo(cod2, esquema2):
                        diferencias_codigo.append(obj)
                        cod_sincro = re.sub(f'"{esquema1}"\.', f'"{esquema2}".', cod1, flags=re.IGNORECASE)
                        ddl_sync.append(f"\n-- ACTUALIZAR {tipo}: {obj}")
                        ddl_sync.append(f"{cod_sincro}\n/")

                if diferencias_codigo:
                    informe.append(f"**Difieren en su código fuente ({len(diferencias_codigo)}):**")
                    for dif in sorted(diferencias_codigo):
                        informe.append(f"- {dif}")
                elif not solo_en_1 and not solo_en_2:
                    informe.append(f"- *Todos los {tipo}s coinciden exactamente en existencia y código.*")
            else:
                if not solo_en_1 and not solo_en_2:
                    informe.append(f"- *Todos los {tipo}s coinciden.*")
            
            informe.append("\n")

        # 2. Comparación de Columnas (Tablas comunes)
        if config.get("comparar_columnas") and "TABLE" in config.get("objetos_a_comparar", []):
            informe.append("## Estructura de Columnas (Tablas comunes)\n")
            
            cols_esq1 = obtener_columnas(cliente_origen, esquema1)
            cols_esq2 = obtener_columnas(cliente_destino, esquema2)

            tablas_esq1 = obtener_objetos(cliente_origen, esquema1, "TABLE")
            tablas_esq2 = obtener_objetos(cliente_destino, esquema2, "TABLE")
            tablas_comunes = tablas_esq1.intersection(tablas_esq2)

            diferencias_encontradas = False
            ddl_sync.append("\n-- Sincronización de columnas en tablas existentes")

            for tabla in sorted(tablas_comunes):
                c1 = {k: v for k, v in cols_esq1.items() if k.startswith(f"{tabla}.")}
                c2 = {k: v for k, v in cols_esq2.items() if k.startswith(f"{tabla}.")}

                faltan_en_2 = set(c1.keys()) - set(c2.keys())
                faltan_en_1 = set(c2.keys()) - set(c1.keys())
                
                if faltan_en_2 or faltan_en_1:
                    informe.append(f"### Tabla: {tabla} (Columnas faltantes)")
                    if faltan_en_2:
                        informe.append(f"**Solo en {esquema1}:**")
                        for col in sorted(faltan_en_2):
                            nom_col = col.split('.')[1]
                            informe.append(f"- {nom_col}")
                            ddl_sync.append(f"ALTER TABLE {esquema2}.{tabla} ADD ({nom_col} {c1[col]});")
                    if faltan_en_1:
                        informe.append(f"**Solo en {esquema2}:**")
                        for col in sorted(faltan_en_1):
                            nom_col = col.split('.')[1]
                            informe.append(f"- {nom_col}")
                            ddl_sync.append(f"ALTER TABLE {esquema2}.{tabla} DROP COLUMN {nom_col};")
                    diferencias_encontradas = True

                comunes = set(c1.keys()).intersection(set(c2.keys()))
                difs_tipos = []
                for col in sorted(comunes):
                    if c1[col] != c2[col]:
                        nombre_col = col.split('.')[1]
                        difs_tipos.append(f"- `{nombre_col}` -> {esquema1}: `{c1[col]}` | {esquema2}: `{c2[col]}`")
                        ddl_sync.append(f"ALTER TABLE {esquema2}.{tabla} MODIFY ({nombre_col} {c1[col]});")
                        diferencias_encontradas = True
                
                if difs_tipos:
                    informe.append(f"### Tabla: {tabla} (Diferencias de tipo)")
                    informe.extend(difs_tipos)
                    informe.append("\n")

            if not diferencias_encontradas:
                informe.append("- *Todas las columnas y tipos de datos coinciden en las tablas comunes.*")

    if ddl_sync:
        informe.append("\n## Scripts de Sincronización (DDL Sugerido)")
        informe.append("Ejecuta estos comandos en tu base de datos DESTINO para igualar la estructura y el código con el ORIGEN.\n")
        informe.append("```sql")
        informe.extend(ddl_sync)
        informe.append("```\n")

    nombre_archivo = "reporte_diferencias.md"
    with open(nombre_archivo, "w", encoding="utf-8") as f:
        f.write("\n".join(informe))
    
    print(f"¡Proceso completado! Se ha generado el archivo: {nombre_archivo}")

if __name__ == "__main__":
    main()