from functools import wraps
from flask import session, redirect, url_for, make_response, jsonify, request

# Prefijos que corresponden a personal de almacenes (N, N0, N1, M, M0, M1…)
_PREFIJOS_ALMACENES = ('N', 'M')
_PREFIJOS_ADMIN     = ('A', 'J')


def _tipo():
    return (session.get('tipo') or '').strip().upper()

def _es_almacenes(t):
    return t.startswith(_PREFIJOS_ALMACENES)

def _es_admin(t):
    return t.startswith(_PREFIJOS_ADMIN)

def puede_almacenes():
    t = _tipo()
    return _es_almacenes(t) or _es_admin(t)

def puede_pim():
    return not _es_almacenes(_tipo())


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
