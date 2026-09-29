# tools/vigia_bloqueos.py
#
# Vigilante TEMPORAL de bloqueos por metadata lock en MySQL (almacenes).
# Mientras la app de producción (mototrbo) no tenga el arreglo de conexiones.py
# (rollback al final de cada request), sus workers ociosos dejan transacciones de
# lectura abiertas; cuando el FoxPro ejecuta ALTER TABLE … AUTO_INCREMENT queda
# esperando y detrás se encola todo el sistema.
#
# Solo actúa si alguna consulta lleva >= ESPERA_MIN s esperando un metadata lock, y
# entonces corta ÚNICAMENTE conexiones que cumplan TODO:
#   - vienen de la app web de mototrbo (mysql-connector-python, _source_host=mototrbo)
#   - están ociosas (Sleep) hace >= OCIOSA_MIN s
#   - tienen una transacción de solo lectura (0 filas modificadas)
# La app se reconecta sola. Nunca toca el FoxPro ni otras aplicaciones.
#
# Quitar cuando producción tenga el hotfix:  docker rm -f almacenes_vigia
import os
import time
import datetime
import mysql.connector

ESPERA_MIN = 20
OCIOSA_MIN = 30
INTERVALO  = 10

SQL_ESPERAS = """
    SELECT id, time, LEFT(info, 100) FROM information_schema.processlist
    WHERE state = 'Waiting for table metadata lock' AND time >= %s
"""
SQL_CANDIDATAS = """
    SELECT t.trx_mysql_thread_id, p.time
    FROM information_schema.innodb_trx t
    JOIN information_schema.processlist p ON p.id = t.trx_mysql_thread_id
    JOIN performance_schema.session_connect_attrs c ON c.processlist_id = p.id
         AND c.attr_name = '_connector_name' AND c.attr_value = 'mysql-connector-python'
    JOIN performance_schema.session_connect_attrs h ON h.processlist_id = p.id
         AND h.attr_name = '_source_host' AND h.attr_value = 'mototrbo'
    WHERE p.command = 'Sleep' AND p.time >= %s AND t.trx_rows_modified = 0
"""


def log(msg):
    print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def conectar():
    return mysql.connector.connect(host=os.environ["MYSQL_HOST"], user=os.environ["MYSQL_USER"],
                                   password=os.environ["MYSQL_PASSWORD"], autocommit=True,
                                   connect_timeout=5, read_timeout=20)


def ciclo(cur):
    cur.execute(SQL_ESPERAS, (ESPERA_MIN,))
    esperas = cur.fetchall()
    if not esperas:
        return
    log(f"bloqueo detectado: {esperas[:3]}{' …' if len(esperas) > 3 else ''} ({len(esperas)} esperando)")
    cur.execute(SQL_CANDIDATAS, (OCIOSA_MIN,))
    for tid, ociosa in cur.fetchall():
        try:
            cur.execute(f"KILL {int(tid)}")
            log(f"  KILL {tid} (app mototrbo, ociosa {ociosa}s, transacción de solo lectura)")
        except Exception as e:
            log(f"  no se pudo cortar {tid}: {e}")


def main():
    log(f"vigía iniciado (espera >= {ESPERA_MIN}s, ociosa >= {OCIOSA_MIN}s, cada {INTERVALO}s)")
    conn = None
    while True:
        try:
            if conn is None or not conn.is_connected():
                conn = conectar()
            cur = conn.cursor()
            ciclo(cur)
            cur.close()
        except Exception as e:
            log(f"error: {e}")
            conn = None
        time.sleep(INTERVALO)


if __name__ == "__main__":
    main()
