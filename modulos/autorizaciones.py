# modulos/autorizaciones.py
#
# Ingeniería inversa de permisos.exe (VFP9) — autorización de pedidos firmados en papel.
#
# Acceso (form "ingreso"): el operario debe tener en comun.asignaciones algún tipo de
#   TIPOS_HABILITADOS ("administradores y gerencias" + "interior"). Si tiene varios, elige
#   con cuál trabaja (form "tipousr"). De ese tipo salen:
#     dJftr = tiposoperarios.idjefatura   ·   dSubG = jefaturas.idsubgerencia de dJftr
#
# Pantalla (form "autorizacion"):
#   dJftr = 1 (gerencia)   → Pedidos / Retiros / Compras · autoriza con 4 (Gerencia)
#   otra jefatura          → Pedidos / Retiros, solo de su subgerencia · autoriza con 3
#   tipo J1 (secretaría)   → debe indicar el gerente (J0) que firmó: queda en autorizadopor
#
#   Pedidos : vpedidosvirtuales1 — estado 0 y autorización pendiente
#             gerencia: idautorizacion 1 ó 2 · resto: idautorizacion 1 y su subgerencia
#   Retiros : vretiromateriales — idestado < 2 (gerencia: todos · resto: su subgerencia)
#   Compras : vpedidosreales1 — idautorizacion < 2 (solo gerencia)
#
#   Autorizar:
#     P.I.M.  → pedidosvirtuales.autorizadopor/autorizacion + detalles.autorizacion
#               (+ autorizadoproyectoespecial y proyectosespeciales.autorizacion si es P.E.)
#     Retiro  → detallesretiromateriales.autorizacion
#     Compra  → pedidosreales: estado 21, autorizadopor, autorizacion, fechaautorizado
#
# Diferencia deliberada: el FoxPro guardaba en autorizadopor el NOMBRE del gerente del combo
# (BoundColumn = 2) sobre un campo numérico → quedaba 0. Acá se guarda su IdOperario.
#
from functools import wraps
from flask import Blueprint, render_template, session, jsonify, request, redirect, url_for, make_response
from conexiones import conn_almacenes, cursor_almacenes, check_connection
from datetime import date
from decimal import Decimal

autorizaciones_bp = Blueprint('autorizaciones', __name__)

# permisos.exe → Command1.Click del form "ingreso"
TIPOS_HABILITADOS = ('A', 'A0', 'J0', 'J1', 'D0', 'C0',
                     'I0', 'I1', 'I2', 'I3', 'I4', 'I5', 'I6', 'I7')
GERENCIA   = 1          # idjefatura de gerencia
SECRETARIA = 'J1'       # autoriza en nombre de un gerente (J0)
LIMITE     = 3000

AUT_GERENCIA, AUT_SUBGERENCIA = 4, 3


def _s(v):
    if isinstance(v, date):    return v.strftime('%d/%m/%Y')
    if isinstance(v, Decimal): return float(v)
    if isinstance(v, str):     return v.strip()
    return v


def _rows():
    cols = [x[0] for x in cursor_almacenes.description]
    return [{c: _s(v) for c, v in zip(cols, r)} for r in cursor_almacenes.fetchall()]


def _conectar():
    global conn_almacenes, cursor_almacenes
    conn_almacenes, cursor_almacenes = check_connection(conn_almacenes, cursor_almacenes, 'almacenes')


# ── Permisos ──────────────────────────────────────────────────────────────────

def tipos_autorizacion():
    """Tipos de operario habilitados para autorizar del usuario logueado.
    Se calcula una vez por sesión (login hace session.clear())."""
    if 'id' not in session:
        return []
    cache = session.get('aut_tipos')
    if cache is not None and session.get('aut_tipos_id') == session['id']:
        return cache
    _conectar()
    marcas = ','.join(['%s'] * len(TIPOS_HABILITADOS))
    cursor_almacenes.execute(f"""
        SELECT a.idtipooperario, t.tipooperario, t.idjefatura, j.jefatura, j.idsubgerencia
        FROM comun.asignaciones a
        JOIN comun.tiposoperarios t ON t.idtipooperario = a.idtipooperario
        LEFT JOIN comun.jefaturas j ON j.idjefatura = t.idjefatura
        WHERE a.idoperario = %s AND a.idtipooperario IN ({marcas})
        ORDER BY t.idjefatura <> 1, a.idtipooperario     -- gerencia primero (tipo propuesto)
    """, (session['id'], *TIPOS_HABILITADOS))
    tipos = [{'id': r[0].strip(), 'nombre': (r[1] or '').strip(), 'jefatura': r[2],
              'jefatura_nombre': (r[3] or '').strip(), 'subgerencia': r[4]}
             for r in cursor_almacenes.fetchall()]
    session['aut_tipos'] = tipos
    session['aut_tipos_id'] = session['id']
    return tipos


def puede_autorizar():
    try:
        return bool(tipos_autorizacion())
    except Exception:
        return False


def _tipo_activo():
    """Tipo con el que trabaja (form "tipousr" si tiene más de uno)."""
    tipos = tipos_autorizacion()
    elegido = session.get('aut_tipo')
    for t in tipos:
        if t['id'] == elegido:
            return t
    return tipos[0] if tipos else None


def autorizador_requerido(f):
    @wraps(f)
    def decorada(*args, **kwargs):
        if 'usuario' not in session or 'id_sector' not in session:
            return redirect(url_for('login.login'))
        if not puede_autorizar():
            if request.method != 'GET' or request.path.count('/') > 1:
                return jsonify({'ok': False, 'msg': 'No está habilitado para autorizar'}), 403
            return redirect(url_for('menu_bp.menu_principal'))
        resp = make_response(f(*args, **kwargs))
        resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        return resp
    return decorada


def _contexto():
    t = _tipo_activo()
    gerencia = t['jefatura'] == GERENCIA
    return {
        'tipo': t,
        'gerencia': gerencia,
        'nivel': AUT_GERENCIA if gerencia else AUT_SUBGERENCIA,
        'subgerencia': t['subgerencia'],
        'secretaria': t['id'] == SECRETARIA,
    }


# ── Pantalla ──────────────────────────────────────────────────────────────────

@autorizaciones_bp.route('/autorizaciones')
@autorizador_requerido
def panel():
    _conectar()
    ctx = _contexto()
    gerentes = []
    if ctx['secretaria']:
        # FoxPro: select * from comun.voperarios where idtipooperario = 'J0'
        cursor_almacenes.execute(
            "SELECT idoperario, descoperario FROM comun.voperarios "
            "WHERE idtipooperario = 'J0' ORDER BY descoperario")
        gerentes = [{'id': r[0], 'nombre': (r[1] or '').strip()} for r in cursor_almacenes.fetchall()]
    return render_template('autorizaciones.html',
                           usuario=session.get('usuario', ''),
                           sector_nombre=session.get('sector_nombre', ''),
                           ctx=ctx, tipos=tipos_autorizacion(), gerentes=gerentes)


@autorizaciones_bp.route('/autorizaciones/tipo', methods=['POST'])
@autorizador_requerido
def cambiar_tipo():
    tipo = (request.get_json() or {}).get('tipo')
    if not any(t['id'] == tipo for t in tipos_autorizacion()):
        return jsonify({'ok': False, 'msg': 'Tipo no habilitado'}), 400
    session['aut_tipo'] = tipo
    return jsonify({'ok': True})


# ── Listas (Check1.Click con Detalles destildado) ────────────────────────────

def _sql_lista(clase, ctx):
    if clase == 'pim':
        if ctx['gerencia']:
            where, params = "idautorizacion IN (1, 2) AND estado = 0", []
        else:
            where, params = "idautorizacion = 1 AND estado = 0 AND idsubgerencia = %s", [ctx['subgerencia']]
        return (f"SELECT idpedidovirtual, jefatura, idproyectoespecial, proyectoespecial, comentarios, "
                f"fecha, fechaentrega, autorizacion FROM almacenes.vpedidosvirtuales1 WHERE {where} "
                f"ORDER BY idpedidovirtual DESC", params)      # más recientes primero
    if clase == 'retiro':
        # = vretiromateriales WHERE idestado < 2 (la vista entera tarda 5 s: se consulta directo)
        sql = ("SELECT r.idretiro, j.jefatura AS sector, e.estado, r.fechapedido, o.DescOperario "
               "FROM almacenes.retiromateriales r "
               "LEFT JOIN comun.jefaturas j ON j.idjefatura = r.sector "
               "LEFT JOIN comun.operarios o ON o.IdOperario = r.quienpidio "
               "JOIN almacenes.estadospedidos e ON e.idestado = r.estado "
               "WHERE r.estado < 2")
        if ctx['gerencia']:
            return sql + " ORDER BY r.sector, r.idretiro", []
        return sql + " AND j.idsubgerencia = %s ORDER BY r.idretiro", [ctx['subgerencia']]
    if clase == 'compra' and ctx['gerencia']:
        return ("SELECT idpedidoreal, fecha, proveedor, total, moneda, condicion, autorizacion "
                "FROM almacenes.vpedidosreales1 WHERE idautorizacion < 2 "
                "ORDER BY idpedidoreal DESC", [])
    return None, None


@autorizaciones_bp.route('/autorizaciones/lista/<clase>')
@autorizador_requerido
def lista(clase):
    _conectar()
    sql, params = _sql_lista(clase, _contexto())
    if sql is None:
        return jsonify({'ok': False, 'msg': 'Opción no habilitada para su usuario'}), 403
    cursor_almacenes.execute(sql + f" LIMIT {LIMITE + 1}", params)
    filas = _rows()
    return jsonify({'ok': True, 'filas': filas[:LIMITE], 'truncado': len(filas) > LIMITE})


# ── Detalle (Check1.Click con Detalles tildado) ──────────────────────────────

_SQL_DETALLE = {
    'pim': "SELECT renglon, material, cantidad, autorizacion FROM almacenes.vdetallespedidosvirtuales "
           "WHERE idpedidovirtual = %s ORDER BY renglon",
    'retiro': "SELECT renglon, material, cantidadpedida, autorizacion, stock, stockalmacen "
              "FROM almacenes.vdetallesretiromateriales WHERE idretiro = %s ORDER BY renglon",
    'compra': "SELECT renglon, material, cantidad, preciounitario, descuento1, importe "
              "FROM almacenes.vdetallespedidosreales WHERE idpedidoreal = %s ORDER BY renglon",
}


@autorizaciones_bp.route('/autorizaciones/detalle/<clase>/<int:id_pedido>')
@autorizador_requerido
def detalle(clase, id_pedido):
    _conectar()
    if clase not in _SQL_DETALLE or (clase == 'compra' and not _contexto()['gerencia']):
        return jsonify({'ok': False, 'msg': 'Opción no habilitada para su usuario'}), 403
    cursor_almacenes.execute(_SQL_DETALLE[clase], (id_pedido,))
    return jsonify({'ok': True, 'filas': _rows()})


# ── Autorizar (Command3.Click) ───────────────────────────────────────────────

def _pendiente(clase, id_pedido, ctx):
    """Revalida que el pedido siga en la lista del usuario (evita autorizar dos veces
    o fuera de su subgerencia manipulando la petición)."""
    sql, params = _sql_lista(clase, ctx)
    if sql is None:
        return None
    clave = {'pim': 'idpedidovirtual', 'retiro': 'r.idretiro', 'compra': 'idpedidoreal'}[clase]
    sql = sql.split(' ORDER BY ')[0] + f" AND {clave} = %s"
    cursor_almacenes.execute(sql, (*params, id_pedido))
    filas = _rows()
    return filas[0] if filas else None


@autorizaciones_bp.route('/autorizaciones/autorizar', methods=['POST'])
@autorizador_requerido
def autorizar():
    _conectar()
    d = request.get_json() or {}
    clase = d.get('clase')
    try:
        id_pedido = int(d.get('id'))
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'msg': 'Seleccione un pedido'}), 400
    ctx = _contexto()

    quien = int(session['id'])
    if ctx['secretaria']:
        # "el que autoriza es el que está en el combo, no el secretario"
        try:
            quien = int(d.get('gerente'))
        except (TypeError, ValueError):
            return jsonify({'ok': False, 'msg': 'Ingrese el gerente que autorizó el pedido'}), 400
        cursor_almacenes.execute(
            "SELECT 1 FROM comun.asignaciones WHERE idoperario = %s AND idtipooperario = 'J0'", (quien,))
        if not cursor_almacenes.fetchone():
            return jsonify({'ok': False, 'msg': 'El autorizante elegido no es gerente'}), 400

    ped = _pendiente(clase, id_pedido, ctx)
    if not ped:
        return jsonify({'ok': False, 'msg': 'El pedido ya no está pendiente de su autorización'}), 409
    nivel = ctx['nivel']

    try:
        if clase == 'pim':
            cursor_almacenes.execute(
                "UPDATE almacenes.pedidosvirtuales SET autorizadopor = %s, autorizacion = %s "
                "WHERE idpedidovirtual = %s", (quien, nivel, id_pedido))
            cursor_almacenes.execute(
                "UPDATE almacenes.detallespedidosvirtuales SET autorizacion = %s "
                "WHERE idpedidovirtual = %s", (nivel, id_pedido))
            if ped.get('proyectoespecial') == 1:
                cursor_almacenes.execute(
                    "UPDATE almacenes.pedidosvirtuales SET autorizadoproyectoespecial = %s "
                    "WHERE idpedidovirtual = %s", (nivel, id_pedido))
                if ped.get('idproyectoespecial'):
                    cursor_almacenes.execute(
                        "UPDATE almacenes.proyectosespeciales SET autorizacion = %s "
                        "WHERE idproyectoespecial = %s", (nivel, ped['idproyectoespecial']))
            texto = f'P.I.M. {id_pedido} autorizado'
        elif clase == 'retiro':
            cursor_almacenes.execute(
                "UPDATE almacenes.detallesretiromateriales SET autorizacion = %s WHERE idretiro = %s",
                (nivel, id_pedido))
            texto = f'Retiro {id_pedido} autorizado'
        else:
            cursor_almacenes.execute(
                "UPDATE almacenes.pedidosreales SET estado = 21, autorizadopor = %s, autorizacion = %s, "
                "fechaautorizado = CURDATE() WHERE idpedidoreal = %s", (quien, nivel, id_pedido))
            texto = f'O.C. {id_pedido} autorizada'
        conn_almacenes.commit()
        return jsonify({'ok': True, 'msg': texto})
    except Exception as e:
        conn_almacenes.rollback()
        return jsonify({'ok': False, 'msg': f'No se pudo autorizar: {e}'}), 500
