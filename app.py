import os
import mysql.connector
from flask import Flask, redirect, url_for, request, jsonify, render_template, session
from modulos.login import login_bp
from modulos.menu import menu_bp
from modulos.pedidos import pedidos_bp
from modulos.retiro import retiro_bp
from modulos.estado import estado_bp
from modulos.imprimir import imprimir_bp
from modulos.almacenes import almacenes_bp
from modulos.ingreso import ingreso_bp
from modulos.devoluciones import devoluciones_bp
from modulos.movimientos import movimientos_bp
from modulos.buscar import buscar_bp
from modulos.informes import informes_bp
from modulos.autorizaciones import autorizaciones_bp, puede_autorizar

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'refsa_almacenes_2026')

# Sesión no permanente: expira al cerrar el navegador
app.config['SESSION_PERMANENT']         = False
app.config['SESSION_COOKIE_HTTPONLY']   = True
app.config['SESSION_COOKIE_SAMESITE']   = 'Lax'

@app.errorhandler(mysql.connector.Error)
def _error_base_datos(e):
    # Bloqueo o caída de MySQL: mensaje claro en vez de "Internal Server Error"
    app.logger.error(f"MySQL: {e}")
    if request.is_json or request.headers.get('X-Requested-With') or request.path.count('/') > 2:
        return jsonify({'ok': False, 'msg': 'La base de datos está ocupada. Intente de nuevo en unos segundos.'}), 503
    return render_template('error_bd.html'), 503


@app.teardown_request
def _cerrar_transacciones(exc):
    # Ver conexiones.cerrar_transacciones: evita que un worker ocioso bloquee MySQL
    import conexiones
    conexiones.cerrar_transacciones()


@app.route('/')
def index():
    return redirect(url_for('login.login'))

app.register_blueprint(login_bp)
app.register_blueprint(menu_bp)
app.register_blueprint(pedidos_bp)
app.register_blueprint(retiro_bp)
app.register_blueprint(estado_bp)
app.register_blueprint(imprimir_bp)
app.register_blueprint(almacenes_bp)
app.register_blueprint(ingreso_bp)
app.register_blueprint(devoluciones_bp)
app.register_blueprint(movimientos_bp)
app.register_blueprint(buscar_bp)
app.register_blueprint(informes_bp)
app.register_blueprint(autorizaciones_bp)


# Operarios que solo usan Autorizaciones (reemplaza el ejecutable independiente permisos.exe).
# Se configura en .env: SOLO_AUTORIZACIONES=703,704   (703 = QUIROS JULIO)
SOLO_AUTORIZACIONES = {x.strip() for x in os.environ.get('SOLO_AUTORIZACIONES', '703').split(',') if x.strip()}
_PERMITIDOS_SOLO_AUT = ('autorizaciones.', 'login.', 'static')


def solo_autorizaciones():
    return str(session.get('id', '')) in SOLO_AUTORIZACIONES


@app.before_request
def _restringir_solo_autorizaciones():
    # Bloqueo del lado del servidor: aunque escriba otra URL, solo accede a Autorizaciones
    if not solo_autorizaciones():
        return None
    endpoint = request.endpoint or ''
    if endpoint == 'static' or endpoint.startswith(_PERMITIDOS_SOLO_AUT):
        return None
    # navegación del navegador → redirige; llamadas de datos (fetch/POST) → 403
    # (fetch() manda Accept: */*; el navegador al navegar pide text/html explícitamente)
    if request.method == 'GET' and 'text/html' in request.headers.get('Accept', ''):
        return redirect(url_for('autorizaciones.panel'))
    return jsonify({'ok': False, 'msg': 'Su usuario solo tiene acceso a Autorizaciones'}), 403


@app.context_processor
def _permisos_menu():
    # "Autorizaciones" solo aparece para los tipos habilitados en permisos.exe
    return {'puede_autorizar': puede_autorizar() if 'id' in session else False,
            'solo_autorizaciones': solo_autorizaciones()}

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=8080)
