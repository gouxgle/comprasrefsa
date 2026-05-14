# modulos/almacenes.py
#
# Ingeniería inversa de almacenes3.exe (VFP9):
#  - Lista retiros pendientes (estado 30/31) — vista del operador de almacenes
#  - Autoriza ítems (autorizacion 2→0) antes de la entrega
#  - Registra entrega (cantidadretirada, estado 32, fechaentregado, quienentrego)
#  - Actualiza estado general del vale de retiro
#
from flask import Blueprint, render_template, session, jsonify, request
from conexiones import conn_almacenes, cursor_almacenes, check_connection
from modulos.utils import almacenes_requerido as login_requerido
from datetime import date
from decimal import Decimal

almacenes_bp = Blueprint('almacenes', __name__)


def _s(v):
    if isinstance(v, date):    return v.strftime('%d/%m/%Y')
    if isinstance(v, Decimal): return float(v)
    return v


# ── Panel principal ────────────────────────────────────────────────────────────

@almacenes_bp.route('/almacenes')
@login_requerido
def panel():
    global conn_almacenes, cursor_almacenes
    conn_almacenes, cursor_almacenes = check_connection(conn_almacenes, cursor_almacenes, 'almacenes')

    # Todos los sectores que tienen cualquier vale → para el combo
    cursor_almacenes.execute("""
        SELECT DISTINCT j.idjefatura, j.jefatura
        FROM almacenes.retiromateriales r
        JOIN comun.jefaturas j ON j.idjefatura = r.sector
        ORDER BY j.jefatura
    """)
    sectores = [{'id': row[0], 'nombre': row[1] or ''}
                for row in cursor_almacenes.fetchall()]

    return render_template(
        'almacenes.html',
        usuario=session.get('usuario', ''),
        sector_nombre=session.get('sector_nombre', ''),
        sectores=sectores,
    )


# ── Vales por sector (AJAX) ───────────────────────────────────────────────────

@almacenes_bp.route('/almacenes/retiros_sector/<int:id_sector>')
@login_requerido
def retiros_sector(id_sector):
    global conn_almacenes, cursor_almacenes
    conn_almacenes, cursor_almacenes = check_connection(conn_almacenes, cursor_almacenes, 'almacenes')

    cursor_almacenes.execute("""
        SELECT r.idretiro,
               COALESCE(r.idproyectoespecial, 0)                        AS proyecto,
               r.fechapedido,
               COALESCE(op_pide.DescOperario, '')                       AS quien_pide,
               COALESCE(op_retira.DescOperario, p.nombre, '')           AS quien_retira,
               r.estado
        FROM almacenes.retiromateriales r
        LEFT JOIN comun.operarios op_pide   ON op_pide.IdOperario   = r.quienpidio
        LEFT JOIN comun.operarios op_retira ON op_retira.IdOperario  = r.quienretiro
        LEFT JOIN comun.personal  p         ON p.idpersonal          = r.quienretiro
        WHERE r.sector = %s
        ORDER BY r.idretiro DESC
        LIMIT 300
    """, (id_sector,))
    cols = [x[0] for x in cursor_almacenes.description]
    return jsonify([{c: _s(v) for c, v in zip(cols, r)}
                    for r in cursor_almacenes.fetchall()])


# ── Detalle de ítems (AJAX) ────────────────────────────────────────────────────

@almacenes_bp.route('/almacenes/items/<int:id_retiro>')
@login_requerido
def items_retiro(id_retiro):
    global conn_almacenes, cursor_almacenes
    conn_almacenes, cursor_almacenes = check_connection(conn_almacenes, cursor_almacenes, 'almacenes')

    cursor_almacenes.execute("""
        SELECT
            d.renglon,
            d.cd1, d.cd2,
            COALESCE(m.material, CONCAT(d.cd1,' ',d.cd2))         AS material,
            d.cantidadpedida,
            COALESCE(d.cantidadretirada, 0)                        AS cantidadretirada,
            d.cantidadpedida - COALESCE(d.cantidadretirada, 0)     AS a_entregar,
            d.estado,
            d.autorizacion,
            COALESCE(m.stock, 0)                                   AS stock_almacen
        FROM almacenes.detallesretiromateriales d
        LEFT JOIN almacenes.materiales m ON m.cd1 = d.cd1 AND m.cd2 = d.cd2
        WHERE d.idretiro = %s
        ORDER BY d.renglon
    """, (id_retiro,))
    cols = [x[0] for x in cursor_almacenes.description]
    return jsonify([{c: _s(v) for c, v in zip(cols, r)}
                    for r in cursor_almacenes.fetchall()])


# ── Autorizar ítems ────────────────────────────────────────────────────────────
# Réplica de: lGrab = SQLEXEC(lConn,
#   "update almacenes.detallesretiromateriales set autorizacion = 0
#    where idretiro = ?dPin AND renglon = ?dRng")
# En FoxPro autorizacion=0 → "AUTORIZADO"; 1 o 2 → "FALTA AUTORIZAR"

@almacenes_bp.route('/almacenes/autorizar', methods=['POST'])
@login_requerido
def autorizar():
    global conn_almacenes, cursor_almacenes
    conn_almacenes, cursor_almacenes = check_connection(conn_almacenes, cursor_almacenes, 'almacenes')

    data      = request.get_json()
    id_retiro = data.get('id_retiro')
    renglon   = data.get('renglon')     # int o None (None = todos)

    if not id_retiro:
        return jsonify({'ok': False, 'msg': 'Retiro no especificado'}), 400

    try:
        if renglon is not None:
            cursor_almacenes.execute(
                "UPDATE almacenes.detallesretiromateriales "
                "SET autorizacion = 0 "
                "WHERE idretiro = %s AND renglon = %s AND estado < 32",
                (id_retiro, renglon)
            )
        else:
            cursor_almacenes.execute(
                "UPDATE almacenes.detallesretiromateriales "
                "SET autorizacion = 0 "
                "WHERE idretiro = %s AND estado < 32",
                (id_retiro,)
            )
        conn_almacenes.commit()
        return jsonify({'ok': True})
    except Exception as e:
        conn_almacenes.rollback()
        return jsonify({'ok': False, 'msg': str(e)}), 500


# ── Registrar entrega ──────────────────────────────────────────────────────────
# Réplica del bloque de entrega del FoxPro:
#   UPDATE detallesretiromateriales SET cantidadretirada=?, estado=32,
#          fechaentregado=NOW(), quienentrego=?
#   UPDATE retiromateriales SET estado = MIN(estado detalles)

@almacenes_bp.route('/almacenes/entregar', methods=['POST'])
@login_requerido
def entregar():
    global conn_almacenes, cursor_almacenes
    conn_almacenes, cursor_almacenes = check_connection(conn_almacenes, cursor_almacenes, 'almacenes')

    data       = request.get_json()
    id_retiro  = data.get('id_retiro')
    renglon    = data.get('renglon')    # int o None (None = todos autorizados)
    usuario_id = session.get('id')

    if not id_retiro:
        return jsonify({'ok': False, 'msg': 'Retiro no especificado'}), 400

    try:
        if renglon is not None:
            cursor_almacenes.execute("""
                SELECT renglon, cantidadpedida
                FROM almacenes.detallesretiromateriales
                WHERE idretiro = %s AND renglon = %s AND estado < 32
            """, (id_retiro, renglon))
        else:
            cursor_almacenes.execute("""
                SELECT renglon, cantidadpedida
                FROM almacenes.detallesretiromateriales
                WHERE idretiro = %s AND estado < 32
            """, (id_retiro,))

        items = cursor_almacenes.fetchall()
        if not items:
            return jsonify({'ok': False,
                            'msg': 'No hay ítems autorizados pendientes de entrega'}), 400

        for rng, cant_pedida in items:
            cursor_almacenes.execute("""
                UPDATE almacenes.detallesretiromateriales
                SET cantidadretirada = %s,
                    estado           = 32,
                    fechaentregado   = CURDATE(),
                    quienentrego     = %s
                WHERE idretiro = %s AND renglon = %s
            """, (float(cant_pedida), usuario_id, id_retiro, rng))

        # Actualizar estado global del vale (MIN de estados de detalles)
        cursor_almacenes.execute(
            "SELECT MIN(estado) FROM almacenes.detallesretiromateriales "
            "WHERE idretiro = %s", (id_retiro,)
        )
        min_est = cursor_almacenes.fetchone()[0]
        cursor_almacenes.execute(
            "UPDATE almacenes.retiromateriales SET estado = %s WHERE idretiro = %s",
            (32 if min_est == 32 else 31, id_retiro)
        )

        conn_almacenes.commit()
        return jsonify({'ok': True, 'msg': 'Entrega registrada correctamente'})

    except Exception as e:
        conn_almacenes.rollback()
        return jsonify({'ok': False, 'msg': str(e)}), 500
