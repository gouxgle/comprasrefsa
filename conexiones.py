# conexiones.py
import os
import time
import mysql.connector
from mysql.connector import Error

DB_CONFIG = {
    "host":            os.environ["MYSQL_HOST"],
    "user":            os.environ["MYSQL_USER"],
    "password":        os.environ["MYSQL_PASSWORD"],
    "connect_timeout": 5,
    "connection_timeout": 5,
    "read_timeout":    60,
    "write_timeout":   30,
}

def get_connection(db_name, retries=10, delay=3):
    """Conecta a MySQL con reintentos — tolera que MySQL esté ocupado al arrancar."""
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            conn = mysql.connector.connect(database=db_name, **DB_CONFIG)
            cursor = conn.cursor(buffered=True)
            return conn, cursor
        except Exception as exc:
            last_exc = exc
            print(f"[conexiones] {db_name}: intento {attempt}/{retries} falló — {exc}")
            if attempt < retries:
                time.sleep(delay)
    raise last_exc

def check_connection(conn, cursor, db_name):
    """Verifica con ping. Si la conexión está muerta, cierra la vieja y abre nueva."""
    try:
        conn.ping(reconnect=False, attempts=1, delay=0)
        try:
            conn.rollback()  # limpia transacciones pendientes si request anterior crasheó sin rollback
        except Exception:
            pass
        cursor = conn.cursor(buffered=True)
    except Exception:
        print(f"Reconectando a '{db_name}'...")
        try:
            conn.close()
        except Exception:
            pass
        # Reintentos cortos: esto corre DENTRO de un request en vivo — no podemos
        # bloquear el worker 30s (10x3s) como en el arranque. Si falla rápido,
        # el request devuelve error en vez de trabar el único pool de workers.
        conn, cursor = get_connection(db_name, retries=2, delay=1)
    return conn, cursor

# Conexiones iniciales (al iniciar la app)
conn, cursor = get_connection("comun")
conn_almacenes, cursor_almacenes = get_connection("almacenes")
