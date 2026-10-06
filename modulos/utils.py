from functools import wraps
from flask import session, redirect, url_for, make_response, jsonify, request

from modulos import permisos


def _caps():
    """Capacidades del usuario logueado (ver modulos/permisos.py: réplica de la lógica del FoxPro)."""
    return permisos.capacidades(session.get('tipos'), session.get('tipo_id'))


def puede_almacenes():
    return _caps()['almacenes']


def puede_retiro():
    return _caps()['retiro']


def puede_pim():
    return _caps()['pim']


def login_requerido(f):
    @wraps(f)
    def decorada(*args, **kwargs):
        # sin 'tipos' = sesión anterior al modelo de permisos por tipo: vuelve a ingresar
        if 'usuario' not in session or 'id_sector' not in session or 'tipos' not in session:
            return redirect(url_for('login.login'))
        resp = make_response(f(*args, **kwargs))
        resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        resp.headers['Pragma'] = 'no-cache'
        resp.headers['Expires'] = '0'
        return resp
    return decorada


def pedidos_requerido(f):
    """Estado de Pedidos / Imprimir Vales: para quien hace pedidos de sector o trabaja en Almacenes
    (en el FoxPro son pantallas de esos programas; quien solo autoriza no las tiene)."""
    @wraps(f)
    def decorada(*args, **kwargs):
        if 'usuario' not in session or 'id_sector' not in session or 'tipos' not in session:
            return redirect(url_for('login.login'))
        c = _caps()
        if not (c['retiro'] or c['pim'] or c['almacenes']):
            if request.is_json or request.headers.get('X-Requested-With') or request.path.count('/') > 1:
                return jsonify({'ok': False, 'msg': 'Sin permiso'}), 403
            return redirect(url_for('menu_bp.menu_principal'))
        resp = make_response(f(*args, **kwargs))
        resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        resp.headers['Pragma'] = 'no-cache'
        resp.headers['Expires'] = '0'
        return resp
    return decorada


def almacenes_requerido(f):
    @wraps(f)
    def decorada(*args, **kwargs):
        if 'usuario' not in session or 'id_sector' not in session or 'tipos' not in session:
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


def respuesta_csv(cols, filas, nombre):
    """CSV para "A Excel": ';' + BOM y coma decimal, como lo abre Excel en español.
    cols: [(clave, título, tipo)] — tipo 'n' = numérico."""
    import csv, io
    from flask import Response
    buf = io.StringIO()
    buf.write('﻿')
    w = csv.writer(buf, delimiter=';')
    w.writerow([t for _, t, _ in cols])
    for f in filas:
        fila = []
        for k, _, ty in cols:
            v = f.get(k)
            if v is None:
                fila.append('')
            elif ty == 'n' and isinstance(v, (int, float)):
                fila.append(str(v).replace('.', ','))
            else:
                fila.append(v)
        w.writerow(fila)
    return Response(buf.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition': f'attachment; filename={nombre}'})
