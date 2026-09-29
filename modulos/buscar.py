# modulos/buscar.py
#
# Búsquedas compartidas por las pestañas de almacenes (reemplazan Page8 "Tablas"
# y Page9 "Materiales" del FoxPro, que se usaban como selectores).
# Algoritmo FoxPro: cada palabra tipeada es un LIKE '%palabra%' unido con AND.
#
from flask import Blueprint, jsonify, request
from conexiones import conn_almacenes, cursor_almacenes, check_connection
from modulos.utils import almacenes_requerido as login_requerido

buscar_bp = Blueprint('buscar', __name__)


def _where_palabras(campo, texto):
    palabras = [p for p in (texto or '').upper().split() if p]
    if not palabras:
        return '', []
    return ' AND '.join(f'{campo} LIKE %s' for _ in palabras), [f'%{p}%' for p in palabras]


# Personal (FoxPro pagina 601/1002/1003 → comun.personal)
@buscar_bp.route('/almacenes/buscar/personal')
@login_requerido
def personal():
    global conn_almacenes, cursor_almacenes
    conn_almacenes, cursor_almacenes = check_connection(conn_almacenes, cursor_almacenes, 'almacenes')

    q = (request.args.get('q') or '').strip()
    if q.isdigit():
        cursor_almacenes.execute(
            "SELECT idlegajo, nombre FROM comun.personal WHERE idlegajo = %s", (int(q),))
    else:
        where, params = _where_palabras('nombre', q)
        if not where:
            return jsonify({'results': []})
        cursor_almacenes.execute(
            f"SELECT idlegajo, nombre FROM comun.personal WHERE {where} ORDER BY nombre LIMIT 30",
            params)
    return jsonify({'results': [{'id': r[0], 'text': f'{r[0]} — {(r[1] or "").strip()}'}
                                for r in cursor_almacenes.fetchall()]})


# Materiales (FoxPro Page9 → vmateriales, por palabras en la descripción o por código)
@buscar_bp.route('/almacenes/buscar/materiales')
@login_requerido
def materiales():
    global conn_almacenes, cursor_almacenes
    conn_almacenes, cursor_almacenes = check_connection(conn_almacenes, cursor_almacenes, 'almacenes')

    q = (request.args.get('q') or '').strip().upper()
    partes = q.replace('-', ' ').split()
    if len(partes) == 2 and len(partes[0]) <= 3 and partes[1].isdigit():
        # código "AL 0024" o "AL-0024"
        cursor_almacenes.execute("""
            SELECT cd1, cd2, material, unidad FROM almacenes.materiales
            WHERE cd1 = %s AND cd2 = %s AND baja <> 1
        """, (partes[0], partes[1].zfill(4)))
    else:
        where, params = _where_palabras('material', q)
        if not where:
            return jsonify({'results': []})
        cursor_almacenes.execute(f"""
            SELECT cd1, cd2, material, unidad FROM almacenes.materiales
            WHERE {where} AND baja <> 1
            ORDER BY cd1, cd2 LIMIT 50
        """, params)
    return jsonify({'results': [
        {'id': f'{r[0]}-{r[1]}', 'cd1': r[0], 'cd2': r[1], 'material': r[2], 'unidad': r[3],
         'text': f'{r[0]}-{r[1]}  {r[2]}'}
        for r in cursor_almacenes.fetchall()]})


# Proveedores (FoxPro pagina 201/1202 → almacenes.proveedores)
@buscar_bp.route('/almacenes/buscar/proveedores')
@login_requerido
def proveedores():
    global conn_almacenes, cursor_almacenes
    conn_almacenes, cursor_almacenes = check_connection(conn_almacenes, cursor_almacenes, 'almacenes')

    q = (request.args.get('q') or '').strip()
    if q.isdigit():
        cursor_almacenes.execute(
            "SELECT idproveedor, proveedor FROM almacenes.proveedores WHERE idproveedor = %s", (int(q),))
    else:
        where, params = _where_palabras('proveedor', q)
        if not where:
            return jsonify({'results': []})
        cursor_almacenes.execute(
            f"SELECT idproveedor, proveedor FROM almacenes.proveedores WHERE {where} "
            "ORDER BY proveedor LIMIT 30", params)
    return jsonify({'results': [{'id': r[0], 'text': f'{r[0]} — {(r[1] or "").strip()}'}
                                for r in cursor_almacenes.fetchall()]})
