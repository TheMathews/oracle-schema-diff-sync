"""
module.oracle_client
====================

Cliente Oracle avanzado para Python con soporte para consultas clásicas,
DML, procedimientos almacenados y JSON. Incluye gestión de contexto 
transparente para auditoría y trazabilidad en base de datos.

Version: 1.3.1
Build date: 2026-04-29

Mejoras de esta versión:
------------------------
1. Soporte de Contexto: Inyección automática de CLIENT_INFO y CLIENT_IDENTIFIER.
2. Compatibilidad Thin Mode: Uso de bloques PL/SQL para asegurar que los 
   Triggers de Oracle vean el contexto de sesión inmediatamente.
3. Robustez: Gestión de contexto integrada en el ciclo de vida del Pool.
"""

import oracledb
import time
import logging
from datetime import datetime, date
from decimal import Decimal
from typing import Any, Generator

class SafeJSONEncoder(oracledb.JSONEncoder if hasattr(oracledb, "JSONEncoder") else object):
    """
    Encoder para JSON que maneja tipos específicos de Oracle y Python.
    """
    def default(self, obj: Any):
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, oracledb.LOB):
            return obj.read()
        return super().default(obj) if hasattr(super(), "default") else str(obj)


class OracleClient:
    """
    Cliente Oracle con gestión de pool y contexto de sesión automático.
    
    Attributes:
        app_name (str): Identificador de la aplicación para CLIENT_INFO.
        user_app (str): Identificador del usuario para CLIENT_IDENTIFIER.
    """

    __version__ = "1.3.2"
    __build_date__ = "2026-09-07"

    def __init__(self, cfg: dict, pool_min=1, pool_max=5, retry=1, app_name: str = None, user_app: str = None):
        """
        Inicializa el cliente y configura el pool de conexiones.

        :param cfg: Diccionario con credenciales (servidor, puerto, database, usuario, password).
        :param pool_min: Mínimo de conexiones abiertas.
        :param pool_max: Máximo de conexiones en el pool.
        :param retry: Número de reintentos automáticos ante fallos de conexión.
        :param app_name: Nombre de la aplicación para CLIENT_INFO en Oracle.
        :param user_app: Identificador de usuario para CLIENT_IDENTIFIER en Oracle.
        """
        if "BBDD" in cfg:
            cfg = cfg["BBDD"]
        
        self.cfg = cfg
        self.retry = retry
        self.app_name = app_name
        self.user_app = user_app
        self.logger = logging.getLogger("OracleClient")
        
        dsn = f'{cfg["servidor"]}:{cfg["puerto"]}/{cfg["database"]}'
        
        self.pool = oracledb.create_pool(
            user=cfg["usuario"],
            password=cfg["password"],
            dsn=dsn,
            min=pool_min,
            max=pool_max,
            increment=1,
            getmode=oracledb.POOL_GETMODE_WAIT,
            wait_timeout=30
        )

    def _set_session_context(self, conn: oracledb.Connection):
        """
        Inyecta app_name y user_app en la sesión actual de Oracle.
        
        Utiliza una combinación de propiedades nativas del driver y llamadas 
        explícitas a DBMS_APPLICATION_INFO y DBMS_SESSION para garantizar que 
        los Triggers y las vistas V$SESSION vean los datos inmediatamente.
        
        :param conn: Objeto de conexión activo obtenido del pool.
        """
        # 1. Propiedades del driver (para herramientas de monitoreo externas)
        if self.app_name: conn.client_info = self.app_name
        if self.user_app: conn.client_identifier = self.user_app

        # 2. PL/SQL (para visibilidad inmediata en Triggers y SYS_CONTEXT)
        if self.app_name or self.user_app:
            with conn.cursor() as cursor:
                cursor.execute("""
                    BEGIN
                        IF :app IS NOT NULL THEN DBMS_APPLICATION_INFO.SET_CLIENT_INFO(:app); END IF;
                        IF :usr IS NOT NULL THEN DBMS_SESSION.SET_IDENTIFIER(:usr); END IF;
                    END;
                """, app=self.app_name, usr=self.user_app)

    def _result_base(self):
        """
        Crea la estructura de respuesta estándar para todos los métodos.
        
        :return: Diccionario con campos predefinidos para éxito, filas, error y metadatos.
        """        
        return {
            "library_version": self.__version__,
            "build_date": self.__build_date__,
            "success": False,
            "rows": [],
            "rowcount": 0,
            "output": [],
            "duration": 0.0,
            "error": None,
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }

    def _execute(self, func):
        """
        Método núcleo que envuelve todas las operaciones de base de datos.
        
        Maneja el ciclo de vida completo: 
        1. Adquisición de conexión del pool.
        2. Configuración de contexto (auditoría).
        3. Ejecución de la lógica de negocio.
        4. Gestión de excepciones y reintentos.
        5. Medición de tiempos.

        :param func: Función ejecutable que recibe 'conn' como argumento.
        :return: Diccionario de resultado estándar generado por _result_base.
        """
        result = self._result_base()
        start = time.time()
        attempts = 0
        while attempts <= self.retry:
            try:
                with self.pool.acquire() as conn:
                    self._set_session_context(conn)
                    value = func(conn)
                    if isinstance(value, dict):
                        result.update(value)
                    result["success"] = True
                    break
            except Exception as e:
                result["error"] = str(e)
                attempts += 1
                if attempts > self.retry:
                    self.logger.error(f"Oracle Error: {e}")
                    # Añadimos un raise para que el error suba al orquestador (main.py)
                    # y aborte el procesamiento del diccionario actual.
                    raise RuntimeError(f"Fallo en base de datos tras {self.retry} reintentos: {str(e)}") 
        
        result["duration"] = round(time.time() - start, 4)
        return result

    # --- Métodos de Acción ---

    def execute_query(self, query: str, params: dict | None = None):
        """
        Ejecuta una consulta SELECT y devuelve los resultados en formato de lista de tuplas.
        Incluye metadatos de las columnas.
        """
        def run(conn):
            with conn.cursor() as cursor:
                cursor.execute(query, params or {})
                rows = cursor.fetchall()
                columns = [d[0] for d in cursor.description]
                return {"rows": rows, "columns": columns, "rowcount": len(rows)}
        return self._execute(run)

    def execute_dml(self, query: str, params: dict | None = None):
        """
        Ejecuta operaciones INSERT, UPDATE o DELETE. 
        Asegura la transacción con un commit automático tras el éxito.
        """
        def run(conn):
            with conn.cursor() as cursor:
                cursor.execute(query, params or {})
                conn.commit()
                return {"rowcount": max(cursor.rowcount, 0)}
        return self._execute(run)

    def bulk_insert(self, query: str, data: list, batch_size: int = None):
        """
        Realiza una carga masiva de datos utilizando el método executemany.
        Permite el procesamiento por lotes (batching) para optimizar memoria.
        """
        def run(conn):
            with conn.cursor() as cursor:
                if batch_size:
                    for i in range(0, len(data), batch_size):
                        cursor.executemany(query, data[i:i + batch_size])
                else:
                    cursor.executemany(query, data)
                conn.commit()
                return {"rowcount": cursor.rowcount}
        return self._execute(run)

    def call_procedure(self, proc_name: str, params: list | None = None):        
        def run(conn):
            with conn.cursor() as cursor:
                cursor.callproc(proc_name, params or [])
                conn.commit()
                return {}
        return self._execute(run)

    def execute_query_json(self, query: str, params: dict | None = None):
        """
        Ejecuta un SELECT y transforma el resultado en una lista de diccionarios.
        Maneja automáticamente la conversión de LOBs, fechas y tipos numéricos.
        """
        def run(conn):
            with conn.cursor() as cursor:
                cursor.execute(query, params or {})
                columns = [d[0] for d in cursor.description]
                res = []
                for row in cursor.fetchall():
                    row_dict = {}
                    for col, val in zip(columns, row):
                        if isinstance(val, oracledb.LOB): val = val.read()
                        elif isinstance(val, (datetime, date)): val = val.isoformat()
                        elif isinstance(val, Decimal): val = float(val)
                        row_dict[col] = val
                    res.append(row_dict)
                return {"rows": res, "columns": columns, "rowcount": len(res)}
        return self._execute(run)

    def stream_query_json(self, query: str, params: dict | None = None, batch_size: int = 1000):
        """
        Generador para consultas de gran volumen. 
        Devuelve diccionarios fila por fila sin cargar todo el conjunto de datos en RAM.
        """
        conn = self.pool.acquire()
        try:
            self._set_session_context(conn)
            cursor = conn.cursor()
            cursor.execute(query, params or {})
            cols = [d[0] for d in cursor.description]
            while True:
                rows = cursor.fetchmany(batch_size)
                if not rows: break
                for row in rows:
                    row_dict = {}
                    for col, val in zip(cols, row):
                        if isinstance(val, oracledb.LOB): val = val.read()
                        elif isinstance(val, (datetime, date)): val = val.isoformat()
                        elif isinstance(val, Decimal): val = float(val)
                        row_dict[col] = val
                    yield row_dict
        finally:
            cursor.close()
            conn.close()

    def __enter__(self): return self
    def __exit__(self, exc_type, exc_val, exc_tb): self.close()
    def close(self):
        if self.pool: self.pool.close()