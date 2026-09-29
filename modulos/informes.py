# modulos/informes.py
#
# Ingeniería inversa de almacenes3.exe (VFP9) — pestaña "Informes" (Page12).
#
# "Ver" (Command6): informe según Optiongroup1
#   P.I.M.        → vdetallespedidosvirtuales4
#   Retiro        → vdetallesretiromateriales2 (se consulta sin la vista: tarda 8 s)
#   Transferencia → vdetallestransfermateriales1
#   Stock         → vmaterialesdesectores2; con material: vrestaretirar (R.M. faltan
#                   retirar) o vrestaentregar (O.C. faltan ingresar). Siempre por sector.
# Filtros (checks): Distrito (check7, tiene prioridad) o Sector (check6) · Proveedor /
#   Empleado (check1) · Material (check2) · Destino (check3, solo transferencias) ·
#   Entre fechas (check4) · Estado (check5 + P.I.M./O.C.)
# "Dar de Baja" (Command8): P.I.M. → estado 9 · Retiro → estado 39
# Medidores (Command10/11/13/12): retiros de medidores, buscar un N° de serie,
#   buscar en rango y ver los medidores de un retiro (tabla retiromedidores)
# "A Excel" (Command7): CSV con todas las columnas del informe
#
# Ramas del FoxPro que fallaban y acá se resuelven con la columna real:
#   estado → la vista no tiene "estadopim" (se usa estado) · Dar de baja P.I.M. leía
#   idestado (no existe en la vista) · destino/distrito en transferencias (no existen:
#   se usa sectorhasta y la cabecera del sector)
#
from flask import Blueprint, render_template, session, jsonify, request
from conexiones import conn_almacenes, cursor_almacenes, check_connection
from modulos.utils import almacenes_requerido as login_requerido, respuesta_csv
from datetime import date, datetime, timedelta
from decimal import Decimal

informes_bp = Blueprint('informes', __name__)

LIMITE       = 3000
LIMITE_EXCEL = 50000


def _s(v):
    if isinstance(v, date):    return v.strftime('%d/%m/%Y')
    if isinstance(v, Decimal): return float(v)
    return v


def _fecha(txt):
    try:
        return datetime.strptime(txt, '%Y-%m-%d').date() if txt else None
    except ValueError:
        return None


def _entero(txt):
    return int(txt) if (txt or '').strip().isdigit() else None


# Columnas visibles (las mismas 9 del FoxPro + estado). El Excel lleva todas.
COLS_DETALLE = [('id', 'ID', 'c'), ('renglon', 'Renglón', 'c'), ('sector', 'Sector', 'c'),
                ('fecha', 'Fecha', 'c'), ('cd1', 'CD1', 'c'), ('cd2', 'CD2', 'c'),
                ('material', 'Material', 't'), ('cantidad', 'Cantidad', 'n'),
                ('unidad', 'Unidad', 'c')]

_DEVUELTO = ("(SELECT SUM(dv.cantdevuelta) FROM almacenes.devoluciones dv "
             "WHERE dv.retiro = d.idretiro AND dv.renglon = d.renglon)")

MODOS = {
    'pim': {
        'sql': "SELECT * FROM almacenes.vdetallespedidosvirtuales4",
        'sector': 'sector', 'cabecera': 'cabecera', 'cd': ('cd1', 'cd2'),
        'fecha': 'fecha', 'fecha_oc': 'fechaoc', 'persona': 'idproveedor',
        'estado': 'estado', 'estado_oc': 'estadooc', 'destino': None,
        'orden': 'id DESC, renglon',
        'cols': COLS_DETALLE + [('destadospim', 'Estado P.I.M.', 't'),
                                ('ordendecompra', 'O.C.', 'c'), ('destadooc', 'Estado O.C.', 't'),
                                ('proveedor', 'Proveedor', 't')],
    },
    'retiro': {     # = vdetallesretiromateriales2 (misma lista de columnas)
        'sql': f"""SELECT STRAIGHT_JOIN d.idretiro AS id, d.renglon, r.sector,
                          d.fechaentregado AS fecha, d.cd1, d.cd2, m.material,
                          d.cantidadpedida AS cantidad, m.unidad, d.cantidadretirada,
                          d.quienentrego, d.autorizacion, d.dealmacen,
                          d.estado AS idestado, e.estado,
                          IFNULL({_DEVUELTO}, 0) AS devuelto,
                          d.cantidadretirada - IFNULL({_DEVUELTO}, 0) AS totalsacado,
                          r.quienretiro, p.nombre, r.destino, r.ubicacion,
                          j.jefatura, ms.baja, j.cabecera
                   FROM almacenes.detallesretiromateriales d
                   LEFT JOIN almacenes.retiromateriales r ON r.idretiro = d.idretiro
                   LEFT JOIN almacenes.materiales m ON m.cd1 = d.cd1 AND m.cd2 = d.cd2
                   LEFT JOIN almacenes.estadospedidos e ON e.idestado = d.estado
                   LEFT JOIN comun.personal p ON p.idlegajo = r.quienretiro
                   LEFT JOIN comun.jefaturas j ON j.idjefatura = r.destino
                   JOIN almacenes.materialesdesectores ms
                        ON ms.sector = r.sector AND ms.cd1 = d.cd1 AND ms.cd2 = d.cd2 AND ms.baja = 0""",
        'sector': 'r.sector', 'cabecera': 'j.cabecera', 'cd': ('d.cd1', 'd.cd2'),
        'fecha': 'd.fechaentregado', 'fecha_oc': None, 'persona': 'r.quienretiro',
        'estado': 'd.estado', 'estado_oc': None, 'destino': None,
        'orden': 'd.idretiro DESC, d.renglon',
        'cols': COLS_DETALLE + [('cantidadretirada', 'Retirado', 'n'), ('estado', 'Estado', 't'),
                                ('nombre', 'Quién retiró', 't')],
    },
    'transfer': {
        'sql': "SELECT * FROM almacenes.vdetallestransfermateriales1",
        'sector': 'sector',
        'cabecera': 'sector IN (SELECT idjefatura FROM comun.jefaturas WHERE cabecera = %s)',
        'cd': ('cd1', 'cd2'), 'fecha': 'fecha', 'fecha_oc': None, 'persona': None,
        'estado': None, 'estado_oc': None, 'destino': 'sectorhasta',
        'orden': 'id DESC, renglon',
        'cols': COLS_DETALLE + [('sectororg', 'Desde', 't'), ('sectordest', 'Hacia', 't')],
    },
}

COLS_STOCK = [('sector', 'Sector', 'c'), ('jefatura', 'Sector', 't'), ('cd1', 'CD1', 'c'),
              ('cd2', 'CD2', 'c'), ('material', 'Material', 't'), ('unidad', 'Unidad', 'c'),
              ('minimo', 'Stk.Seg.', 'n'), ('medio', 'Pto.Ped.', 'n'), ('maximo', 'Máximo', 'n'),
              ('stock', 'Stock', 'n'), ('prioridad', 'Prior', 'c'), ('demanda', 'Demanda', 'n'),
              ('restaretirar', 'Restan Retirar', 'n'), ('cantpedret', 'Cant. R.M.', 'n'),
              ('faltaningresar', 'Faltan Ingresar', 'n'), ('cantoc', 'Cant. O.C.', 'n')]
COLS_RESTA_RETIRAR = [('sector', 'Sector', 'c'), ('cd1', 'CD1', 'c'), ('cd2', 'CD2', 'c'),
                      ('material', 'Material', 't'), ('stock', 'Stock', 'n'),
                      ('idretiro', 'Retiro', 'c'), ('renglon', 'Renglón', 'c'),
                      ('restaretirar', 'Resta retirar', 'n')]
COLS_RESTA_ENTREGAR = [('sector', 'Sector', 'c'), ('cd1', 'CD1', 'c'), ('cd2', 'CD2', 'c'),
                       ('material', 'Material', 't'), ('idpedidoreal', 'O.C.', 'c'),
                       ('renglon', 'Renglón', 'c'), ('proveedor', 'Proveedor', 't'),
                       ('fecha', 'Fecha O.C.', 'c'), ('cantidad', 'Cantidad', 'n'),
                       ('cantidadingresada', 'Ingresado', 'n'), ('faltaningresar', 'Faltan', 'n'),
                       ('pim', 'P.I.M.', 'c'), ('renglonpim', 'Ítem', 'c')]

COLS_MEDIDORES = [('idretiro', 'ID', 'c'), ('renglon', 'Renglón', 'c'), ('sector', 'Sector', 'c'),
                  ('fechapedido', 'Fecha', 'c'), ('cd1', 'CD1', 'c'), ('cd2', 'CD2', 'c'),
                  ('material', 'Material', 't'), ('cantidadpedida', 'Pedidos', 'n'),
                  ('cantidadretirada', 'Retirados', 'n')]
COLS_RANGO = [('idretiro', 'ID', 'c'), ('renglon', 'Renglón', 'c'), ('item', 'Ítem', 'c'),
              ('sector', 'Sector', 'c'), ('cd1', 'CD1', 'c'), ('cd2', 'CD2', 'c'),
              ('material', 'Material', 't'), ('cantidadpedida', 'Pedidos', 'n'),
              ('cantidadretirada', 'Retirados', 'n'), ('medidor', 'Medidores', 't'),
              ('medi', 'Medidor', 't'), ('fechapedido', 'Fecha', 'c')]
COLS_MED_RETIRO = [('idretiro', 'ID', 'c'), ('renglonretiro', 'Renglón', 'c'),
                   ('item', 'Ítem', 'c'), ('medidor', 'Medidor', 't')]


# ── Armado de consultas ───────────────────────────────────────────────────────

def _consulta_informe(a):
    """Devuelve (sql, params, cols) o lanza ValueError con el mensaje para el usuario."""
    modo    = a.get('modo', 'pim')
    sector  = _entero(a.get('sector'))
    cd1, cd2 = a.get('cd1'), a.get('cd2')

    if modo == 'stock':
        if sector is None:
            raise ValueError('Seleccione un sector')
        if cd1 and cd2:
            if a.get('stockop') == 'oc':
                sql, cols = "SELECT * FROM almacenes.vrestaentregar", COLS_RESTA_ENTREGAR
            else:
                sql, cols = "SELECT * FROM almacenes.vrestaretirar", COLS_RESTA_RETIRAR
            return (sql + " WHERE cd1 = %s AND cd2 = %s AND sector = %s ORDER BY cd1, cd2",
                    [cd1, cd2, sector], cols)
        return ("SELECT * FROM almacenes.vmaterialesdesectores2 WHERE sector = %s ORDER BY cd1, cd2",
                [sector], COLS_STOCK)

    if modo not in MODOS:
        raise ValueError('Informe inválido')
    m = MODOS[modo]
    conds, params = [], []

    distrito = _entero(a.get('distrito'))
    if distrito is not None:                         # Distrito tiene prioridad sobre Sector
        conds.append(m['cabecera'] if '%s' in m['cabecera'] else f"{m['cabecera']} = %s")
        params.append(distrito)
    elif sector is not None:
        conds.append(f"{m['sector']} = %s"); params.append(sector)

    if cd1 and cd2:
        conds.append(f"{m['cd'][0]} = %s AND {m['cd'][1]} = %s"); params += [cd1, cd2]

    destino = _entero(a.get('destino'))
    if destino is not None and m['destino']:
        conds.append(f"{m['destino']} = %s"); params.append(destino)

    por_oc = a.get('po') == 'oc'
    estado = _entero(a.get('estado'))
    if estado is not None and m['estado']:
        col = m['estado_oc'] if por_oc and m['estado_oc'] else m['estado']
        conds.append(f"{col} = %s"); params.append(estado)

    desde, hasta = _fecha(a.get('desde')), _fecha(a.get('hasta'))
    if desde and hasta:                              # FoxPro: solo entre dos fechas
        col = m['fecha_oc'] if por_oc and m['fecha_oc'] else m['fecha']
        conds.append(f"{col} BETWEEN %s AND %s"); params += [desde, hasta]

    persona = _entero(a.get('persona'))
    if persona is not None and m['persona']:
        conds.append(f"{m['persona']} = %s"); params.append(persona)

    sql = m['sql'] + (' WHERE ' + ' AND '.join(conds) if conds else '') + f" ORDER BY {m['orden']}"
    return sql, params, m['cols']


def _consulta_medidores(a):
    """Command10 "Medidores": vretiromedidores (solo cd1 = 'MED')."""
    conds, params = [], []
    sector = _entero(a.get('sector'))
    if sector is not None:
        conds.append('sector = %s'); params.append(sector)
    if a.get('cd1') and a.get('cd2'):
        conds.append('cd1 = %s AND cd2 = %s'); params += [a['cd1'], a['cd2']]
    desde, hasta = _fecha(a.get('desde')), _fecha(a.get('hasta'))
    if desde and hasta:
        conds.append('fechapedido BETWEEN %s AND %s'); params += [desde, hasta]
    persona = _entero(a.get('persona'))
    if persona is not None:
        conds.append('quienpidio = %s'); params.append(persona)   # FoxPro filtra por quien pidió
    sql = "SELECT * FROM almacenes.vretiromedidores"
    if conds:
        sql += ' WHERE ' + ' AND '.join(conds)
    return sql + ' ORDER BY idretiro DESC, renglon', params, COLS_MEDIDORES


# Command13 "Buscar en rango" = vretiromedidores2 WHERE medi BETWEEN … (la vista tarda 16 s:
# se filtra primero retiromedidores y después se une)
_MEDI = "IF(LOCATE('/', rm.medidor) > 1, LEFT(rm.medidor, LOCATE('/', rm.medidor) - 2), rm.medidor)"
_SQL_RANGO = f"""
    SELECT x.idretiro, x.renglon, x.item, x.medidor, x.medi,
           d.cd1, d.cd2, m.material, d.cantidadpedida, d.cantidadretirada,
           r.sector, r.fechapedido, r.quienpidio, r.quienretiro, p.nombre
    FROM (SELECT rm.idretiro, rm.renglonretiro AS renglon, rm.item, rm.medidor, {_MEDI} AS medi
          FROM almacenes.retiromedidores rm
          WHERE {_MEDI} >= %s AND {_MEDI} <= %s) x
    LEFT JOIN almacenes.detallesretiromateriales d ON d.idretiro = x.idretiro AND d.renglon = x.renglon
    LEFT JOIN almacenes.retiromateriales r ON r.idretiro = d.idretiro
    LEFT JOIN almacenes.materiales m ON m.cd1 = d.cd1 AND m.cd2 = d.cd2
    LEFT JOIN comun.personal p ON p.idlegajo = r.quienretiro
    ORDER BY x.idretiro DESC, x.renglon, x.item
"""


def _ejecutar(sql, params, limite):
    cursor_almacenes.execute(sql + f" LIMIT {limite + 1}", params)
    cols = [x[0] for x in cursor_almacenes.description]
    filas = [{c: _s(v) for c, v in zip(cols, r)} for r in cursor_almacenes.fetchall()]
    return filas[:limite], len(filas) > limite, cols


def _responder(sql, params, cols, nombre):
    """JSON para la grilla, o CSV con TODAS las columnas si ?formato=csv (FoxPro: COPY TO … XL5)."""
    if request.args.get('formato') == 'csv':
        filas, _, todas = _ejecutar(sql, params, LIMITE_EXCEL)
        tipos = {k: ty for k, _, ty in cols}
        return respuesta_csv([(c, c, tipos.get(c, 'n' if filas and isinstance(filas[0].get(c), float) else 't'))
                              for c in todas], filas, f'{nombre}_{date.today():%Y%m%d}.csv')
    filas, truncado, _ = _ejecutar(sql, params, LIMITE)
    return jsonify({'ok': True, 'filas': filas, 'truncado': truncado,
                    'cols': [{'key': k, 'titulo': t, 'tipo': ty} for k, t, ty in cols]})


# ── Rutas ─────────────────────────────────────────────────────────────────────

@informes_bp.route('/almacenes/informes')
@login_requerido
def panel():
    global conn_almacenes, cursor_almacenes
    conn_almacenes, cursor_almacenes = check_connection(conn_almacenes, cursor_almacenes, 'almacenes')

    cursor_almacenes.execute("SELECT idjefatura, jefatura FROM comun.jefaturas ORDER BY jefatura")
    sectores = [{'id': r[0], 'nombre': (r[1] or '').strip()} for r in cursor_almacenes.fetchall()]
    cursor_almacenes.execute("SELECT idcabecera, cabecera FROM comun.cabeceras ORDER BY idcabecera")
    distritos = [{'id': r[0], 'nombre': (r[1] or '').strip()} for r in cursor_almacenes.fetchall()]
    cursor_almacenes.execute(
        "SELECT idestado, estado FROM almacenes.estadospedidos WHERE estado <> '' ORDER BY idestado")
    estados = [{'id': r[0], 'nombre': r[1]} for r in cursor_almacenes.fetchall()]

    hoy = date.today()
    return render_template('informes.html',
                           usuario=session.get('usuario', ''),
                           sector_nombre=session.get('sector_nombre', ''),
                           sectores=sectores, distritos=distritos, estados=estados,
                           hoy=hoy.isoformat(),
                           hace_un_anio=(hoy - timedelta(days=365)).isoformat())


@informes_bp.route('/almacenes/informes/ver')
@login_requerido
def ver():
    global conn_almacenes, cursor_almacenes
    conn_almacenes, cursor_almacenes = check_connection(conn_almacenes, cursor_almacenes, 'almacenes')
    try:
        sql, params, cols = _consulta_informe(request.args)
    except ValueError as e:
        return jsonify({'ok': False, 'msg': str(e)}), 400
    return _responder(sql, params, cols, f"informe_{request.args.get('modo', 'pim')}")


@informes_bp.route('/almacenes/informes/medidores')
@login_requerido
def medidores():
    global conn_almacenes, cursor_almacenes
    conn_almacenes, cursor_almacenes = check_connection(conn_almacenes, cursor_almacenes, 'almacenes')
    sql, params, cols = _consulta_medidores(request.args)
    return _responder(sql, params, cols, 'medidores')


# Command11 "Buscar un medidor": N° de serie exacto → el retiro que lo llevó
@informes_bp.route('/almacenes/informes/medidor')
@login_requerido
def buscar_medidor():
    global conn_almacenes, cursor_almacenes
    conn_almacenes, cursor_almacenes = check_connection(conn_almacenes, cursor_almacenes, 'almacenes')
    serie = (request.args.get('serie') or '').strip()
    if not serie:
        return jsonify({'ok': False, 'msg': 'Ingrese el número de medidor'}), 400
    cursor_almacenes.execute(
        "SELECT idretiro FROM almacenes.retiromedidores WHERE medidor = %s LIMIT 1", (serie,))
    row = cursor_almacenes.fetchone()
    if not row:
        return jsonify({'ok': False, 'msg': f'El medidor {serie} no figura en ningún retiro'}), 404
    return _responder("SELECT * FROM almacenes.vretiromedidores WHERE idretiro = %s ORDER BY renglon",
                      [row[0]], COLS_MEDIDORES, 'medidor')


@informes_bp.route('/almacenes/informes/medidores_rango')
@login_requerido
def medidores_rango():
    global conn_almacenes, cursor_almacenes
    conn_almacenes, cursor_almacenes = check_connection(conn_almacenes, cursor_almacenes, 'almacenes')
    desde = (request.args.get('desde') or '').strip()
    hasta = (request.args.get('hasta') or '').strip()
    if not desde or not hasta:
        return jsonify({'ok': False, 'msg': 'Ingrese el medidor inicial y el final del rango'}), 400
    return _responder(_SQL_RANGO.rstrip(), [desde, hasta], COLS_RANGO, 'medidores_rango')


# Command12 "Ver medidores del retiro" (del retiro seleccionado en la grilla)
@informes_bp.route('/almacenes/informes/medidores_retiro/<int:id_retiro>')
@login_requerido
def medidores_retiro(id_retiro):
    global conn_almacenes, cursor_almacenes
    conn_almacenes, cursor_almacenes = check_connection(conn_almacenes, cursor_almacenes, 'almacenes')
    cursor_almacenes.execute(
        "SELECT COUNT(*) FROM almacenes.retiromedidores WHERE idretiro = %s", (id_retiro,))
    if cursor_almacenes.fetchone()[0] == 0:
        return jsonify({'ok': False, 'msg': f'El retiro {id_retiro} no tiene medidores registrados'}), 404
    return _responder("SELECT * FROM almacenes.retiromedidores WHERE idretiro = %s "
                      "ORDER BY renglonretiro, item", [id_retiro], COLS_MED_RETIRO, 'medidores_retiro')


# ── Dar de Baja (Command8) ────────────────────────────────────────────────────
# Paso 1 (GET): informa si se puede dar de baja el pedido completo (P.I.M. en estado 10 /
# retiro en estado 30), para que la pantalla pregunte como el FoxPro.
# Paso 2 (POST): ejecuta.

def _estado_baja(modo, id_ped, renglon):
    if modo == 'pim':
        cursor_almacenes.execute(
            "SELECT estado FROM almacenes.pedidosvirtuales WHERE idpedidovirtual = %s", (id_ped,))
        cab = cursor_almacenes.fetchone()
        cursor_almacenes.execute(
            "SELECT estado FROM almacenes.detallespedidosvirtuales "
            "WHERE idpedidovirtual = %s AND renglon = %s", (id_ped, renglon))
        det = cursor_almacenes.fetchone()
        if not cab or not det:
            return None
        return {'completo': cab[0] == 10,
                'renglon': det[0] is None or det[0] < 20,     # aún sin pedido de precio / O.C.
                'msg_no': 'No se puede dar de baja ese PIM'}
    cursor_almacenes.execute(
        "SELECT estado FROM almacenes.retiromateriales WHERE idretiro = %s", (id_ped,))
    cab = cursor_almacenes.fetchone()
    cursor_almacenes.execute(
        "SELECT estado FROM almacenes.detallesretiromateriales "
        "WHERE idretiro = %s AND renglon = %s", (id_ped, renglon))
    det = cursor_almacenes.fetchone()
    if not cab or not det:
        return None
    return {'completo': cab[0] == 30, 'renglon': det[0] == 30,   # sin retirar
            'msg_no': 'No se puede dar de baja este retiro'}


@informes_bp.route('/almacenes/informes/baja', methods=['GET', 'POST'])
@login_requerido
def baja():
    global conn_almacenes, cursor_almacenes
    conn_almacenes, cursor_almacenes = check_connection(conn_almacenes, cursor_almacenes, 'almacenes')

    d = request.get_json() if request.method == 'POST' else request.args
    modo = d.get('modo')
    try:
        id_ped, renglon = int(d.get('id')), int(d.get('renglon'))
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'msg': 'Seleccione un renglón del informe'}), 400
    if modo not in ('pim', 'retiro'):
        return jsonify({'ok': False, 'msg': 'Solo se dan de baja P.I.M. y Retiros'}), 400

    est = _estado_baja(modo, id_ped, renglon)
    if est is None:
        return jsonify({'ok': False, 'msg': 'Pedido inexistente'}), 404
    if request.method == 'GET':
        return jsonify({'ok': True, 'completo': est['completo'], 'renglon': est['renglon'],
                        'msg_no': est['msg_no']})

    completo = bool(d.get('completo')) and est['completo']
    if not completo and not est['renglon']:
        return jsonify({'ok': False, 'msg': est['msg_no']}), 400

    if modo == 'pim':
        det, cab, col, baja_est = ('detallespedidosvirtuales', 'pedidosvirtuales', 'idpedidovirtual', 9)
    else:
        det, cab, col, baja_est = ('detallesretiromateriales', 'retiromateriales', 'idretiro', 39)
    try:
        if completo:
            cursor_almacenes.execute(
                f"UPDATE almacenes.{det} SET estado = %s WHERE {col} = %s", (baja_est, id_ped))
            cursor_almacenes.execute(
                f"UPDATE almacenes.{cab} SET estado = %s WHERE {col} = %s", (baja_est, id_ped))
        else:
            cursor_almacenes.execute(
                f"UPDATE almacenes.{det} SET estado = %s WHERE {col} = %s AND renglon = %s",
                (baja_est, id_ped, renglon))
        conn_almacenes.commit()
        return jsonify({'ok': True, 'msg': 'El pedido se ha dado de baja correctamente'})
    except Exception as e:
        conn_almacenes.rollback()
        return jsonify({'ok': False, 'msg': f'El pedido NO se ha podido dar de baja: {e}'}), 500
