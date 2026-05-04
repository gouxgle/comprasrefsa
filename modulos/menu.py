from flask import Blueprint, render_template, session, make_response
from modulos.utils import login_requerido
from conexiones import conn, cursor

menu_bp = Blueprint('menu_bp', __name__)

@menu_bp.route('/menu_principal')
@login_requerido
def menu_principal():
    response = make_response(render_template('menu.html', usuario=session['usuario']))
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    return response
