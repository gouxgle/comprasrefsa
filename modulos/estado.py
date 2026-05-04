from flask import Blueprint, render_template, request, session, jsonify
from conexiones import conn_almacenes, cursor_almacenes, check_connection
from modulos.utils import login_requerido
from datetime import date
from decimal import Decimal

estado_bp = Blueprint('estado', __name__)


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
            SELECT p.idpedidovirtual, p.fecha, COUNT(d.renglon) AS total_items
            FROM almacenes.pedidosvirtuales p
            LEFT JOIN almacenes.detallespedidosvirtuales d
                   ON d.idpedidovirtual = p.idpedidovirtual
            WHERE p.quienpidio = %s
            GROUP BY p.idpedidovirtual, p.fecha
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
        cursor_almacenes.execute("""
            SELECT d.id AS nro_retiro, d.material, d.cantidad, r.fechapedido AS fecha, d.estado,
                   d.cantidadretirada, d.quienentrego, d.renglon
            FROM almacenes.vdetallesretiromateriales2 d
            JOIN almacenes.retiromateriales r ON r.idretiro = d.id
            WHERE r.quienpidio = %s
            ORDER BY r.idretiro DESC, d.renglon
        """, (usuario_id,))

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
    return jsonify([{c: _serialize(v) for c, v in zip(columnas, f)} for f in filas])
