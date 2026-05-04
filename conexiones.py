# conexiones.py
import os
import mysql.connector
from mysql.connector import Error, OperationalError

# Configuración común
DB_CONFIG = {
    "host":     os.environ["MYSQL_HOST"],
    "user":     os.environ["MYSQL_USER"],
    "password": os.environ["MYSQL_PASSWORD"],
}

def get_connection(db_name):
    """Crea una conexión nueva con la base especificada."""
    conn = mysql.connector.connect(database=db_name, **DB_CONFIG)
    cursor = conn.cursor(buffered=True)
    return conn, cursor

def check_connection(conn, cursor, db_name):
    """Verifica si la conexión está viva. Si no, la restablece."""
    try:
        cursor.execute("SELECT 1")
    except (mysql.connector.Error, OperationalError):
        print(f"🔄 Reconectando a base '{db_name}'...")
        conn, cursor = get_connection(db_name)
    return conn, cursor

# Conexiones iniciales (al iniciar la app)
conn, cursor = get_connection("comun")
conn_almacenes, cursor_almacenes = get_connection("almacenes")
