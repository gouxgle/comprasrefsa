# modulos/devoluciones.py
#
# Ingeniería inversa de almacenes3.exe (VFP9) — pestaña "Devoluciones" (Page10):
#  - "Buscar" (Command1): ítems de retiros filtrados por material / quien retiró /
#    departamento, con lo ya devuelto (vista vdevoluciones1)
#  - "Ver" (Command9): mismo listado por N° de retiro y/o N° de proyecto especial
#  - "DEVOLVER ITEM" (Command4): INSERT en devoluciones + reintegro de stock según
#    "Devuelve a stock de": Sector / Almacén / Estado no reintegrable.
#    Retiro de proyecto especial → reintegra en detallesproyectosespeciales.
#
# FoxPro no modifica detallesretiromateriales al devolver: lo devuelto se calcula
# siempre como SUM(devoluciones.cantdevuelta) por retiro+renglón.
#
from flask import Blueprint, render_template, session, jsonify, request
from conexiones import conn_almacenes, cursor_almacenes, check_connection
from modulos.utils import almacenes_requerido as login_requerido
from datetime import date
from decimal import Decimal

devoluciones_bp = Blueprint('devoluciones', __name__)

EPS    = 0.005
LIMITE = 1000   # FoxPro traía la vista entera (430k filas); en web se acota

TIPO_SECTOR, TIPO_PROYECTO, TIPO_SIN_RETIRO = 1, 2, 3     # Optiongroup1
A_SECTOR, A_ALMACEN, NO_REINTEGRABLE = 1, 2, 3            # Optiongroup2


def _s(v):
    if isinstance(v, date):    return v.strftime('%d/%m/%Y')
    if isinstance(v, Decimal): return float(v)
    return v


def _rows():
    cols = [x[0] for x in cursor_almacenes.description]
    return [{c: _s(v) for c, v in zip(cols, r)} for r in cursor_almacenes.fetchall()]


class DevolucionError(Exception):
    pass


@devoluciones_bp.route('/almacenes/devoluciones')
@login_requerido
def panel():
    global conn_almacenes, cursor_almacenes
    conn_almacenes, cursor_almacenes = check_connection(conn_almacenes, cursor_almacenes, 'almacenes')

    cursor_almacenes.execute("SELECT idjefatura, jefatura FROM comun.jefaturas ORDER BY jefatura")
    sectores = [{'id': r[0], 'nombre': (r[1] or '').strip()} for r in cursor_almacenes.fetchall()]
    cursor_almacenes.execute(
        "SELECT idmotdevolucion, detmotdevolucion FROM almacenes.motivosdevoluciones "
        "ORDER BY idmotdevolucion")
    motivos = [{'id': r[0], 'nombre': r[1]} for r in cursor_almacenes.fetchall()]

    return render_template('devoluciones.html',
                           usuario=session.get('usuario', ''),
                           sector_nombre=session.get('sector_nombre', ''),
                           sectores=sectores, motivos=motivos)


# ── Listado de ítems de retiro (equivalente a vdevoluciones1) ────────────────
# Se consulta sin la vista: con GROUP BY el filtro no entra y tarda 4-8 s.

_SQL_ITEMS = """
    SELECT d.idretiro, d.renglon, d.cd1, d.cd2,
           d.cantidadpedida, d.cantidadretirada,
           (SELECT SUM(dv.cantdevuelta) FROM almacenes.devoluciones dv
             WHERE dv.retiro = d.idretiro AND dv.renglon = d.renglon) AS cantdevuelta,
           d.estado, d.fechaentregado, d.dealmacen,
           r.sector, COALESCE(j.jefatura, '') AS jefatura,
           r.quienretiro, r.idproyectoespecial,
           COALESCE(m.material, '') AS material,
           COALESCE(p.nombre, '')   AS nombre
    FROM almacenes.detallesretiromateriales d
    JOIN almacenes.retiromateriales r ON r.idretiro = d.idretiro
    LEFT JOIN almacenes.materiales m  ON m.cd1 = d.cd1 AND m.cd2 = d.cd2
    LEFT JOIN comun.personal p        ON p.idlegajo = r.quienretiro
    LEFT JOIN comun.jefaturas j       ON j.idjefatura = r.sector
"""


@devoluciones_bp.route('/almacenes/devoluciones/items')
@login_requerido
def items():
    global conn_almacenes, cursor_almacenes
    conn_almacenes, cursor_almacenes = check_connection(conn_almacenes, cursor_almacenes, 'almacenes')

    a = request.args
    conds, params = [], []
    # "Ver": por N° de retiro / proyecto especial (Command9)
    if a.get('retiro', '').isdigit():
        conds.append('d.idretiro = %s'); params.append(int(a['retiro']))
    if a.get('proyecto', '').isdigit():
        conds.append('r.idproyectoespecial = %s'); params.append(int(a['proyecto']))
    # "Buscar": material / quien retiró / sector (Command1)
    if a.get('cd1') and a.get('cd2'):
        conds.append('d.cd1 = %s AND d.cd2 = %s'); params += [a['cd1'], a['cd2']]
    if a.get('quienretiro', '').isdigit():
        conds.append('r.quienretiro = %s'); params.append(int(a['quienretiro']))
    if a.get('sector', '').isdigit():
        conds.append('r.sector = %s'); params.append(int(a['sector']))

    if not conds:
        return jsonify({'ok': False,
                        'msg': 'Indique al menos un filtro: material, quién retiró, departamento o N° de retiro'}), 400

    cursor_almacenes.execute(
        _SQL_ITEMS + ' WHERE ' + ' AND '.join(conds) +
        f' ORDER BY d.idretiro DESC, d.renglon LIMIT {LIMITE + 1}', params)
    filas = _rows()
    return jsonify({'ok': True, 'items': filas[:LIMITE], 'truncado': len(filas) > LIMITE})


# ── DEVOLVER ITEM ─────────────────────────────────────────────────────────────

def _reintegrar_stock(destino, sector, cd1, cd2, cant):
    if destino == A_SECTOR:
        # FoxPro: al stock del sector y al total general
        if not sector:
            raise DevolucionError('Debe haber un sector al cual devolver')
        cursor_almacenes.execute(
            "UPDATE almacenes.materialesdesectores SET stock = stock + %s "
            "WHERE cd1 = %s AND cd2 = %s AND sector = %s", (cant, cd1, cd2, sector))
        if cursor_almacenes.rowcount == 0:
            # FoxPro no lo detectaba y la cantidad se perdía
            raise DevolucionError(f'El material {cd1}-{cd2} no está habilitado para el sector {sector}')
        cursor_almacenes.execute(
            "UPDATE almacenes.materiales SET total = total + %s WHERE cd1 = %s AND cd2 = %s",
            (cant, cd1, cd2))
    elif destino == A_ALMACEN:
        cursor_almacenes.execute(
            "UPDATE almacenes.materiales SET stock = stock + %s WHERE cd1 = %s AND cd2 = %s",
            (cant, cd1, cd2))
        if cursor_almacenes.rowcount == 0:
            raise DevolucionError(f'Material {cd1}-{cd2} inexistente')
    # NO_REINTEGRABLE: queda registrada la devolución pero no vuelve a ningún stock


@devoluciones_bp.route('/almacenes/devoluciones/devolver', methods=['POST'])
@login_requerido
def devolver():
    global conn_almacenes, cursor_almacenes
    conn_almacenes, cursor_almacenes = check_connection(conn_almacenes, cursor_almacenes, 'almacenes')

    d = request.get_json() or {}
    try:
        tipo     = int(d.get('tipo') or TIPO_SECTOR)
        destino  = int(d.get('destino') or A_SECTOR)
        cant     = round(float(d.get('cantidad') or 0), 2)
        motivo   = int(d.get('motivo') or 0)
        quien    = int(d.get('quien_devuelve') or 0)   # FoxPro permitía vacío (queda 0)
        retiro   = int(d.get('id_retiro') or 0)
        renglon  = int(d.get('renglon') or 0)
        sector_c = int(d.get('sector') or 0)           # combo Departamento
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'msg': 'Datos inválidos'}), 400

    if cant <= 0:
        return jsonify({'ok': False, 'msg': 'No hay cantidad a devolver'}), 400
    if not motivo:
        return jsonify({'ok': False, 'msg': 'Seleccione el motivo de la devolución'}), 400
    if tipo not in (1, 2, 3) or destino not in (1, 2, 3):
        return jsonify({'ok': False, 'msg': 'Opción inválida'}), 400

    try:
        if tipo in (TIPO_SECTOR, TIPO_PROYECTO):
            if not retiro or not renglon:
                raise DevolucionError('No hay un número de Retiro al cual devolver')
            cursor_almacenes.execute("""
                SELECT d.cd1, d.cd2, d.cantidadretirada, r.sector, r.idproyectoespecial
                FROM almacenes.detallesretiromateriales d
                JOIN almacenes.retiromateriales r ON r.idretiro = d.idretiro
                WHERE d.idretiro = %s AND d.renglon = %s
                FOR UPDATE
            """, (retiro, renglon))
            row = cursor_almacenes.fetchone()
            if not row:
                raise DevolucionError('Ítem de retiro inexistente')
            cd1, cd2, retirado, sector_ret, id_pe = row

            cursor_almacenes.execute(
                "SELECT COALESCE(SUM(cantdevuelta), 0) FROM almacenes.devoluciones "
                "WHERE retiro = %s AND renglon = %s", (retiro, renglon))
            resta = round(float(retirado) - float(cursor_almacenes.fetchone()[0]), 2)
            if cant > resta + EPS:
                raise DevolucionError(
                    f'No puede devolver más de lo retirado menos lo ya devuelto (resta {resta:g})')

            cursor_almacenes.execute("""
                INSERT INTO almacenes.devoluciones
                    (retiro, renglon, cantdevuelta, fechadevolucion, quiendevolvio, motivo)
                VALUES (%s, %s, %s, CURDATE(), %s, %s)
            """, (retiro, renglon, cant, quien, motivo))

            if tipo == TIPO_SECTOR:
                # FoxPro: sector del retiro (text17); si no hay, el del combo
                _reintegrar_stock(destino, sector_ret or sector_c, cd1, cd2, cant)
            else:
                if not id_pe:
                    raise DevolucionError('No hay un número de Proyecto Especial al cual devolver')
                # solo vuelve al stock del proyecto especial (ignora "Devuelve a stock de")
                cursor_almacenes.execute("""
                    UPDATE almacenes.detallesproyectosespeciales
                    SET cantidadout    = cantidadout - %s,
                        cantidadactual = cantidadactual + %s,
                        estadoout      = IF(cantidadout = 0, 30, 31)
                    WHERE cd1 = %s AND cd2 = %s AND idproyectoespecial = %s
                """, (cant, cant, cd1, cd2, id_pe))
                if cursor_almacenes.rowcount == 0:
                    raise DevolucionError(f'Material {cd1}-{cd2} no encontrado en el proyecto especial {id_pe}')

        else:  # TIPO_SIN_RETIRO: devolución sin orden de retiro
            cd1, cd2 = (d.get('cd1') or '').strip(), (d.get('cd2') or '').strip()
            if not cd1 or not cd2:
                raise DevolucionError('No hay un material seleccionado')
            cursor_almacenes.execute("""
                INSERT INTO almacenes.devoluciones
                    (cantdevuelta, fechadevolucion, quiendevolvio, motivo)
                VALUES (%s, CURDATE(), %s, %s)
            """, (cant, quien, motivo))
            _reintegrar_stock(destino, sector_c, cd1, cd2, cant)

        conn_almacenes.commit()
        return jsonify({'ok': True, 'msg': 'Devolución subida correctamente'})
    except DevolucionError as e:
        conn_almacenes.rollback()
        return jsonify({'ok': False, 'msg': str(e)}), 400
    except Exception as e:
        conn_almacenes.rollback()
        return jsonify({'ok': False, 'msg': f'Devolución NO subida: {e}'}), 500
