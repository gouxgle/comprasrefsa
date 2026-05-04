from flask import Flask, redirect, url_for
from modulos.login import login_bp
from modulos.menu import menu_bp
from modulos.pedidos import pedidos_bp
from modulos.retiro import retiro_bp
from modulos.estado import estado_bp 
from modulos.imprimir import imprimir_bp


app = Flask(__name__)
app.secret_key = 'clave_secreta'

@app.route('/')
def index():
    return redirect(url_for('login.login'))

# Registrar Blueprints
app.register_blueprint(login_bp)
app.register_blueprint(menu_bp)
app.register_blueprint(pedidos_bp)
app.register_blueprint(retiro_bp)
app.register_blueprint(estado_bp, url_prefix='/estado_pedido')
app.register_blueprint(imprimir_bp)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=8080)
