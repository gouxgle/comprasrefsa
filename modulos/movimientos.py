# modulos/movimientos.py
#
# Ingeniería inversa de almacenes3.exe (VFP9) — pestaña "Movimientos" (Page11).
# Pestaña de solo consulta. Filtros (checks del FoxPro):
#   Sector (check2 + combo1) · Material (check3 + cd1/cd2) · Fecha (check1 + desde/hasta:
#   solo "desde" → fecha exacta; ambas → BETWEEN) · "Con falta entregar" (check4, solo Stock)
# Botones:
#   Stock            → vmaterialesdesectores2 / vmatsdesectoressiningresar (requiere sector)
#   Ingresos         → vingresopedidovirtualesagrupados (maestro) → vingresopedidosvirtuales
#   Ingresos Material→ vingresopedidosvirtuales + totales (pedida / ingresada / faltan)
#   Salidas          → vretiromateriales1 (maestro) → ítems del retiro con devoluciones
#   Salidas Material → vdetallesretiromateriales2 + totales (pedida / retirada / faltan / devuelta)
#   Devoluciones     → vdevoluciones
#   A Excel          → mismo resultado en CSV
#
from flask import Blueprint, render_template, session, jsonify, request
from conexiones import conn_almacenes, cursor_almacenes, check_connection
from modulos.utils import almacenes_requerido as login_requerido, respuesta_csv
from datetime import date, datetime
from decimal import Decimal

movimientos_bp = Blueprint('movimientos', __name__)

LIMITE       = 3000     # FoxPro cargaba vistas completas (hasta 430k filas)
LIMITE_EXCEL = 50000


def _s(v):
    if isinstance(v, date):    return v.strftime('%d/%m/%Y')
    if isinstance(v, Decimal): return float(v)
    return v


# ── Definición de consultas ───────────────────────────────────────────────────
# campos de filtro por consulta: (columna sector, columna fecha, columnas material)
# columnas: (clave, título, tipo) — tipo: 'n' número, 't' texto, 'c' centrado

_DEVUELTO = ("(SELECT SUM(dv.cantdevuelta) FROM almacenes.devoluciones dv "
             "WHERE dv.retiro = d.idretiro AND dv.renglon = d.renglon)")

CONSULTAS = {
    'stock': {
        'sql': "SELECT * FROM almacenes.vmaterialesdesectores2",
        'sector': 'sector', 'fecha': None, 'material': None,
        'orden': 'cd1, cd2',
        'cols': [('jefatura', 'Sector', 't'), ('cd1', 'CD1', 'c'), ('cd2', 'CD2', 'c'),
                 ('material', 'Material', 't'), ('stock', 'Stock', 'n'),
                 ('minimo', 'S.Seguridad', 'n'), ('medio', 'P.Pedido', 'n'),
                 ('maximo', 'S.Máximo', 'n'), ('unidad', 'Unidad', 'c'),
                 ('faltaningresar', 'Faltan Ing.', 'n'), ('restaretirar', 'Faltan Ret.', 'n')],
    },
    'stock_oc': {   # "Stock" con "Con Falta Entregar" tildado
        'sql': "SELECT * FROM almacenes.vmatsdesectoressiningresar",
        'sector': 'sector', 'fecha': None, 'material': None,
        'orden': 'cd1, cd2',
        'cols': [('jefatura', 'Sector', 't'), ('cd1', 'CD1', 'c'), ('cd2', 'CD2', 'c'),
                 ('material', 'Material', 't'), ('stock', 'Stock', 'n'),
                 ('minimo', 'S.Seguridad', 'n'), ('medio', 'P.Pedido', 'n'),
                 ('maximo', 'S.Máximo', 'n'), ('unidad', 'Unidad', 'c'),
                 ('ordencompra', 'O.C.', 'c'), ('itemoc', 'Ítem', 'c'),
                 ('faltaningresar', 'Faltan', 'n'), ('pim', 'PIM', 'c'),
                 ('renglonpim', 'Ítem', 'c'), ('proveedor', 'Proveedor', 't'),
                 ('fecha', 'Fecha O.C.', 'c')],
    },
    'ingresos': {   # maestro
        'sql': "SELECT * FROM almacenes.vingresopedidovirtualesagrupados",
        'sector': 'sector', 'fecha': 'fecha', 'material': None,
        'orden': 'idpedidovirtual DESC, sector',
        'cols': [('idpedidovirtual', 'Pedido', 'c'), ('jefatura', 'Sector', 't'),
                 ('fecha', 'Fecha', 'c'), ('comprobante', 'Comprobante', 't'),
                 ('estado', 'Estado', 't'), ('nombre', 'Quién pidió', 't'),
                 ('proveedor', 'Proveedor', 't'), ('proyectoespecial', 'P.E.', 'c')],
        'detalle': 'ingresos_det',
    },
    'ingresos_det': {
        'sql': "SELECT * FROM almacenes.vingresopedidosvirtuales",
        'clave': ('idpedidovirtual', 'idpedidovirtual'),
        'orden': 'renglon, ingreso',
        'cols': [('idpedidovirtual', 'Pedido', 'c'), ('renglon', 'Renglón', 'c'),
                 ('ingreso', 'Ingreso', 'c'), ('jefatura', 'Sector', 't'),
                 ('fecha', 'Fecha', 'c'), ('cd1', 'CD1', 'c'), ('cd2', 'CD2', 'c'),
                 ('material', 'Material', 't'), ('estadodetalle', 'Estado', 't'),
                 ('cantidadingresada', 'Cant.Ingr.', 'n'), ('comprobante', 'Comprobante', 't'),
                 ('proveedor', 'Proveedor', 't'), ('proyectoespecial', 'P.E.', 'c')],
    },
    'ingresos_material': {
        'sql': "SELECT * FROM almacenes.vingresopedidosvirtuales",
        'sector': 'sector', 'fecha': 'fecha', 'material': ('cd1', 'cd2'),
        'orden': 'idpedidovirtual DESC, renglon',
        'cols': [('idpedidovirtual', 'Pedido', 'c'), ('renglon', 'Renglón', 'c'),
                 ('ingreso', 'Ingreso', 'c'), ('jefatura', 'Sector', 't'),
                 ('fecha', 'Fecha', 'c'), ('cd1', 'CD1', 'c'), ('cd2', 'CD2', 'c'),
                 ('material', 'Material', 't'), ('estadodetalle', 'Estado', 't'),
                 ('nombre', 'Quién pidió', 't'), ('proveedor', 'Proveedor', 't'),
                 ('proyectoespecial', 'P.E.', 'c'), ('cantidad', 'Cant.Ped.', 'n'),
                 ('cantidadingresada', 'Cant.Ingr.', 'n')],
    },
    'salidas': {    # maestro
        'sql': "SELECT * FROM almacenes.vretiromateriales1",
        'sector': 'sector', 'fecha': 'fechapedido', 'material': None,
        'orden': 'idretiro DESC',
        'cols': [('idretiro', 'N° Retiro', 'c'), ('idproyectoespecial', 'Proy.Esp.', 'c'),
                 ('jefatura', 'Sector', 't'), ('fechapedido', 'Fecha', 'c'),
                 ('estado', 'Estado', 't'), ('quienpidio', 'Quién pidió', 't'),
                 ('quienretiro', 'Quién retiró', 't'), ('destino', 'Destino', 't')],
        'detalle': 'salidas_det',
    },
    'salidas_det': {    # vdevoluciones1 where idretiro = ? (sin la vista: tarda 4 s)
        'sql': f"""SELECT d.idretiro, r.idproyectoespecial, d.cd1, d.cd2,
                          m.material, d.fechaentregado, d.cantidadretirada,
                          {_DEVUELTO} AS cantdevuelta, d.dealmacen, p.nombre
                   FROM almacenes.detallesretiromateriales d
                   LEFT JOIN almacenes.retiromateriales r ON r.idretiro = d.idretiro
                   LEFT JOIN almacenes.materiales m ON m.cd1 = d.cd1 AND m.cd2 = d.cd2
                   LEFT JOIN comun.personal p ON p.idlegajo = r.quienretiro""",
        'clave': ('idretiro', 'd.idretiro'),
        'orden': 'd.renglon',
        'cols': [('idretiro', 'N° Retiro', 'c'), ('idproyectoespecial', 'Proy.Esp.', 'c'),
                 ('cd1', 'CD1', 'c'), ('cd2', 'CD2', 'c'), ('material', 'Material', 't'),
                 ('fechaentregado', 'Fecha Entregado', 'c'),
                 ('cantidadretirada', 'Cant.Retirada', 'n'), ('cantdevuelta', 'Cant.Devuelta', 'n'),
                 ('dealmacen', 'De Almacén', 'n'), ('nombre', 'Quién retiró', 't')],
    },
    'salidas_material': {   # vdetallesretiromateriales2 (sin la vista: tarda 8 s)
        # STRAIGHT_JOIN: sin él MySQL arranca por materialesdesectores y tarda >10 s
        'sql': f"""SELECT STRAIGHT_JOIN d.idretiro AS id, d.renglon, r.sector, d.cd1, d.cd2, m.material,
                          d.cantidadpedida AS cantidad, d.cantidadretirada,
                          d.fechaentregado AS fecha, d.dealmacen, e.estado,
                          IFNULL({_DEVUELTO}, 0) AS devuelto,
                          d.cantidadretirada - IFNULL({_DEVUELTO}, 0) AS totalsacado,
                          p.nombre
                   FROM almacenes.detallesretiromateriales d
                   LEFT JOIN almacenes.retiromateriales r ON r.idretiro = d.idretiro
                   LEFT JOIN almacenes.materiales m ON m.cd1 = d.cd1 AND m.cd2 = d.cd2
                   LEFT JOIN almacenes.estadospedidos e ON e.idestado = d.estado
                   LEFT JOIN comun.personal p ON p.idlegajo = r.quienretiro
                   JOIN almacenes.materialesdesectores ms
                        ON ms.sector = r.sector AND ms.cd1 = d.cd1 AND ms.cd2 = d.cd2 AND ms.baja = 0""",
        'sector': 'r.sector', 'fecha': 'd.fechaentregado', 'material': ('d.cd1', 'd.cd2'),
        'orden': 'd.idretiro DESC, d.renglon',
        'cols': [('id', 'N° Retiro', 'c'), ('renglon', 'Renglón', 'c'), ('sector', 'Sector', 'c'),
                 ('cd1', 'CD1', 'c'), ('cd2', 'CD2', 'c'), ('material', 'Material', 't'),
                 ('cantidad', 'Cant.Pedida', 'n'), ('cantidadretirada', 'Cant.Retirada', 'n'),
                 ('fecha', 'Fecha Entregado', 'c'), ('dealmacen', 'De Almacén', 'n'),
                 ('estado', 'Estado', 't'), ('devuelto', 'Devuelto', 'n'),
                 ('totalsacado', 'Total Sacado', 'n'), ('nombre', 'Quién retiró', 't')],
    },
    'devoluciones': {
        'sql': "SELECT * FROM almacenes.vdevoluciones",
        'sector': 'sector', 'fecha': 'fechadevolucion', 'material': ('cd1', 'cd2'),
        'orden': 'iddevolucion DESC',
        'cols': [('iddevolucion', 'Devolución', 'c'), ('idretiro', 'N° Retiro', 'c'),
                 ('renglon', 'Renglón', 'c'), ('cd1', 'CD1', 'c'), ('cd2', 'CD2', 'c'),
                 ('material', 'Material', 't'), ('fechadevolucion', 'Fecha Devuelto', 'c'),
                 ('cantidadretirada', 'Cant.Retirada', 'n'), ('cantdevuelta', 'Cant.Devuelta', 'n'),
                 ('nombre', 'Quién devolvió', 't'), ('motivo', 'Motivo', 't')],
    },
}

# Totales de los recuadros (Container1 / Container2 del FoxPro)
TOTALES = {
    'ingresos_material': lambda f: _totales([
        ('Cantidad pedida',   sum(r['cantidad'] or 0 for r in f)),
        ('Cantidad ingresada', sum(r['cantidadingresada'] or 0 for r in f))],
        ('Faltan ingresar', 0, 1)),
    'salidas_material': lambda f: _totales([
        ('Cantidad pedida',   sum(r['cantidad'] or 0 for r in f)),
        ('Cantidad retirada', sum(r['cantidadretirada'] or 0 for r in f))],
        ('Faltan retirar', 0, 1),
        ('Cantidad devuelta', sum(r['devuelto'] or 0 for r in f))),
}


def _totales(base, resta, *extra):
    out = [{'label': l, 'valor': round(float(v), 2)} for l, v in base]
    out.append({'label': resta[0], 'valor': round(out[resta[1]]['valor'] - out[resta[2]]['valor'], 2)})
    out += [{'label': l, 'valor': round(float(v), 2)} for l, v in extra]
    return out


def _fecha(txt):
    try:
        return datetime.strptime(txt, '%Y-%m-%d').date() if txt else None
    except ValueError:
        return None


def _armar(tipo, args, limite):
    q = CONSULTAS[tipo]
    conds, params = [], []

    if 'clave' in q:                                # consulta de detalle (maestro → detalle)
        conds.append(f"{q['clave'][1]} = %s"); params.append(args.get('id'))
    else:
        sector = args.get('sector', '')
        if q['sector'] and sector.isdigit():
            conds.append(f"{q['sector']} = %s"); params.append(int(sector))
        if q['material'] and args.get('cd1') and args.get('cd2'):
            conds.append(f"{q['material'][0]} = %s AND {q['material'][1]} = %s")
            params += [args['cd1'], args['cd2']]
        desde, hasta = _fecha(args.get('desde')), _fecha(args.get('hasta'))
        if q['fecha'] and desde:
            if hasta:
                conds.append(f"{q['fecha']} BETWEEN %s AND %s"); params += [desde, hasta]
            else:
                conds.append(f"{q['fecha']} = %s"); params.append(desde)

    # ninguna consulta base trae WHERE propio (los de las vistas quedan dentro de la vista)
    sql = q['sql']
    if conds:
        sql += ' WHERE ' + ' AND '.join(conds)
    sql += f" ORDER BY {q['orden']} LIMIT {limite + 1}"
    return sql, params


def _ejecutar(tipo, args, limite):
    sql, params = _armar(tipo, args, limite)
    cursor_almacenes.execute(sql, params)
    cols = [x[0] for x in cursor_almacenes.description]
    filas = [{c: _s(v) for c, v in zip(cols, r)} for r in cursor_almacenes.fetchall()]
    return filas[:limite], len(filas) > limite


def _validar(tipo, args):
    if tipo not in CONSULTAS:
        return 'Consulta inválida'
    if tipo in ('stock', 'stock_oc') and not args.get('sector', '').isdigit():
        return 'Ingrese un sector'
    if 'clave' in CONSULTAS[tipo] and not args.get('id', '').isdigit():
        return 'Falta el número de pedido / retiro'
    return None


@movimientos_bp.route('/almacenes/movimientos')
@login_requerido
def panel():
    global conn_almacenes, cursor_almacenes
    conn_almacenes, cursor_almacenes = check_connection(conn_almacenes, cursor_almacenes, 'almacenes')
    cursor_almacenes.execute("SELECT idjefatura, jefatura FROM comun.jefaturas ORDER BY jefatura")
    sectores = [{'id': r[0], 'nombre': (r[1] or '').strip()} for r in cursor_almacenes.fetchall()]
    return render_template('movimientos.html',
                           usuario=session.get('usuario', ''),
                           sector_nombre=session.get('sector_nombre', ''),
                           sectores=sectores, hoy=date.today().isoformat())


@movimientos_bp.route('/almacenes/movimientos/consulta/<tipo>')
@login_requerido
def consulta(tipo):
    global conn_almacenes, cursor_almacenes
    conn_almacenes, cursor_almacenes = check_connection(conn_almacenes, cursor_almacenes, 'almacenes')

    error = _validar(tipo, request.args)
    if error:
        return jsonify({'ok': False, 'msg': error}), 400
    filas, truncado = _ejecutar(tipo, request.args, LIMITE)
    q = CONSULTAS[tipo]
    return jsonify({
        'ok': True,
        'cols': [{'key': k, 'titulo': t, 'tipo': ty} for k, t, ty in q['cols']],
        'filas': filas,
        'truncado': truncado,
        'detalle': q.get('detalle'),
        'totales': TOTALES[tipo](filas) if tipo in TOTALES and not truncado else None,
    })


@movimientos_bp.route('/almacenes/movimientos/excel/<tipo>')
@login_requerido
def excel(tipo):
    global conn_almacenes, cursor_almacenes
    conn_almacenes, cursor_almacenes = check_connection(conn_almacenes, cursor_almacenes, 'almacenes')

    error = _validar(tipo, request.args)
    if error:
        return jsonify({'ok': False, 'msg': error}), 400
    filas, _ = _ejecutar(tipo, request.args, LIMITE_EXCEL)
    return respuesta_csv(CONSULTAS[tipo]['cols'], filas, f"movimientos_{tipo}_{date.today():%Y%m%d}.csv")
