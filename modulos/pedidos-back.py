# modulos/pedidos.py
from flask import Blueprint, render_template, request, session, jsonify, flash, redirect, url_for
from conexiones import conn, cursor
from datetime import datetime
from modulos.utils import login_requerido


pedidos_bp = Blueprint('pedidos', __name__)

@pedidos_bp.route('/pedidos.pedido_interno', methods=['GET', 'POST'])
@login_requerido
def pedido_interno():
    cursor.execute("SELECT cd1, cd2, material, stock FROM almacenes.materiales")
    materiales = cursor.fetchall()

    if request.method == 'POST':
        accion = request.form.get('accion')
        if accion == 'agregar':
            material = request.form.get('material')
            cantidad = request.form.get('cantidad')
            if material and cantidad:
                pedidos = session.get('pedidos', [])
                cd1, cd2 = material.split('|')
                descripcion = next((m[2] for m in materiales if m[0] == cd1 and m[1] == cd2), '')
                stock = next((m[3] for m in materiales if m[0] == cd1 and m[1] == cd2), 0)
                pedidos.append({
                    'cd1': cd1,
                    'cd2': cd2,
                    'descripcion': descripcion,
                    'cantidad': cantidad,
                    'stock': stock
                })
                session['pedidos'] = pedidos
                flash('Material agregado a la lista.', 'success')
        elif accion == 'limpiar':
            session['pedidos'] = []
            flash('Lista de materiales limpiada.', 'info')
        elif accion == 'subir':
            id_sector = int(session.get('id_sector'))
            pedidos = session.get('pedidos', [])
            idproyectoespecial = 0
            usuario_id = int(session.get('id'))
            estado = 30
#            quienretira_str = request.form.get('quienretira', '').strip()
#            if not quienretira_str.isdigit():
#                flash('Debe seleccionar quién retira el material.', 'danger')
#                return redirect(url_for('pedidos.pedido_interno'))  # o volver al formulario
#            quienretira = int(quienretira_str)
            motivo = 0  # Valor fijo
            ubicacion = request.form.get('ubicacion', '')
            detalles = request.form.get('detalles', '')
            cargo = 0  # Valor por defecto o usar request.form.get('cargo', 0) si viene del formulario

 
            if not pedidos:
                flash('No hay materiales en la lista.', 'danger')
            elif not quienretira:
                flash('Debe seleccionar quién retira el material.', 'danger')
            else:
                try:
                    cursor.execute("SELECT DATABASE();")
                    print("📌 Base actual conectada:", cursor.fetchone()[0])

                    retiro_id = cursor.lastrowid

                    for renglon, item in enumerate(pedidos, start=1):
                        cursor.execute("USE almacenes")

                        cursor.execute("""
                            INSERT INTO almacenes.detallesretiromateriales (
                                idretiro, renglon, sector, cd1, cd2, cantidad,
                                estado, cantidadentregada, autorizacion
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """, (
                            retiro_id, renglon, id_sector,
                            item['cd1'], item['cd2'], item['cantidad'],
                            0, 0, 2
                        ))

                    conn.commit()
                    session['pedidos'] = []
                    flash(f'Retiro #{retiro_id} guardado correctamente.', 'success')
                except Exception as e:
                    conn.rollback()
                    flash(f'Error al registrar el retiro: {str(e)}', 'danger')

        elif accion == 'cerrar':
            session.clear()
            return redirect(url_for('login.login'))

        pass

    pedidos = session.get('pedidos', [])
    return render_template(
        'pedido_interno.html',
        usuario=session['usuario'],
        materiales=materiales,
        pedidos=pedidos,
        fecha_actual=datetime.now().strftime('%d/%m/%Y'),
        distrito="FORMOSA"
    )

@pedidos_bp.route('/buscar_materiales', methods=['POST'])
@login_requerido
def buscar_materiales():
    try:
        datos = request.get_json()
        texto = datos.get('texto', '').upper().strip()
        id_sector = session.get('id_sector', '')

        if not texto or not id_sector:
            return jsonify([])

        palabras = texto.split()
        condiciones = [f"material LIKE '%{palabra}%'" for palabra in palabras]
        filtro = " AND ".join(condiciones)

        query = f"""
            SELECT cd1, cd2, material, stock
            FROM almacenes.vmaterialesdesectores
            WHERE sector = %s AND {filtro}
            ORDER BY cd1, cd2
        """

        cursor.execute(query, (id_sector,))
        resultados = cursor.fetchall()

        materiales = [
            {'cd1': r[0], 'cd2': r[1], 'material': r[2], 'stock': r[3]}
            for r in resultados
        ]
        return jsonify(materiales)

    except Exception as e:
        print("❌ Error en /buscar_materiales:", e)
        return jsonify([]), 500