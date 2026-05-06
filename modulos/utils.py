from functools import wraps
from flask import session, redirect, url_for, make_response, jsonify, request

# Tipos que corresponden a personal de almacenes
_TIPOS_ALMACENES = {'N', 'M'}
_TIPOS_ADMIN     = {'A', 'J'}


def _tipo():
    return (session.get('tipo') or '').strip().upper()

def puede_almacenes():
    return _tipo() in _TIPOS_ALMACENES or _tipo() in _TIPOS_ADMIN

def puede_pim():
    return _tipo() not in _TIPOS_ALMACENES


def login_requerido(f):
    @wraps(f)
    def decorada(*args, **kwargs):
        if 'usuario' not in session or 'id_sector' not in session:
            return redirect(url_for('login.login'))
        resp = make_response(f(*args, **kwargs))
        resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        resp.headers['Pragma'] = 'no-cache'
        resp.headers['Expires'] = '0'
        return resp
    return decorada


def almacenes_requerido(f):
    @wraps(f)
    def decorada(*args, **kwargs):
        if 'usuario' not in session or 'id_sector' not in session:
            return redirect(url_for('login.login'))
        if not puede_almacenes():
            if request.is_json or request.headers.get('X-Requested-With'):
                return jsonify({'ok': False, 'msg': 'Sin permiso'}), 403
            return redirect(url_for('menu_bp.menu_principal'))
        resp = make_response(f(*args, **kwargs))
        resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        resp.headers['Pragma'] = 'no-cache'
        resp.headers['Expires'] = '0'
        return resp
    return decorada
