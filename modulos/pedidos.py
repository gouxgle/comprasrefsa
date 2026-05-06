# modulos/pedidos.py
from flask import Blueprint, render_template, request, session, jsonify, flash, redirect, url_for
from conexiones import conn, cursor, check_connection
from datetime import datetime
from modulos.utils import login_requerido, puede_pim

pedidos_bp = Blueprint('pedidos', __name__)

conn, cursor = check_connection(conn, cursor, 'comun')


@pedidos_bp.route('/pedidos.pedido_interno', methods=['GET', 'POST'])
@login_requerido
def pedido_interno():
    if not puede_pim():
        return redirect(url_for('menu_bp.menu_principal'))

    global conn, cursor
    conn, cursor = check_connection(conn, cursor, 'comun')

    id_sector = session.get('id_sector', 0)

    # Materiales del sector para Select2
    cursor.execute("""
        SELECT cd1, cd2, material, stock
        FROM almacenes.vmaterialesdesectores
        WHERE sector = %s
        ORDER BY cd1, cd2
    """, (id_sector,))
    materiales = cursor.fetchall()

    if request.method == 'POST':
        accion = request.form.get('accion')

        if accion == 'agregar':
            material = request.form.get('material', '').strip()
            cantidad_str = request.form.get('cantidad', '').strip()
            if not material or not cantidad_str:
                flash('Debe seleccionar un material y especificar cantidad.', 'danger')
                return redirect(url_for('pedidos.pedido_interno'))
            try:
                cantidad = float(cantidad_str)
                if cantidad <= 0:
                    raise ValueError
            except ValueError:
                flash('Cantidad inválida.', 'danger')
                return redirect(url_for('pedidos.pedido_interno'))

            cd1, cd2 = material.split('|')
            mat = next((m for m in materiales if m[0] == cd1 and m[1] == cd2), None)
            if not mat:
                flash('Material no encontrado.', 'danger')
                return redirect(url_for('pedidos.pedido_interno'))

            pedidos = session.get('pedidos', [])
            existente = next((p for p in pedidos if p['cd1'] == cd1 and p['cd2'] == cd2), None)
            if existente:
                existente['cantidad'] = round(float(existente['cantidad']) + cantidad, 2)
                flash(f'Cantidad actualizada para {mat[2]}.', 'info')
            else:
                pedidos.append({
                    'cd1': cd1, 'cd2': cd2, 'descripcion': mat[2],
                    'cantidad': cantidad, 'stock': float(mat[3]) if mat[3] else 0
                })
                flash('Material agregado a la lista.', 'success')
            session['pedidos'] = pedidos
            session.modified = True

        elif accion == 'eliminar':
            try:
                idx = int(request.form.get('index', ''))
                pedidos = session.get('pedidos', [])
                del pedidos[idx]
                session['pedidos'] = pedidos
                session.modified = True
                flash('Material eliminado de la lista.', 'info')
            except (ValueError, IndexError):
                flash('Error al eliminar el material.', 'danger')

        elif accion == 'limpiar':
            session['pedidos'] = []
            session.modified = True
            flash('Lista limpiada.', 'info')

        elif accion == 'subir':
            pedidos = session.get('pedidos', [])
            usuario_id = session.get('id')
            comentarios = request.form.get('observaciones', '').strip()

            if not pedidos:
                flash('No hay materiales en la lista.', 'danger')
            else:
                try:
                    cursor.execute("""
                        INSERT INTO almacenes.pedidosvirtuales
                            (jefatura, fecha, quienpidio, estado, proyectoespecial,
                             autorizadoproyectoespecial, comentarios, autorizacion, sector)
                        VALUES (%s, %s, %s, 0, 0, 0, %s, 2, %s)
                    """, (id_sector, datetime.now().date(), usuario_id, comentarios, id_sector))

                    pedido_id = cursor.lastrowid

                    for renglon, item in enumerate(pedidos, start=1):
                        cursor.execute("""
                            INSERT INTO almacenes.detallespedidosvirtuales
                                (idpedidovirtual, renglon, sector, cd1, cd2, cantidad,
                                 estado, cantidadingresada, autorizacion,
                                 cantidadpp, cantidadoc, destino)
                            VALUES (%s, %s, %s, %s, %s, %s, 0, 0, 1, 0, 0, %s)
                        """, (pedido_id, renglon, id_sector,
                              item['cd1'], item['cd2'], item['cantidad'], id_sector))

                    conn.commit()
                    session['pedidos'] = []
                    session.modified = True
                    flash(f'PIM N° {pedido_id} registrado correctamente.', 'success')
                except Exception as e:
                    conn.rollback()
                    flash(f'Error al registrar el pedido: {str(e)}', 'danger')

        return redirect(url_for('pedidos.pedido_interno'))

    pedidos = session.get('pedidos', [])
    return render_template(
        'pedido_interno.html',
        usuario=session['usuario'],
        materiales=materiales,
        pedidos=pedidos,
        fecha_actual=datetime.now().strftime('%d/%m/%Y'),
    )


@pedidos_bp.route('/buscar_materiales', methods=['POST'])
@login_requerido
def buscar_materiales():
    global conn, cursor
    conn, cursor = check_connection(conn, cursor, 'comun')
    try:
        datos = request.get_json()
        texto = datos.get('texto', '').strip().upper()
        id_sector = session.get('id_sector', '')
        if not texto or not id_sector:
            return jsonify([])

        palabras = texto.split()
        condiciones = ' AND '.join('material LIKE %s' for _ in palabras)
        params = [f'%{p}%' for p in palabras] + [id_sector]

        cursor.execute(
            f"SELECT cd1, cd2, material, stock FROM almacenes.vmaterialesdesectores "
            f"WHERE {condiciones} AND sector = %s ORDER BY cd1, cd2 LIMIT 50",
            params
        )
        resultados = cursor.fetchall()
        return jsonify([
            {'cd1': r[0], 'cd2': r[1], 'material': r[2], 'stock': float(r[3]) if r[3] else 0}
            for r in resultados
        ])
    except Exception as e:
        print("Error en buscar_materiales:", e)
        return jsonify([]), 500
