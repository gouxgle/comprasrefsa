from flask import Blueprint, render_template, session
from modulos.utils import login_requerido

menu_bp = Blueprint('menu_bp', __name__)


@menu_bp.route('/menu_principal')
@login_requerido
def menu_principal():
    return render_template(
        'menu.html',
        usuario=session.get('usuario', ''),
        sector_nombre=session.get('sector_nombre', ''),
        tipooperario=session.get('tipooperario', ''),
    )
