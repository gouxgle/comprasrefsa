# conexiones.py
import os
import socket
import time
import mysql.connector
from mysql.connector import Error

DB_CONFIG = {
    "host":            os.environ["MYSQL_HOST"],
    "user":            os.environ["MYSQL_USER"],
    "password":        os.environ["MYSQL_PASSWORD"],
    "connect_timeout": 5,
    "connection_timeout": 5,
    "read_timeout":    10,   # antes 60 — un socket colgado (NAT/firewall) bloqueaba el worker 60s
    "write_timeout":   10,
    "use_pure":        True,  # necesario para poder setear TCP keepalive sobre el socket real
}

# Detectado en prod: firewall/NAT entre la VM y el MySQL compartido corta
# conexiones TCP inactivas en silencio (sin FIN/RST) mucho antes de que el
# keepalive default del kernel (7200s) llegue a mandar un probe. El próximo
# ping()/query sobre esa conexión "zombie" se cuelga hasta el read_timeout.
# Con keepalive corto y explícito por socket, el SO detecta la caída y la
# conexión falla rápido en vez de colgarse en silencio.
def _set_keepalive(conn):
    try:
        sock = conn._socket.sock
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE,  60)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 10)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT,   3)
    except Exception:
        pass  # best-effort — si cambia la implementación interna, no debe romper la conexión

def get_connection(db_name, retries=10, delay=3):
    """Conecta a MySQL con reintentos — tolera que MySQL esté ocupado al arrancar."""
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            conn = mysql.connector.connect(database=db_name, **DB_CONFIG)
            _set_keepalive(conn)
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
