# modulos/modificar.py
#
# Modificación de P.I.M. y de Vales de Retiro de Materiales ya generados.
#
# P.I.M. (corregir un error al generarlo)
#   Lo puede modificar quien lo generó (quienpidio), mientras:
#     - estado = 0 (todavía no pasó a Compras) y NO esté autorizado (autorizacion 0/1/2)
#     - no sea de proyecto especial
#     - ningún renglón esté en el circuito de compras (pedido de precio / O.C. / ingresos)
#   Se puede: cambiar cantidades, cambiar/quitar/agregar materiales y editar los comentarios.
#
# Vale de retiro (ajustar al stock real)
#   Lo puede modificar quien lo generó o el personal de Almacenes, mientras el vale esté
#   sin retirar (30) o retirado parcialmente (31) y SOLO en los ítems todavía no entregados
#   (estado 30 y cantidadretirada = 0). Se puede: cambiar la cantidad, quitar ítems y agregar.
#   Si un ítem sube de cantidad o es nuevo, vuelve a "falta autorizar" (autorizacion = 2) y
#   se valida contra el stock disponible (misma cuenta que al crear el vale).
#
# Todo se hace en una transacción con FOR UPDATE, porque el FoxPro opera sobre las mismas
# tablas. Los renglones se renumeran 1..n solo si ninguna otra tabla los referencia.
#
from flask import Blueprint, render_template, session, jsonify, request, redirect, url_for, flash, current_app
from conexiones import conn_almacenes, cursor_almacenes, check_connection
from modulos.utils import login_requerido, puede_pim, puede_almacenes
from datetime import date
from decimal import Decimal

modificar_bp = Blueprint('modificar', __name__)

EPS          = 0.005
CANT_MAX     = 9999999.99
MAX_ITEMS    = 60
ESTADO_PIM_NUEVO       = 0
ESTADOS_RETIRO_ABIERTO = (30, 31)
RETIRO_SIN_ENTREGAR    = 30
AUT_RETIRO_PENDIENTE   = 2


class ModificarError(Exception):
    """Error de negocio: se informa tal cual al usuario (HTTP 400/409)."""
    def __init__(self, msg, status=400):
        super().__init__(msg)
        self.status = status


def _conectar():
    global conn_almacenes, cursor_almacenes
    conn_almacenes, cursor_almacenes = check_connection(conn_almacenes, cursor_almacenes, 'almacenes')


def _num(v):
    return float(v) if isinstance(v, Decimal) else v


def _fecha(v):
    return v.strftime('%d/%m/%Y') if isinstance(v, date) else v


def _uid():
    try:
        return int(session.get('id'))
    except (TypeError, ValueError):
        return None


def _palabras(campo, texto):
    palabras = [p for p in (texto or '').upper().split() if p]
    if not palabras:
        return '', []
    return ' AND '.join(f'{campo} LIKE %s' for _ in palabras), [f'%{p}%' for p in palabras]


def _cantidad(valor):
    try:
        c = round(float(valor), 2)
    except (TypeError, ValueError):
        raise ModificarError('Hay una cantidad inválida')
    if c <= 0:
        raise ModificarError('Las cantidades deben ser mayores a cero')
    if c > CANT_MAX:
        raise ModificarError('Hay una cantidad demasiado grande')
    return c


def _nombre(sql, params):
    cursor_almacenes.execute(sql, params)
    row = cursor_almacenes.fetchone()
    return (row[0] or '').strip() if row else ''


def _compactar(tabla, col_id, id_ped):
    """Renumera los renglones 1..n (ascendente: nunca pisa un renglón aún sin mover)."""
    cursor_almacenes.execute(
        f"SELECT renglon FROM almacenes.{tabla} WHERE {col_id} = %s ORDER BY renglon", (id_ped,))
    for nuevo, (actual,) in enumerate(cursor_almacenes.fetchall(), start=1):
        if actual != nuevo:
            cursor_almacenes.execute(
                f"UPDATE almacenes.{tabla} SET renglon = %s WHERE {col_id} = %s AND renglon = %s",
                (nuevo, id_ped, actual))


def _responder_error(e):
    return jsonify({'ok': False, 'msg': str(e)}), e.status


# ═════════════════════════════════════════════════════════════════════════════
#  P.I.M.
# ═════════════════════════════════════════════════════════════════════════════

def motivo_pim_no_modificable(estado, autorizacion, proyecto_especial, en_circuito):
    """Motivo (texto) por el que un P.I.M. no se puede modificar, o None si se puede.
    Lo usa también Estado de Pedidos para habilitar/deshabilitar el botón."""
    if estado != ESTADO_PIM_NUEVO:
        return 'El pedido ya fue tomado por Compras'
    if autorizacion in (3, 4):
        return 'El pedido ya fue autorizado'
    if proyecto_especial:
        return 'Es un pedido de proyecto especial'
    if en_circuito:
        return 'Tiene materiales en circuito de compra'
    return None


def _pim_leer(id_pim, bloquear=False):
    """(cabecera, renglones) de un P.I.M.; bloquea las filas si se va a escribir."""
    lock = ' FOR UPDATE' if bloquear else ''
    cursor_almacenes.execute(
        "SELECT idpedidovirtual, fecha, quienpidio, jefatura, estado, autorizacion, proyectoespecial, "
        "idproyectoespecial, comentarios FROM almacenes.pedidosvirtuales WHERE idpedidovirtual = %s" + lock,
        (id_pim,))
    r = cursor_almacenes.fetchone()
    if not r:
        raise ModificarError(f'El P.I.M. {id_pim} no existe', 404)
    cab = {'id': r[0], 'fecha': r[1], 'quien': r[2], 'sector': r[3], 'estado': r[4], 'autorizacion': r[5],
           'pe': bool(r[6] or r[7]), 'comentarios': r[8] or ''}
    cursor_almacenes.execute(
        "SELECT renglon, cd1, cd2, cantidad, estado, ordendecompra, cantidadpp, cantidadoc, cantidadingresada, "
        "autorizacion, destino FROM almacenes.detallespedidosvirtuales WHERE idpedidovirtual = %s "
        "ORDER BY renglon" + lock, (id_pim,))
    dets = [dict(zip(('renglon', 'cd1', 'cd2', 'cantidad', 'estado', 'oc', 'pp', 'coc', 'ing', 'aut', 'destino'),
                     (x[0], x[1], x[2], _num(x[3]), x[4], x[5], _num(x[6]), _num(x[7]), _num(x[8]), x[9], x[10])))
            for x in cursor_almacenes.fetchall()]
    return cab, dets


def _pim_motivo(cab, dets):
    """Reglas completas (con lectura de las tablas que referencian el pedido)."""
    uid = _uid()
    if not puede_pim() or uid is None or cab['quien'] != uid:
        return 'Solo puede modificar los pedidos que usted generó'
    en_circuito = any(d['estado'] != 0 or d['oc'] is not None or d['pp'] > 0 or d['coc'] > 0 or d['ing'] > 0
                      for d in dets)
    motivo = motivo_pim_no_modificable(cab['estado'], cab['autorizacion'], cab['pe'], en_circuito)
    if motivo:
        return motivo
    for tabla, col in (('comparativaprecios', 'pim'), ('detallescomparativaprecios', 'pim'),
                       ('detallespedidosreales', 'pim'), ('ingresospedidosvirtuales', 'idpedidovirtual')):
        cursor_almacenes.execute(f"SELECT 1 FROM almacenes.{tabla} WHERE {col} = %s LIMIT 1", (cab['id'],))
        if cursor_almacenes.fetchone():
            return 'Tiene materiales en circuito de compra'
    return None


def _material_del_sector(sector, cd1, cd2):
    cursor_almacenes.execute(
        "SELECT material, stock FROM almacenes.vmaterialesdesectores WHERE sector = %s AND cd1 = %s AND cd2 = %s",
        (sector, cd1, cd2))
    return cursor_almacenes.fetchone()


@modificar_bp.route('/pim/modificar/<int:id_pim>')
@login_requerido
def pim_pantalla(id_pim):
    _conectar()
    try:
        cab, dets = _pim_leer(id_pim)
        motivo = _pim_motivo(cab, dets)
    except ModificarError as e:
        flash(str(e), 'danger')
        return redirect(url_for('estado.estado_pedido'))
    if motivo:
        flash(f'El P.I.M. {id_pim} no se puede modificar: {motivo}.', 'warning')
        return redirect(url_for('estado.estado_pedido'))

    items = []
    for d in dets:
        mat = _material_del_sector(cab['sector'], d['cd1'], d['cd2'])
        if not mat:   # material dado de baja del sector: se muestra igual para poder quitarlo
            cursor_almacenes.execute(
                "SELECT material FROM almacenes.materiales WHERE cd1 = %s AND cd2 = %s", (d['cd1'], d['cd2']))
            r = cursor_almacenes.fetchone()
            mat = (r[0] if r else f"{d['cd1']}-{d['cd2']}", None)
        items.append({'renglon': d['renglon'], 'cd1': d['cd1'], 'cd2': d['cd2'], 'material': (mat[0] or '').strip(),
                      'cantidad': d['cantidad'], 'stock': _num(mat[1]) if mat[1] is not None else None})

    cfg = {
        'modo': 'pim', 'id': id_pim, 'items': items, 'comentarios': cab['comentarios'],
        'cab': [
            {'etq': 'Fecha', 'val': _fecha(cab['fecha'])},
            {'etq': 'Sector', 'val': _nombre("SELECT jefatura FROM comun.jefaturas WHERE idjefatura = %s",
                                             (cab['sector'],)).upper()},
            {'etq': 'Generó', 'val': _nombre("SELECT DescOperario FROM comun.operarios WHERE IdOperario = %s",
                                             (cab['quien'],))},
            {'etq': 'Autorización', 'val': 'Pendiente de autorización'},
        ],
        'urls': {'guardar': url_for('modificar.pim_guardar', id_pim=id_pim),
                 'materiales': url_for('modificar.pim_materiales', id_pim=id_pim),
                 'volver': url_for('estado.estado_pedido'),
                 'imprimir': f'/imprimir_pim/{id_pim}'},
    }
    return render_template('modificar_pedido.html', cfg=cfg, titulo=f'P.I.M. N° {id_pim}',
                           etiqueta_doc='Pedido Interno', usuario=session.get('usuario'))


@modificar_bp.route('/pim/modificar/<int:id_pim>/materiales')
@login_requerido
def pim_materiales(id_pim):
    _conectar()
    try:
        cab, dets = _pim_leer(id_pim)
    except ModificarError as e:
        return _responder_error(e)
    q = (request.args.get('q') or '').strip().upper()
    partes = q.replace('-', ' ').split()
    if len(partes) == 2 and len(partes[0]) <= 3 and partes[1].isdigit():
        cursor_almacenes.execute(
            "SELECT cd1, cd2, material, stock FROM almacenes.vmaterialesdesectores "
            "WHERE sector = %s AND cd1 = %s AND cd2 = %s", (cab['sector'], partes[0], partes[1].zfill(4)))
    else:
        where, params = _palabras('material', q)
        if not where:
            return jsonify({'results': []})
        cursor_almacenes.execute(
            f"SELECT cd1, cd2, material, stock FROM almacenes.vmaterialesdesectores "
            f"WHERE sector = %s AND {where} ORDER BY cd1, cd2 LIMIT 50", (cab['sector'], *params))
    return jsonify({'results': [
        {'id': f'{r[0]}-{r[1]}', 'cd1': r[0], 'cd2': r[1], 'material': (r[2] or '').strip(),
         'stock': _num(r[3]) or 0, 'text': f"{r[0]}-{r[1]}  {(r[2] or '').strip()}"}
        for r in cursor_almacenes.fetchall()]})


@modificar_bp.route('/pim/modificar/<int:id_pim>', methods=['POST'])
@login_requerido
def pim_guardar(id_pim):
    _conectar()
    d = request.get_json(silent=True) or {}
    try:
        items = d.get('items')
        if not isinstance(items, list) or not items:
            raise ModificarError('El pedido debe tener al menos un material')
        if len(items) > MAX_ITEMS:
            raise ModificarError(f'Un pedido admite hasta {MAX_ITEMS} materiales')
        comentarios = str(d.get('comentarios') or '').strip()

        cab, dets = _pim_leer(id_pim, bloquear=True)        # bloquea cabecera y renglones
        motivo = _pim_motivo(cab, dets)
        if motivo:
            raise ModificarError(f'El P.I.M. {id_pim} ya no se puede modificar: {motivo}', 409)
        actuales = {x['renglon']: x for x in dets}

        vistos, nuevos_items = set(), []
        for it in items:
            cd1, cd2 = str(it.get('cd1') or '').strip().upper(), str(it.get('cd2') or '').strip()
            cant = _cantidad(it.get('cantidad'))
            if not cd1 or not cd2:
                raise ModificarError('Hay un material sin código')
            if (cd1, cd2) in vistos:
                raise ModificarError(f'El material {cd1}-{cd2} está repetido; sume las cantidades en un solo renglón')
            vistos.add((cd1, cd2))
            reng = it.get('renglon')
            reng = int(reng) if reng not in (None, '') else None
            previo = actuales.get(reng) if reng is not None else None
            if reng is not None and previo is None:
                raise ModificarError('Un renglón del pedido cambió; vuelva a abrir la pantalla', 409)
            if previo is None or (previo['cd1'], previo['cd2']) != (cd1, cd2):
                if not _material_del_sector(cab['sector'], cd1, cd2):
                    raise ModificarError(f'El material {cd1}-{cd2} no está habilitado para el sector del pedido')
            nuevos_items.append({'renglon': reng, 'cd1': cd1, 'cd2': cd2, 'cantidad': cant})

        conservados = {i['renglon'] for i in nuevos_items if i['renglon'] is not None}
        if len(conservados) != sum(1 for i in nuevos_items if i['renglon'] is not None):
            raise ModificarError('Renglón repetido en la solicitud')

        # valores por defecto de los renglones nuevos: los de los renglones existentes
        modelo = dets[0] if dets else {'aut': 1, 'destino': cab['sector']}

        for reng in set(actuales) - conservados:                      # quitados
            cursor_almacenes.execute(
                "DELETE FROM almacenes.detallespedidosvirtuales WHERE idpedidovirtual = %s AND renglon = %s",
                (id_pim, reng))
        siguiente = max(actuales) if actuales else 0
        for it in nuevos_items:
            if it['renglon'] is not None:                              # modificados
                previo = actuales[it['renglon']]
                if (previo['cd1'], previo['cd2'], previo['cantidad']) != (it['cd1'], it['cd2'], it['cantidad']):
                    cursor_almacenes.execute(
                        "UPDATE almacenes.detallespedidosvirtuales SET cd1 = %s, cd2 = %s, cantidad = %s "
                        "WHERE idpedidovirtual = %s AND renglon = %s",
                        (it['cd1'], it['cd2'], it['cantidad'], id_pim, it['renglon']))
            else:                                                      # agregados (igual que al crear)
                siguiente += 1
                if siguiente > 255:
                    raise ModificarError('Demasiados renglones')
                cursor_almacenes.execute(
                    "INSERT INTO almacenes.detallespedidosvirtuales (idpedidovirtual, renglon, sector, cd1, cd2, "
                    "cantidad, estado, cantidadingresada, autorizacion, cantidadpp, cantidadoc, destino) "
                    "VALUES (%s, %s, %s, %s, %s, %s, 0, 0, %s, 0, 0, %s)",
                    (id_pim, siguiente, cab['sector'], it['cd1'], it['cd2'], it['cantidad'],
                     modelo['aut'], modelo['destino']))
        _compactar('detallespedidosvirtuales', 'idpedidovirtual', id_pim)
        cursor_almacenes.execute(
            "UPDATE almacenes.pedidosvirtuales SET comentarios = %s WHERE idpedidovirtual = %s",
            (comentarios, id_pim))
        conn_almacenes.commit()
        current_app.logger.info(
            "P.I.M. %s modificado por %s (%s): antes=%s después=%s", id_pim, session.get('usuario'), _uid(),
            [(x['cd1'], x['cd2'], x['cantidad']) for x in dets],
            [(i['cd1'], i['cd2'], i['cantidad']) for i in nuevos_items])
        return jsonify({'ok': True, 'msg': f'P.I.M. {id_pim} modificado', 'id': id_pim})
    except ModificarError as e:
        conn_almacenes.rollback()
        return _responder_error(e)
    except Exception as e:
        conn_almacenes.rollback()
        current_app.logger.exception('Error al modificar P.I.M. %s', id_pim)
        return jsonify({'ok': False, 'msg': f'No se pudo modificar el pedido: {e}'}), 500


# ═════════════════════════════════════════════════════════════════════════════
#  Vales de retiro
# ═════════════════════════════════════════════════════════════════════════════

# tablas que referencian (retiro, renglón): si alguna tiene filas no se borra ni renumera
_REFS_RETIRO = (('asignacionesmedidores', 'retiro', 'renglonretiro'), ('cargos', 'retiro', 'renglon'),
                ('devoluciones', 'retiro', 'renglon'), ('retiromedidores', 'idretiro', 'renglonretiro'),
                ('retiroprecintos', 'idretiro', 'renglonretiro'), ('retirosderollos', 'numpedidoretiro', 'renpedidoretiro'),
                ('retiroformularios', 'idpedidov', 'renglonpedidov'))


def _retiro_referencias(id_retiro):
    """{renglon: True} de los renglones referenciados por otras tablas; None en la clave si es a nivel vale."""
    refs = set()
    for tabla, col_ret, col_reng in _REFS_RETIRO:
        cursor_almacenes.execute(
            f"SELECT DISTINCT {col_reng} FROM almacenes.{tabla} WHERE {col_ret} = %s", (id_retiro,))
        refs.update(r[0] for r in cursor_almacenes.fetchall())
    return refs


def puede_modificar_retiro(quienpidio):
    uid = _uid()
    return puede_almacenes() or (uid is not None and uid == quienpidio)


def _retiro_leer(id_retiro, bloquear=False):
    lock = ' FOR UPDATE' if bloquear else ''
    cursor_almacenes.execute(
        "SELECT idretiro, sector, fechapedido, quienpidio, estado, quienretiro, destino, ubicacion "
        "FROM almacenes.retiromateriales WHERE idretiro = %s" + lock, (id_retiro,))
    r = cursor_almacenes.fetchone()
    if not r:
        raise ModificarError(f'El retiro {id_retiro} no existe', 404)
    cab = {'id': r[0], 'sector': r[1], 'fecha': r[2], 'quien': r[3], 'estado': r[4], 'retira': r[5],
           'destino': r[6], 'ubicacion': r[7] or ''}
    cursor_almacenes.execute(
        "SELECT renglon, cd1, cd2, cantidadpedida, cantidadretirada, estado, autorizacion, cargo, destino "
        "FROM almacenes.detallesretiromateriales WHERE idretiro = %s ORDER BY renglon" + lock, (id_retiro,))
    dets = [dict(zip(('renglon', 'cd1', 'cd2', 'cantidad', 'retirada', 'estado', 'aut', 'cargo', 'destino'),
                     (x[0], x[1], x[2], _num(x[3]), _num(x[4]), x[5], x[6], x[7], x[8])))
            for x in cursor_almacenes.fetchall()]
    for x in dets:
        x['bloqueado'] = x['estado'] != RETIRO_SIN_ENTREGAR or (x['retirada'] or 0) > 0
    return cab, dets


def _retiro_motivo(cab, dets):
    if not puede_modificar_retiro(cab['quien']):
        return 'Solo puede modificarlo quien lo generó o el personal de Almacenes'
    if cab['estado'] not in ESTADOS_RETIRO_ABIERTO:
        return 'El vale ya fue retirado por completo o dado de baja'
    if all(d['bloqueado'] for d in dets):
        return 'Todos sus materiales ya fueron entregados'
    return None


_SQL_DISPONIBLE = """
    SELECT v.stock - COALESCE((
               SELECT SUM(d.cantidadpedida)
               FROM almacenes.detallesretiromateriales d
               JOIN almacenes.retiromateriales r ON r.idretiro = d.idretiro
               WHERE d.cd1 = v.cd1 AND d.cd2 = v.cd2 AND r.estado IN (30, 31) AND r.sector = v.sector), 0)
    FROM almacenes.vmaterialesdesectores v
    WHERE v.sector = %s AND v.cd1 = %s AND v.cd2 = %s
"""


def _disponible(sector, cd1, cd2):
    """Stock disponible = stock del sector − lo pedido en vales abiertos (cuenta del FoxPro). None si no existe."""
    cursor_almacenes.execute(_SQL_DISPONIBLE, (sector, cd1, cd2))
    r = cursor_almacenes.fetchone()
    return None if r is None else float(r[0])


@modificar_bp.route('/retiro/modificar/<int:id_retiro>')
@login_requerido
def retiro_pantalla(id_retiro):
    _conectar()
    volver = url_for('almacenes.panel') if puede_almacenes() else url_for('estado.estado_pedido')
    try:
        cab, dets = _retiro_leer(id_retiro)
        motivo = _retiro_motivo(cab, dets)
    except ModificarError as e:
        flash(str(e), 'danger')
        return redirect(volver)
    if motivo:
        flash(f'El retiro {id_retiro} no se puede modificar: {motivo}.', 'warning')
        return redirect(volver)

    items = []
    for d in dets:
        cursor_almacenes.execute(
            "SELECT m.material FROM almacenes.materiales m WHERE m.cd1 = %s AND m.cd2 = %s", (d['cd1'], d['cd2']))
        r = cursor_almacenes.fetchone()
        disp = _disponible(cab['sector'], d['cd1'], d['cd2'])
        items.append({'renglon': d['renglon'], 'cd1': d['cd1'], 'cd2': d['cd2'],
                      'material': (r[0] if r else f"{d['cd1']}-{d['cd2']}").strip(),
                      'cantidad': d['cantidad'], 'retirada': d['retirada'] or 0, 'bloqueado': d['bloqueado'],
                      'autorizado': d['aut'] == 0, 'disponible': max(0.0, disp) if disp is not None else 0.0})

    nombre_destino = _nombre("SELECT jefatura FROM comun.jefaturas WHERE idjefatura = %s", (cab['destino'],))
    cfg = {
        'modo': 'retiro', 'id': id_retiro, 'items': items, 'comentarios': None,
        'cab': [
            {'etq': 'Fecha', 'val': _fecha(cab['fecha'])},
            {'etq': 'Sector', 'val': _nombre("SELECT jefatura FROM comun.jefaturas WHERE idjefatura = %s",
                                              (cab['sector'],)).upper()},
            {'etq': 'Generó', 'val': _nombre("SELECT DescOperario FROM comun.operarios WHERE IdOperario = %s",
                                             (cab['quien'],))},
            {'etq': 'Retira', 'val': _nombre("SELECT nombre FROM comun.personal WHERE idlegajo = %s", (cab['retira'],))},
            {'etq': 'Destino', 'val': (nombre_destino or cab['ubicacion']).upper()},
            {'etq': 'Estado', 'val': 'Sin retirar' if cab['estado'] == 30 else 'Retirado parcialmente'},
        ],
        'urls': {'guardar': url_for('modificar.retiro_guardar', id_retiro=id_retiro),
                 'materiales': url_for('modificar.retiro_materiales', id_retiro=id_retiro),
                 'volver': volver, 'imprimir': f'/imprimir_retiro/{id_retiro}'},
    }
    return render_template('modificar_pedido.html', cfg=cfg, titulo=f'Retiro N° {id_retiro}',
                           etiqueta_doc='Vale de Retiro de Materiales', usuario=session.get('usuario'))


@modificar_bp.route('/retiro/modificar/<int:id_retiro>/materiales')
@login_requerido
def retiro_materiales(id_retiro):
    _conectar()
    try:
        cab, dets = _retiro_leer(id_retiro)
    except ModificarError as e:
        return _responder_error(e)
    if not puede_modificar_retiro(cab['quien']):
        return jsonify({'ok': False, 'msg': 'Sin permiso'}), 403
    q = (request.args.get('q') or '').strip().upper()
    partes = q.replace('-', ' ').split()
    if len(partes) == 2 and len(partes[0]) <= 3 and partes[1].isdigit():
        filtro, params = 'v.cd1 = %s AND v.cd2 = %s', [partes[0], partes[1].zfill(4)]
    else:
        filtro, params = _palabras('v.material', q)
        if not filtro:
            return jsonify({'results': []})
    cursor_almacenes.execute(f"""
        SELECT v.cd1, v.cd2, v.material, v.stock,
               GREATEST(0, v.stock - COALESCE((
                   SELECT SUM(d.cantidadpedida) FROM almacenes.detallesretiromateriales d
                   JOIN almacenes.retiromateriales r ON r.idretiro = d.idretiro
                   WHERE d.cd1 = v.cd1 AND d.cd2 = v.cd2 AND r.estado IN (30, 31) AND r.sector = v.sector), 0))
        FROM almacenes.vmaterialesdesectores v
        WHERE v.sector = %s AND {filtro} ORDER BY v.cd1, v.cd2 LIMIT 50
    """, (cab['sector'], *params))
    return jsonify({'results': [
        {'id': f'{r[0]}-{r[1]}', 'cd1': r[0], 'cd2': r[1], 'material': (r[2] or '').strip(),
         'stock': _num(r[3]) or 0, 'disponible': _num(r[4]) or 0, 'text': f"{r[0]}-{r[1]}  {(r[2] or '').strip()}"}
        for r in cursor_almacenes.fetchall()]})


@modificar_bp.route('/retiro/modificar/<int:id_retiro>', methods=['POST'])
@login_requerido
def retiro_guardar(id_retiro):
    _conectar()
    d = request.get_json(silent=True) or {}
    try:
        cambios, nuevos = d.get('items') or [], d.get('nuevos') or []
        if not isinstance(cambios, list) or not isinstance(nuevos, list) or len(cambios) + len(nuevos) > 255:
            raise ModificarError('Solicitud inválida')

        cab, dets = _retiro_leer(id_retiro, bloquear=True)
        motivo = _retiro_motivo(cab, dets)
        if motivo:
            raise ModificarError(f'El retiro {id_retiro} ya no se puede modificar: {motivo}', 409)
        por_reng = {x['renglon']: x for x in dets}
        referenciados = _retiro_referencias(id_retiro)

        pedidos, vistos = {}, set()
        for c in cambios:
            try:
                reng = int(c.get('renglon'))
            except (TypeError, ValueError):
                raise ModificarError('Solicitud inválida')
            det = por_reng.get(reng)
            if det is None or reng in pedidos:
                raise ModificarError('Un renglón del vale cambió; vuelva a abrir la pantalla', 409)
            eliminar = bool(c.get('eliminar'))
            cant = det['cantidad'] if eliminar else _cantidad(c.get('cantidad'))
            if det['bloqueado']:
                if eliminar or abs(cant - det['cantidad']) > EPS:
                    raise ModificarError(f"El renglón {reng} ya fue entregado y no se puede modificar", 409)
                continue
            if eliminar and reng in referenciados:
                raise ModificarError(f'El renglón {reng} tiene registros asociados (medidores/devoluciones) '
                                     'y no se puede quitar')
            pedidos[reng] = {'eliminar': eliminar, 'cantidad': cant}

        # materiales que quedan en el vale (no se repiten)
        for x in dets:
            if pedidos.get(x['renglon'], {}).get('eliminar'):
                continue
            vistos.add((x['cd1'], x['cd2']))
        altas = []
        for n in nuevos:
            cd1, cd2 = str(n.get('cd1') or '').strip().upper(), str(n.get('cd2') or '').strip()
            cant = _cantidad(n.get('cantidad'))
            if (cd1, cd2) in vistos:
                raise ModificarError(f'El material {cd1}-{cd2} ya está en el vale; modifique su cantidad')
            vistos.add((cd1, cd2))
            if _disponible(cab['sector'], cd1, cd2) is None:
                raise ModificarError(f'El material {cd1}-{cd2} no está habilitado para el sector del vale')
            altas.append({'cd1': cd1, 'cd2': cd2, 'cantidad': cant})

        quedan = [x for x in dets if not pedidos.get(x['renglon'], {}).get('eliminar')]
        if not quedan and not altas:
            raise ModificarError('El vale debe conservar al menos un material (para anularlo use "Dar de baja")')

        # stock: solo se controla lo que SUBE (reducir siempre se permite: es el ajuste al stock real)
        delta = {}
        for reng, p in pedidos.items():
            det = por_reng[reng]
            nuevo = 0.0 if p['eliminar'] else p['cantidad']
            delta[(det['cd1'], det['cd2'])] = delta.get((det['cd1'], det['cd2']), 0.0) + (nuevo - det['cantidad'])
        for a in altas:
            delta[(a['cd1'], a['cd2'])] = delta.get((a['cd1'], a['cd2']), 0.0) + a['cantidad']
        for (cd1, cd2), dif in delta.items():
            if dif > EPS:
                disp = _disponible(cab['sector'], cd1, cd2) or 0.0
                if dif > disp + EPS:
                    cursor_almacenes.execute(
                        "SELECT material FROM almacenes.materiales WHERE cd1 = %s AND cd2 = %s", (cd1, cd2))
                    r = cursor_almacenes.fetchone()
                    raise ModificarError(f"Stock insuficiente para {cd1}-{cd2} {(r[0] if r else '').strip()}: "
                                         f"disponible {max(0.0, disp):g}, necesita {dif:g} más")

        # aplicar
        modelo = dets[0]
        for reng, p in pedidos.items():
            det = por_reng[reng]
            if p['eliminar']:
                cursor_almacenes.execute(
                    "DELETE FROM almacenes.detallesretiromateriales WHERE idretiro = %s AND renglon = %s",
                    (id_retiro, reng))
            elif abs(p['cantidad'] - det['cantidad']) > EPS:
                # si sube vuelve a "falta autorizar"; si baja conserva su autorización
                aut = AUT_RETIRO_PENDIENTE if p['cantidad'] > det['cantidad'] else det['aut']
                cursor_almacenes.execute(
                    "UPDATE almacenes.detallesretiromateriales SET cantidadpedida = %s, autorizacion = %s "
                    "WHERE idretiro = %s AND renglon = %s", (p['cantidad'], aut, id_retiro, reng))
        siguiente = max(por_reng)
        for a in altas:
            siguiente += 1
            if siguiente > 255:
                raise ModificarError('Demasiados renglones')
            cursor_almacenes.execute(
                "INSERT INTO almacenes.detallesretiromateriales (idretiro, renglon, cd1, cd2, cantidadpedida, "
                "cantidadretirada, estado, autorizacion, cargo, destino) VALUES (%s, %s, %s, %s, %s, 0, 30, %s, %s, %s)",
                (id_retiro, siguiente, a['cd1'], a['cd2'], a['cantidad'], AUT_RETIRO_PENDIENTE,
                 modelo['cargo'], modelo['destino']))
        # se renumera solo si ningún renglón fue entregado ni referenciado por otras tablas
        if not referenciados and not any(x['bloqueado'] for x in dets):
            _compactar('detallesretiromateriales', 'idretiro', id_retiro)

        # estado del vale según lo que queda: todo entregado → 32; algo entregado → 31; nada → 30
        cursor_almacenes.execute(
            "SELECT MIN(estado), SUM(estado <> 30 OR cantidadretirada > 0) "
            "FROM almacenes.detallesretiromateriales WHERE idretiro = %s", (id_retiro,))
        minimo, entregados = cursor_almacenes.fetchone()
        nuevo_estado = 32 if minimo == 32 else (31 if entregados else 30)
        if nuevo_estado != cab['estado']:
            cursor_almacenes.execute(
                "UPDATE almacenes.retiromateriales SET estado = %s WHERE idretiro = %s", (nuevo_estado, id_retiro))
        conn_almacenes.commit()
        current_app.logger.info(
            "Retiro %s modificado por %s (%s): antes=%s cambios=%s altas=%s", id_retiro, session.get('usuario'),
            _uid(), [(x['renglon'], x['cd1'], x['cd2'], x['cantidad']) for x in dets], pedidos, altas)
        return jsonify({'ok': True, 'msg': f'Retiro {id_retiro} modificado', 'id': id_retiro})
    except ModificarError as e:
        conn_almacenes.rollback()
        return _responder_error(e)
    except Exception as e:
        conn_almacenes.rollback()
        current_app.logger.exception('Error al modificar retiro %s', id_retiro)
        return jsonify({'ok': False, 'msg': f'No se pudo modificar el vale: {e}'}), 500
