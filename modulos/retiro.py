# modulos/retiro.py
from flask import Blueprint, render_template, request, session, flash, redirect, url_for, jsonify
from conexiones import conn_almacenes, cursor_almacenes, check_connection
from conexiones import conn, cursor
from datetime import datetime
from modulos.utils import login_requerido

retiro_bp = Blueprint('retiro', __name__)

# Jefaturas con comportamiento especial en retiro
SECTORES_CON_MOTIVO = {3, 5, 20}   # distribucion, zonas, oficina tecnica
SECTOR_HIGIENE = 27                 # requiere cargo (asignación a empleado)

conn_almacenes, cursor_almacenes = check_connection(conn_almacenes, cursor_almacenes, 'almacenes')
conn, cursor = check_connection(conn, cursor, 'comun')


@retiro_bp.route('/retiro_materiales', methods=['GET', 'POST'])
@login_requerido
def retiro_materiales():
    global conn_almacenes, cursor_almacenes, conn, cursor
    conn_almacenes, cursor_almacenes = check_connection(conn_almacenes, cursor_almacenes, 'almacenes')
    conn, cursor = check_connection(conn, cursor, 'comun')

    id_sector = int(session.get('id_sector', 0))

    if request.method == 'POST':
        accion = request.form.get('accion')

        if accion == 'agregar':
            material = request.form.get('material', '').strip()
            cantidad_str = request.form.get('cantidad', '').strip()
            if not material or not cantidad_str:
                flash('Debe seleccionar material y cantidad.', 'danger')
                return redirect(url_for('retiro.retiro_materiales'))
            try:
                cantidad = float(cantidad_str)
                if cantidad <= 0:
                    raise ValueError
            except ValueError:
                flash('Cantidad inválida.', 'danger')
                return redirect(url_for('retiro.retiro_materiales'))

            cd1, cd2 = material.split('|')
            cursor_almacenes.execute(
                "SELECT material, stock FROM almacenes.materiales WHERE cd1=%s AND cd2=%s",
                (cd1, cd2)
            )
            mat = cursor_almacenes.fetchone()
            if not mat:
                flash('Material no encontrado.', 'danger')
                return redirect(url_for('retiro.retiro_materiales'))

            lista = session.get('retiro_items', [])
            # Si ya existe el mismo material, acumular cantidad
            existente = next((i for i in lista if i['cd1'] == cd1 and i['cd2'] == cd2), None)
            if existente:
                existente['cantidad'] = round(float(existente['cantidad']) + cantidad, 2)
                flash(f'Cantidad actualizada para {mat[0]}.', 'info')
            else:
                lista.append({
                    'cd1': cd1,
                    'cd2': cd2,
                    'descripcion': mat[0],
                    'cantidad': cantidad,
                    'stock': float(mat[1]) if mat[1] else 0
                })
                flash('Material agregado.', 'success')
            session['retiro_items'] = lista
            session.modified = True

        elif accion == 'eliminar':
            idx_str = request.form.get('index', '')
            try:
                idx = int(idx_str)
                lista = session.get('retiro_items', [])
                del lista[idx]
                session['retiro_items'] = lista
                session.modified = True
                flash('Material eliminado de la lista.', 'info')
            except (ValueError, IndexError):
                flash('Error al eliminar el material.', 'danger')

        elif accion == 'limpiar':
            session['retiro_items'] = []
            session.modified = True
            flash('Lista limpiada.', 'info')

        elif accion == 'subir':
            lista = session.get('retiro_items', [])
            usuario_id = int(session.get('id'))
            quienretira_str = request.form.get('quienretira', '').strip()
            ubicacion = request.form.get('ubicacion', '').strip()
            detalles = request.form.get('detalles', '').strip()
            motivo_str = request.form.get('motivo', '0').strip()
            motivo = int(motivo_str) if motivo_str.isdigit() else 0
            cargo_persona_str = request.form.get('cargo_persona', '').strip()

            if not lista:
                flash('No hay materiales en la lista.', 'danger')
                return redirect(url_for('retiro.retiro_materiales'))
            if not quienretira_str or not quienretira_str.isdigit():
                flash('Debe seleccionar quién retira el material.', 'danger')
                return redirect(url_for('retiro.retiro_materiales'))

            quienretira = int(quienretira_str)
            cargo_flag = 1 if id_sector == SECTOR_HIGIENE else 0
            sector_destino_str = request.form.get('sector_destino', '').strip()
            destino = int(sector_destino_str) if sector_destino_str.isdigit() else id_sector

            try:
                cursor_almacenes.execute("""
                    INSERT INTO almacenes.retiromateriales
                        (sector, fechapedido, quienpidio, estado, quienretiro,
                         idproyectoespecial, destino, ubicacion, motivo, detalles, cargo)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    id_sector,
                    datetime.now().date(),
                    usuario_id,
                    30,
                    quienretira,
                    0,
                    destino,
                    ubicacion,
                    motivo,
                    detalles,
                    cargo_flag,
                ))
                retiro_id = cursor_almacenes.lastrowid

                cargo_detalle = int(cargo_persona_str) if cargo_persona_str.isdigit() else None

                for renglon, item in enumerate(lista, start=1):
                    cursor_almacenes.execute("""
                        INSERT INTO almacenes.detallesretiromateriales
                            (idretiro, renglon, cd1, cd2, cantidadpedida,
                             cantidadretirada, estado, autorizacion, cargo)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        retiro_id, renglon,
                        item['cd1'], item['cd2'], item['cantidad'],
                        0, 30, 2,
                        cargo_detalle,
                    ))

                conn_almacenes.commit()
                session['retiro_items'] = []
                session.modified = True
                flash(f'Retiro N° {retiro_id} registrado correctamente.', 'success')

            except Exception as e:
                conn_almacenes.rollback()
                flash(f'Error al registrar el retiro: {str(e)}', 'danger')

        return redirect(url_for('retiro.retiro_materiales'))

    lista = session.get('retiro_items', [])
    tiene_motivo = id_sector in SECTORES_CON_MOTIVO
    tiene_cargo = id_sector == SECTOR_HIGIENE

    # Stock disponible = stock_sector - cantidades en vales pendientes (estado 30/31)
    # Replica exactamente el cálculo de FoxPro
    cursor_almacenes.execute("""
        SELECT v.cd1, v.cd2, v.material,
               GREATEST(0, v.stock - COALESCE((
                   SELECT SUM(d.cantidadpedida)
                   FROM almacenes.detallesretiromateriales d
                   JOIN almacenes.retiromateriales r ON r.idretiro = d.idretiro
                   WHERE d.cd1 = v.cd1 AND d.cd2 = v.cd2
                     AND r.estado IN (30, 31)
                     AND r.sector = v.sector
               ), 0)) AS stock_disponible
        FROM almacenes.vmaterialesdesectores v
        WHERE v.sector = %s
        ORDER BY v.cd1, v.cd2
    """, (id_sector,))
    materiales = cursor_almacenes.fetchall()

    return render_template(
        'retiro_materiales.html',
        usuario=session['usuario'],
        lista=lista,
        materiales=materiales,
        fecha_actual=datetime.now().strftime('%d/%m/%Y'),
        tiene_motivo=tiene_motivo,
        tiene_cargo=tiene_cargo,
    )


@retiro_bp.route('/buscar_personas', methods=['GET'])
@login_requerido
def buscar_personas():
    global conn, cursor
    conn, cursor = check_connection(conn, cursor, 'comun')
    texto = request.args.get('q', '').strip().upper()
    if len(texto) < 2:
        return jsonify([])

    palabras = texto.split()
    condiciones = ' AND '.join('nombre LIKE %s' for _ in palabras)
    params = [f'%{p}%' for p in palabras]

    try:
        cursor.execute(
            f"SELECT idlegajo, nombre FROM comun.vpersonal2 WHERE {condiciones} ORDER BY nombre LIMIT 50",
            params
        )
        resultados = cursor.fetchall()
    except Exception as e:
        print("Error en buscar_personas:", e)
        return jsonify([]), 500

    return jsonify([{'id': r[0], 'nombre': r[1]} for r in resultados])


@retiro_bp.route('/buscar_localidades', methods=['GET'])
@login_requerido
def buscar_localidades():
    global conn_almacenes, cursor_almacenes
    conn_almacenes, cursor_almacenes = check_connection(conn_almacenes, cursor_almacenes, 'almacenes')
    q = request.args.get('q', '').strip()
    if len(q) < 2:
        return jsonify([])
    try:
        cursor_almacenes.execute(
            "SELECT idlocalidad, localidad, distrito FROM almacenes.localidades WHERE localidad LIKE %s ORDER BY localidad LIMIT 20",
            (f'%{q}%',)
        )
        return jsonify([{'id': r[0], 'nombre': r[1], 'extra': r[2]} for r in cursor_almacenes.fetchall()])
    except Exception as e:
        print("Error en buscar_localidades:", e)
        return jsonify([]), 500


@retiro_bp.route('/buscar_jefaturas', methods=['GET'])
@login_requerido
def buscar_jefaturas():
    global conn, cursor
    conn, cursor = check_connection(conn, cursor, 'comun')
    q = request.args.get('q', '').strip()
    if len(q) < 2:
        return jsonify([])
    try:
        cursor.execute(
            "SELECT idjefatura, jefatura FROM comun.jefaturas WHERE jefatura LIKE %s ORDER BY jefatura LIMIT 20",
            (f'%{q}%',)
        )
        return jsonify([{'id': r[0], 'nombre': r[1]} for r in cursor.fetchall()])
    except Exception as e:
        print("Error en buscar_jefaturas:", e)
        return jsonify([]), 500
