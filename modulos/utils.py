import mysql.connector
from functools import wraps
from flask import session, flash, redirect, url_for
import hashlib

conn = mysql.connector.connect(
    host="192.168.0.7",
    user="Usuario",
    password="Usuario580",
    database="comun"
)
cursor = conn.cursor()

def encriptar_clave(clave):
    return hashlib.sha256(clave.encode()).hexdigest()

def login_requerido(f):
    @wraps(f)
    def decorada(*args, **kwargs):
        if 'usuario' not in session or 'id_sector' not in session:
            flash('Debe iniciar sesión para continuar.', 'danger')
            return redirect(url_for('login.login'))
        return f(*args, **kwargs)
    return decorada

def manejar_errores_mysql(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except mysql.connector.errors.OperationalError as e:
            print(f"❌ Error de conexión MySQL: {e}")
            session.clear()
            return redirect(url_for('login.login'))
        except mysql.connector.errors.InterfaceError as e:
            print(f"❌ Error de interfaz MySQL: {e}")
            session.clear()
            return redirect(url_for('login.login'))
    return wrapper
