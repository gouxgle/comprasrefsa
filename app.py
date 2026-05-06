import os
from flask import Flask, redirect, url_for
from modulos.login import login_bp
from modulos.menu import menu_bp
from modulos.pedidos import pedidos_bp
from modulos.retiro import retiro_bp
from modulos.estado import estado_bp
from modulos.imprimir import imprimir_bp
from modulos.almacenes import almacenes_bp

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'refsa_almacenes_2026')

# Sesión no permanente: expira al cerrar el navegador
app.config['SESSION_PERMANENT']         = False
app.config['SESSION_COOKIE_HTTPONLY']   = True
app.config['SESSION_COOKIE_SAMESITE']   = 'Lax'

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

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=8080)
