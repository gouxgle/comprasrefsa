# conexiones.py
import os
import mysql.connector
from mysql.connector import Error

DB_CONFIG = {
    "host":            os.environ["MYSQL_HOST"],
    "user":            os.environ["MYSQL_USER"],
    "password":        os.environ["MYSQL_PASSWORD"],
    "connect_timeout": 5,    # no cuelga al conectar
    "connection_timeout": 5,
    "read_timeout":    60,   # falla rápido si MySQL muere mid-query (no bloquea el worker)
    "write_timeout":   30,
}

def get_connection(db_name):
    conn = mysql.connector.connect(database=db_name, **DB_CONFIG)
    cursor = conn.cursor(buffered=True)
    return conn, cursor

def check_connection(conn, cursor, db_name):
    """Verifica con ping. Si la conexión está muerta, cierra la vieja y abre nueva."""
    try:
        conn.ping(reconnect=False, attempts=1, delay=0)
        # ping OK pero cursor puede quedar inválido tras reconexión previa
        cursor = conn.cursor(buffered=True)
    except Exception:
        print(f"Reconectando a '{db_name}'...")
        try:
            conn.close()
        except Exception:
            pass
        conn, cursor = get_connection(db_name)
    return conn, cursor

# Conexiones iniciales (al iniciar la app)
conn, cursor = get_connection("comun")
conn_almacenes, cursor_almacenes = get_connection("almacenes")
