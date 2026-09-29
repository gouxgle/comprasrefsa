# modulos/ingreso.py
#
# Ingeniería inversa de almacenes3.exe (VFP9) — pestaña "Ingreso" (Page2):
#  - Proveedor → Órdenes de Compra pendientes (pedidosreales estado < 23)
#  - OC → renglones (detallespedidosreales) → PIMs que la originaron
#    (detallespedidosvirtuales.ordendecompra / renglonodc)
#  - "Subir": ingreso parcial/total de un renglón OC ↔ renglón PIM contra un
#    comprobante (factura/remito), con impacto en stock del sector
#  - "Subir la Orden de Compra completa con este remito"
#  - "Nuevo Comprobante" (almacenes.comprobantes)
#
# Estados: 22 = ingresada parcialmente, 23 = ingresada totalmente.
# El campo `comprobante` de ingresospedidos* guarda idcomprobante (no el texto).
#
from flask import Blueprint, render_template, session, jsonify, request
from conexiones import conn_almacenes, cursor_almacenes, check_connection
from modulos.utils import almacenes_requerido as login_requerido
from datetime import date, datetime
from decimal import Decimal

ingreso_bp = Blueprint('ingreso', __name__)

ALMACEN = 26      # sector "Almacenes": su stock vive en materiales.stock, no en materialesdesectores
EPS     = 0.005   # columnas float(9,2) — comparar con tolerancia


def _s(v):
    if isinstance(v, date):    return v.strftime('%d/%m/%Y')
    if isinstance(v, Decimal): return float(v)
    return v


def _rows():
    cols = [x[0] for x in cursor_almacenes.description]
    return [{c: _s(v) for c, v in zip(cols, r)} for r in cursor_almacenes.fetchall()]


class IngresoError(Exception):
    pass


# ── Pantalla ───────────────────────────────────────────────────────────────────

@ingreso_bp.route('/almacenes/ingreso')
@login_requerido
def panel():
    global conn_almacenes, cursor_almacenes
    conn_almacenes, cursor_almacenes = check_connection(conn_almacenes, cursor_almacenes, 'almacenes')

    # Solo proveedores con OC pendientes de ingreso — es lo único que se puede ingresar
    cursor_almacenes.execute("""
        SELECT p.idproveedor, p.proveedor, COUNT(*) AS ocs
        FROM almacenes.pedidosreales r
        JOIN almacenes.proveedores p ON p.idproveedor = r.proveedor
        WHERE r.estado < 23
        GROUP BY p.idproveedor, p.proveedor
        ORDER BY p.proveedor
    """)
    proveedores = [{'id': r[0], 'nombre': (r[1] or '').strip(), 'ocs': r[2]}
                   for r in cursor_almacenes.fetchall()]

    return render_template(
        'ingreso.html',
        usuario=session.get('usuario', ''),
        sector_nombre=session.get('sector_nombre', ''),
        proveedores=proveedores,
        hoy=date.today().isoformat(),
    )


# ── Órdenes de compra pendientes del proveedor ────────────────────────────────
# FoxPro: select * from pedidosreales where estado < 23 AND proveedor = ?dPrv

@ingreso_bp.route('/almacenes/ingreso/ocs/<int:id_proveedor>')
@login_requerido
def ocs_proveedor(id_proveedor):
    global conn_almacenes, cursor_almacenes
    conn_almacenes, cursor_almacenes = check_connection(conn_almacenes, cursor_almacenes, 'almacenes')

    cursor_almacenes.execute("""
        SELECT idpedidoreal, fecha, total, estado, fechaultimaentrega
        FROM almacenes.pedidosreales
        WHERE estado < 23 AND proveedor = %s
        ORDER BY idpedidoreal DESC
    """, (id_proveedor,))
    return jsonify(_rows())


# ── Detalle de una OC: renglones + PIMs asociados + ingresos previos ─────────

@ingreso_bp.route('/almacenes/ingreso/oc/<int:id_oc>')
@login_requerido
def detalle_oc(id_oc):
    global conn_almacenes, cursor_almacenes
    conn_almacenes, cursor_almacenes = check_connection(conn_almacenes, cursor_almacenes, 'almacenes')

    cursor_almacenes.execute("""
        SELECT r.idpedidoreal, r.fecha, r.total, r.estado,
               r.proveedor AS idproveedor, COALESCE(p.proveedor, '') AS proveedor
        FROM almacenes.pedidosreales r
        LEFT JOIN almacenes.proveedores p ON p.idproveedor = r.proveedor
        WHERE r.idpedidoreal = %s
    """, (id_oc,))
    cab = _rows()
    if not cab:
        return jsonify({'ok': False, 'msg': f'La OC {id_oc} no existe'}), 404
    cab = cab[0]

    # FoxPro: detallespedidosreales where estado < 23 AND idpedidoreal = ?dIDPR
    cursor_almacenes.execute("""
        SELECT d.renglon, d.cd1, d.cd2,
               COALESCE(m.material, '') AS material,
               COALESCE(m.unidad, '')   AS unidad,
               d.cantidad, d.cantidadingresada, d.estado
        FROM almacenes.detallespedidosreales d
        LEFT JOIN almacenes.materiales m ON m.cd1 = d.cd1 AND m.cd2 = d.cd2
        WHERE d.idpedidoreal = %s AND d.estado < 23
        ORDER BY d.renglon
    """, (id_oc,))
    renglones = _rows()

    # FoxPro: vdetallespedidosvirtuales where estado < 23 AND ordendecompra = ? AND renglonodc = ?
    # (la vista expone estado como texto; se filtra sobre la tabla base)
    cursor_almacenes.execute("""
        SELECT v.renglonodc, v.idpedidovirtual, v.renglon, v.sector,
               COALESCE(j.jefatura, '') AS jefatura,
               v.cantidad, v.cantidadingresada, v.estado,
               COALESCE(pv.idproyectoespecial, 0) AS idproyectoespecial
        FROM almacenes.detallespedidosvirtuales v
        JOIN almacenes.pedidosvirtuales pv ON pv.idpedidovirtual = v.idpedidovirtual
        LEFT JOIN comun.jefaturas j        ON j.idjefatura        = v.sector
        WHERE v.ordendecompra = %s AND v.estado < 23
        ORDER BY v.renglonodc, v.idpedidovirtual, v.renglon
    """, (id_oc,))
    pims = {}
    for p in _rows():
        pims.setdefault(p['renglonodc'], []).append(p)
    for r in renglones:
        r['pims'] = pims.get(r['renglon'], [])

    cursor_almacenes.execute("""
        SELECT renglon, ingreso, material, cantidadingresada,
               comprobante, fecha
        FROM almacenes.vingresospedidosreales
        WHERE idpedidoreal = %s
        ORDER BY renglon, ingreso
    """, (id_oc,))
    ingresos = _rows()

    return jsonify({'ok': True, 'oc': cab, 'renglones': renglones, 'ingresos': ingresos})


# ── Comprobantes (factura / remito) ───────────────────────────────────────────

@ingreso_bp.route('/almacenes/ingreso/comprobantes/<int:id_proveedor>')
@login_requerido
def comprobantes(id_proveedor):
    global conn_almacenes, cursor_almacenes
    conn_almacenes, cursor_almacenes = check_connection(conn_almacenes, cursor_almacenes, 'almacenes')

    cursor_almacenes.execute("""
        SELECT idcomprobante, comprobante, fecha, totalneto
        FROM almacenes.comprobantes
        WHERE proveedor = %s
        ORDER BY idcomprobante DESC
        LIMIT 100
    """, (id_proveedor,))
    return jsonify(_rows())


# FoxPro (Page8.Container1): INSERT INTO comprobantes
#   (comprobante,proveedor,fecha,totalbruto,impuestos,totalneto)
@ingreso_bp.route('/almacenes/ingreso/comprobante', methods=['POST'])
@login_requerido
def nuevo_comprobante():
    global conn_almacenes, cursor_almacenes
    conn_almacenes, cursor_almacenes = check_connection(conn_almacenes, cursor_almacenes, 'almacenes')

    data = request.get_json() or {}
    numero    = (data.get('comprobante') or '').strip().upper()
    proveedor = data.get('proveedor')
    fecha_txt = data.get('fecha') or ''
    try:
        fecha    = datetime.strptime(fecha_txt, '%Y-%m-%d').date()
        subtotal = round(float(data.get('subtotal') or 0), 2)
        impuesto = round(float(data.get('impuestos') or 0), 2)
    except ValueError:
        return jsonify({'ok': False, 'msg': 'Fecha o importes inválidos'}), 400

    if not numero or not proveedor:
        return jsonify({'ok': False, 'msg': 'Falta completar algún dato'}), 400
    if len(numero) > 14:
        return jsonify({'ok': False, 'msg': 'El número de comprobante admite hasta 14 caracteres'}), 400

    try:
        cursor_almacenes.execute(
            "SELECT idcomprobante FROM almacenes.comprobantes "
            "WHERE proveedor = %s AND comprobante = %s", (proveedor, numero))
        if cursor_almacenes.fetchone():
            return jsonify({'ok': False,
                            'msg': f'El comprobante {numero} ya existe para este proveedor'}), 400

        cursor_almacenes.execute("""
            INSERT INTO almacenes.comprobantes
                (comprobante, proveedor, fecha, totalbruto, impuestos, totalneto)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (numero, proveedor, fecha, subtotal, impuesto, round(subtotal + impuesto, 2)))
        nuevo_id = cursor_almacenes.lastrowid
        conn_almacenes.commit()
        return jsonify({'ok': True, 'msg': 'Comprobante agregado',
                        'idcomprobante': nuevo_id, 'comprobante': numero})
    except Exception as e:
        conn_almacenes.rollback()
        return jsonify({'ok': False, 'msg': str(e)}), 500


# ── Helpers de la transacción de ingreso ──────────────────────────────────────

def _siguiente_ingreso(tabla, col_id, id_ped, renglon):
    cursor_almacenes.execute(
        f"SELECT COALESCE(MAX(ingreso), 0) + 1 FROM almacenes.{tabla} "
        f"WHERE {col_id} = %s AND renglon = %s", (id_ped, renglon))
    return int(cursor_almacenes.fetchone()[0])


def _registrar_ingresos(id_oc, reng_oc, id_pim, reng_pim, cant, id_comp):
    """Historial de ingresos (un pedido puede tener varios ingresos correlativos)."""
    if id_pim is not None:
        n = _siguiente_ingreso('ingresospedidosvirtuales', 'idpedidovirtual', id_pim, reng_pim)
        cursor_almacenes.execute("""
            INSERT INTO almacenes.ingresospedidosvirtuales
                (idpedidovirtual, renglon, ingreso, comprobante, cantidadingresada)
            VALUES (%s, %s, %s, %s, %s)
        """, (id_pim, reng_pim, n, id_comp, cant))
    if id_oc is not None:
        n = _siguiente_ingreso('ingresospedidosreales', 'idpedidoreal', id_oc, reng_oc)
        cursor_almacenes.execute("""
            INSERT INTO almacenes.ingresospedidosreales
                (idpedidoreal, renglon, ingreso, comprobante, cantidadingresada)
            VALUES (%s, %s, %s, %s, %s)
        """, (id_oc, reng_oc, n, id_comp, cant))


def _impactar_stock(sector, cd1, cd2, cant, sumar_total):
    """Suma al stock del sector destino. Almacenes (26) usa materiales.stock."""
    if sector == ALMACEN:
        cursor_almacenes.execute(
            "UPDATE almacenes.materiales SET stock = stock + %s WHERE cd1 = %s AND cd2 = %s",
            (cant, cd1, cd2))
    else:
        cursor_almacenes.execute(
            "UPDATE almacenes.materialesdesectores SET stock = stock + %s "
            "WHERE cd1 = %s AND cd2 = %s AND sector = %s",
            (cant, cd1, cd2, sector))
    if cursor_almacenes.rowcount == 0:
        # FoxPro lo ignoraba en silencio y el stock se perdía
        raise IngresoError(f'El material {cd1}-{cd2} no está habilitado para el sector {sector}')
    if sumar_total:
        cursor_almacenes.execute(
            "UPDATE almacenes.materiales SET total = total + %s WHERE cd1 = %s AND cd2 = %s",
            (cant, cd1, cd2))


def _impactar_proyecto_especial(id_pe, sector, cd1, cd2, cant, id_comp):
    """Proyecto especial: no toca stock de sector/almacén, sino detallesproyectosespeciales."""
    # MySQL evalúa las asignaciones de izquierda a derecha → estadoin ve cantidadin ya sumada
    cursor_almacenes.execute("""
        UPDATE almacenes.detallesproyectosespeciales
        SET cantidadactual = cantidadactual + %s,
            cantidadin     = cantidadin + %s,
            estadoin       = IF(cantidadpedida <= cantidadin, 23, 22)
        WHERE cd1 = %s AND cd2 = %s AND sector = %s AND idproyectoespecial = %s
    """, (cant, cant, cd1, cd2, sector, id_pe))
    if cursor_almacenes.rowcount == 0:
        raise IngresoError(f'Material {cd1}-{cd2} no encontrado en el proyecto especial {id_pe}')

    cursor_almacenes.execute("""
        UPDATE almacenes.proyectosespeciales
        SET estadoin = IF((SELECT COUNT(*) FROM almacenes.detallesproyectosespeciales
                           WHERE idproyectoespecial = %s AND estadoin <> 23) = 0, 23, 22)
        WHERE idproyectoespecial = %s
    """, (id_pe, id_pe))

    cursor_almacenes.execute("""
        SELECT renglon FROM almacenes.detallesproyectosespeciales
        WHERE cd1 = %s AND cd2 = %s AND idproyectoespecial = %s LIMIT 1
    """, (cd1, cd2, id_pe))
    rng = cursor_almacenes.fetchone()[0]
    cursor_almacenes.execute("""
        SELECT COALESCE(MAX(movimiento), 0) + 1 FROM almacenes.movisproyectosespeciales
        WHERE idproyectoespecial = %s AND renglon = %s
    """, (id_pe, rng))
    mov = int(cursor_almacenes.fetchone()[0])
    cursor_almacenes.execute("""
        INSERT INTO almacenes.movisproyectosespeciales
            (idproyectoespecial, renglon, movimiento, comprobante, cantidad, ingreso)
        VALUES (%s, %s, %s, %s, %s, 1)
    """, (id_pe, rng, mov, id_comp, cant))


def _actualizar_cabecera_pim(id_pim):
    # FoxPro: MIN(estado) de los detalles < 23 → 22, sino 23
    cursor_almacenes.execute("""
        UPDATE almacenes.pedidosvirtuales
        SET estado = IF((SELECT MIN(estado) FROM almacenes.detallespedidosvirtuales
                         WHERE idpedidovirtual = %s) < 23, 22, 23)
        WHERE idpedidovirtual = %s
    """, (id_pim, id_pim))


def _actualizar_cabecera_oc(id_oc):
    cursor_almacenes.execute(
        "SELECT COUNT(*) FROM almacenes.detallespedidosreales "
        "WHERE idpedidoreal = %s AND cantidadingresada < cantidad", (id_oc,))
    faltan = cursor_almacenes.fetchone()[0]
    if faltan == 0:
        cursor_almacenes.execute("""
            UPDATE almacenes.pedidosreales
            SET estado = 23, fechaultimaentrega = CURDATE(), fechaentregadototal = CURDATE()
            WHERE idpedidoreal = %s
        """, (id_oc,))
    else:
        cursor_almacenes.execute("""
            UPDATE almacenes.pedidosreales
            SET estado = 22, fechaultimaentrega = CURDATE()
            WHERE idpedidoreal = %s
        """, (id_oc,))


def _validar_comprobante(id_comp, id_proveedor):
    cursor_almacenes.execute(
        "SELECT proveedor FROM almacenes.comprobantes WHERE idcomprobante = %s", (id_comp,))
    row = cursor_almacenes.fetchone()
    if not row:
        raise IngresoError('Debe seleccionar un comprobante (factura / remito)')
    if row[0] != id_proveedor:
        raise IngresoError('El comprobante pertenece a otro proveedor')


# ── Subir: ingreso de un renglón OC ↔ renglón PIM ─────────────────────────────
# Réplica de Page2.Command7.Click

@ingreso_bp.route('/almacenes/ingreso/subir', methods=['POST'])
@login_requerido
def subir():
    global conn_almacenes, cursor_almacenes
    conn_almacenes, cursor_almacenes = check_connection(conn_almacenes, cursor_almacenes, 'almacenes')

    data = request.get_json() or {}
    try:
        id_oc    = int(data['id_oc'])
        reng_oc  = int(data['renglon_oc'])
        id_pim   = int(data['id_pim'])
        reng_pim = int(data['renglon_pim'])
        id_comp  = int(data.get('id_comprobante') or 0)
        cant     = round(float(data['cantidad']), 2)
    except (KeyError, TypeError, ValueError):
        return jsonify({'ok': False, 'msg': 'Datos incompletos'}), 400

    if cant <= 0:
        return jsonify({'ok': False, 'msg': "Debe indicar una cantidad en 'Ingreso al sector'"}), 400

    try:
        cursor_almacenes.execute("""
            SELECT d.cantidad, d.cantidadingresada, d.cd1, d.cd2, d.estado, r.proveedor
            FROM almacenes.detallespedidosreales d
            JOIN almacenes.pedidosreales r ON r.idpedidoreal = d.idpedidoreal
            WHERE d.idpedidoreal = %s AND d.renglon = %s
            FOR UPDATE
        """, (id_oc, reng_oc))
        oc = cursor_almacenes.fetchone()
        if not oc:
            raise IngresoError('Renglón de OC inexistente')
        cant_oc, ing_oc, cd1, cd2, est_oc, id_prov = oc
        if est_oc >= 23:
            raise IngresoError('Ese renglón de la OC ya fue ingresado o está dado de baja')

        _validar_comprobante(id_comp, id_prov)

        cursor_almacenes.execute("""
            SELECT v.cantidad, v.cantidadingresada, v.sector, pv.idproyectoespecial
            FROM almacenes.detallespedidosvirtuales v
            JOIN almacenes.pedidosvirtuales pv ON pv.idpedidovirtual = v.idpedidovirtual
            WHERE v.idpedidovirtual = %s AND v.renglon = %s
              AND v.ordendecompra = %s AND v.renglonodc = %s
            FOR UPDATE
        """, (id_pim, reng_pim, id_oc, reng_oc))
        pim = cursor_almacenes.fetchone()
        if not pim:
            raise IngresoError('El PIM seleccionado no corresponde a ese renglón de la OC')
        cant_pim, ing_pim, sector, id_pe = pim

        tir = round(float(ing_oc) + cant, 2)
        tiv = round(float(ing_pim) + cant, 2)
        if tir > float(cant_oc) + EPS:
            raise IngresoError('La cantidad supera lo pendiente de la Orden de Compra '
                               f'(resta {float(cant_oc) - float(ing_oc):g})')
        if tiv > float(cant_pim) + EPS:
            raise IngresoError('La cantidad supera lo pendiente del PIM '
                               f'(resta {float(cant_pim) - float(ing_pim):g})')

        eir = 23 if abs(tir - float(cant_oc)) < EPS else 22
        eiv = 23 if abs(tiv - float(cant_pim)) < EPS else 22

        # 1) OC (real)
        cursor_almacenes.execute("""
            UPDATE almacenes.detallespedidosreales
            SET estado = %s, cantidadingresada = cantidadingresada + %s
            WHERE idpedidoreal = %s AND renglon = %s
        """, (eir, cant, id_oc, reng_oc))
        _actualizar_cabecera_oc(id_oc)

        # 2) PIM (virtual)
        cursor_almacenes.execute("""
            UPDATE almacenes.detallespedidosvirtuales
            SET estado = %s, cantidadingresada = %s
            WHERE idpedidovirtual = %s AND renglon = %s
        """, (eiv, tiv, id_pim, reng_pim))
        _actualizar_cabecera_pim(id_pim)

        # 3) Stock — FoxPro solo suma a materiales.total cuando el destino es Almacenes
        if id_pe:
            _impactar_proyecto_especial(id_pe, sector, cd1, cd2, cant, id_comp)
        else:
            _impactar_stock(sector, cd1, cd2, cant, sumar_total=(sector == ALMACEN))

        # 4) Historial de ingresos
        _registrar_ingresos(id_oc, reng_oc, id_pim, reng_pim, cant, id_comp)

        conn_almacenes.commit()
        return jsonify({'ok': True, 'msg': f'Ingreso registrado: {cant:g} de {cd1}-{cd2}',
                        'oc_completa': eir == 23})
    except IngresoError as e:
        conn_almacenes.rollback()
        return jsonify({'ok': False, 'msg': str(e)}), 400
    except Exception as e:
        conn_almacenes.rollback()
        return jsonify({'ok': False, 'msg': f'Ingreso NO registrado: {e}'}), 500


# ── Subir la Orden de Compra completa con este remito ─────────────────────────
# Réplica de Page2.Command3.Click

@ingreso_bp.route('/almacenes/ingreso/subir_oc', methods=['POST'])
@login_requerido
def subir_oc():
    global conn_almacenes, cursor_almacenes
    conn_almacenes, cursor_almacenes = check_connection(conn_almacenes, cursor_almacenes, 'almacenes')

    data = request.get_json() or {}
    try:
        id_oc   = int(data['id_oc'])
        id_comp = int(data.get('id_comprobante') or 0)
    except (KeyError, TypeError, ValueError):
        return jsonify({'ok': False, 'msg': 'Debe elegir una Orden de Compra'}), 400

    try:
        cursor_almacenes.execute(
            "SELECT estado, proveedor FROM almacenes.pedidosreales "
            "WHERE idpedidoreal = %s FOR UPDATE", (id_oc,))
        row = cursor_almacenes.fetchone()
        if not row:
            raise IngresoError('OC inexistente')
        estado, id_prov = row
        _validar_comprobante(id_comp, id_prov)

        cursor_almacenes.execute(
            "SELECT COALESCE(SUM(cantidadingresada), 0) FROM almacenes.detallespedidosreales "
            "WHERE idpedidoreal = %s", (id_oc,))
        if estado in (22, 23) or float(cursor_almacenes.fetchone()[0]) > 0:
            raise IngresoError('Esta OC ya tuvo ingresos anteriores — ingrese renglón por renglón')
        if estado > 23:
            raise IngresoError('Esta OC está cerrada o dada de baja')

        # PIMs comprados con esta OC → stock + historial
        cursor_almacenes.execute("""
            SELECT v.idpedidovirtual, v.renglon, v.renglonodc, v.sector, v.cd1, v.cd2,
                   v.cantidad - v.cantidadingresada AS resta,
                   pv.idproyectoespecial
            FROM almacenes.detallespedidosvirtuales v
            JOIN almacenes.pedidosvirtuales pv ON pv.idpedidovirtual = v.idpedidovirtual
            WHERE v.ordendecompra = %s AND v.estado < 23
            ORDER BY v.renglonodc
            FOR UPDATE
        """, (id_oc,))
        pims = cursor_almacenes.fetchall()

        for id_pim, reng_pim, _reng_oc, sector, cd1, cd2, resta, id_pe in pims:
            resta = round(float(resta), 2)
            if resta <= 0:
                continue
            cursor_almacenes.execute("""
                UPDATE almacenes.detallespedidosvirtuales
                SET estado = 23, cantidadingresada = cantidad
                WHERE idpedidovirtual = %s AND renglon = %s
            """, (id_pim, reng_pim))
            # FoxPro en este botón siempre suma a materiales.total
            if id_pe:
                _impactar_proyecto_especial(id_pe, sector, cd1, cd2, resta, id_comp)
            else:
                _impactar_stock(sector, cd1, cd2, resta, sumar_total=True)
            _registrar_ingresos(None, None, id_pim, reng_pim, resta, id_comp)

        for id_pim in {p[0] for p in pims}:
            _actualizar_cabecera_pim(id_pim)

        # Renglones de la OC
        cursor_almacenes.execute("""
            SELECT renglon, cantidad - cantidadingresada
            FROM almacenes.detallespedidosreales
            WHERE idpedidoreal = %s AND estado < 23
            ORDER BY renglon
        """, (id_oc,))
        for reng_oc, resta in cursor_almacenes.fetchall():
            resta = round(float(resta), 2)
            if resta <= 0:
                continue
            _registrar_ingresos(id_oc, reng_oc, None, None, resta, id_comp)
        cursor_almacenes.execute("""
            UPDATE almacenes.detallespedidosreales
            SET estado = 23, cantidadingresada = cantidad
            WHERE idpedidoreal = %s AND estado < 23
        """, (id_oc,))
        cursor_almacenes.execute("""
            UPDATE almacenes.pedidosreales
            SET estado = 23, fechaultimaentrega = CURDATE(), fechaentregadototal = CURDATE()
            WHERE idpedidoreal = %s
        """, (id_oc,))

        conn_almacenes.commit()
        return jsonify({'ok': True, 'msg': f'OC {id_oc} ingresada completa'})
    except IngresoError as e:
        conn_almacenes.rollback()
        return jsonify({'ok': False, 'msg': str(e)}), 400
    except Exception as e:
        conn_almacenes.rollback()
        return jsonify({'ok': False, 'msg': f'OC NO ingresada: {e}'}), 500
