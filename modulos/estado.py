from flask import Blueprint, render_template, request, session, jsonify
from conexiones import conn_almacenes, cursor_almacenes, check_connection
from modulos.utils import login_requerido, puede_almacenes
from modulos.modificar import motivo_pim_no_modificable, puede_modificar_retiro
from datetime import date
from decimal import Decimal

estado_bp = Blueprint('estado', __name__)

LIMITE_RETIROS = 2000   # ítems de retiro más recientes que se listan


def _serialize(v):
    if isinstance(v, date):
        return v.strftime('%d/%m/%Y')
    if isinstance(v, Decimal):
        return float(v)
    return v


@estado_bp.route('/estado_pedido')
@login_requerido
def estado_pedido():
    return render_template('estado_pedido.html', usuario=session.get('usuario'))


@estado_bp.route('/estado_pedido/detalles')
@login_requerido
def detalles_estado():
    global conn_almacenes, cursor_almacenes
    conn_almacenes, cursor_almacenes = check_connection(conn_almacenes, cursor_almacenes, 'almacenes')

    tipo = request.args.get('tipo')
    usuario_id = session.get('id')
    id_sector  = session.get('id_sector', 0)

    if tipo == 'pim_lista':
        cursor_almacenes.execute("""
            SELECT p.idpedidovirtual, p.fecha, COUNT(d.renglon) AS total_items,
                   p.estado, p.autorizacion, (p.proyectoespecial <> 0 OR p.idproyectoespecial IS NOT NULL) AS pe,
                   COALESCE(SUM(d.estado <> 0 OR d.ordendecompra IS NOT NULL OR d.cantidadpp > 0
                                OR d.cantidadoc > 0 OR d.cantidadingresada > 0), 0) AS en_circuito
            FROM almacenes.pedidosvirtuales p
            LEFT JOIN almacenes.detallespedidosvirtuales d
                   ON d.idpedidovirtual = p.idpedidovirtual
            WHERE p.quienpidio = %s
            GROUP BY p.idpedidovirtual, p.fecha, p.estado, p.autorizacion, p.proyectoespecial, p.idproyectoespecial
            ORDER BY p.idpedidovirtual DESC
        """, (usuario_id,))

    elif tipo == 'pim':
        cursor_almacenes.execute("""
            SELECT d.renglon, d.material, d.cantidad, d.fecharealizpedido, d.estado,
                   d.cantidadingresada, d.ordendecompra, d.renglonodc
            FROM almacenes.vdetallespedidosvirtuales d
            JOIN almacenes.pedidosvirtuales p ON p.idpedidovirtual = d.idpedidovirtual
            WHERE p.quienpidio = %s
            ORDER BY p.idpedidovirtual DESC, d.renglon
        """, (usuario_id,))

    elif tipo == 'retiro':
        # Consulta directa (la vista vdetallesretiromateriales2 recorre 430k filas y tarda >10 s):
        # los LIMIT_RETIROS ítems más recientes del sector
        cursor_almacenes.execute(f"""
            SELECT STRAIGHT_JOIN d.idretiro AS nro_retiro, m.material, d.cantidadpedida AS cantidad,
                   r.fechapedido AS fecha, e.estado, d.cantidadretirada, d.quienentrego, d.renglon,
                   r.estado AS estado_vale, r.quienpidio, d.estado AS idestado_detalle
            FROM almacenes.retiromateriales r
            JOIN almacenes.detallesretiromateriales d ON d.idretiro = r.idretiro
            LEFT JOIN almacenes.materiales m ON m.cd1 = d.cd1 AND m.cd2 = d.cd2
            LEFT JOIN almacenes.estadospedidos e ON e.idestado = d.estado
            WHERE r.sector = %s
            ORDER BY r.idretiro DESC, d.renglon
            LIMIT {LIMITE_RETIROS}
        """, (id_sector,))

    elif tipo == 'transferencia':
        cursor_almacenes.execute("""
            SELECT material, cantidad
            FROM almacenes.vdetallestransfermateriales1
            WHERE sector = %s
            ORDER BY id DESC
        """, (id_sector,))

    else:
        return jsonify([])

    filas    = cursor_almacenes.fetchall()
    columnas = [desc[0] for desc in cursor_almacenes.description]
    datos = [{c: _serialize(v) for c, v in zip(columnas, f)} for f in filas]

    # Botón "Modificar": habilitado solo si corresponde (el servidor lo vuelve a validar al abrir/guardar)
    if tipo == 'pim_lista':
        for d in datos:
            d['motivo'] = motivo_pim_no_modificable(d['estado'], d['autorizacion'], d['pe'], d['en_circuito'])
    elif tipo == 'retiro':
        for d in datos:
            if d['estado_vale'] not in (30, 31) or d['idestado_detalle'] != 30:
                d['motivo'] = 'Ya fue entregado'
            elif not puede_modificar_retiro(d['quienpidio']):
                d['motivo'] = 'Solo quien lo generó o Almacenes'
            else:
                d['motivo'] = None
    return jsonify(datos)
