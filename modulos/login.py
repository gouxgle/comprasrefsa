from flask import Blueprint, render_template, request, redirect, session, url_for, flash, jsonify, make_response
from conexiones import conn, cursor, check_connection

import hashlib

login_bp = Blueprint('login', __name__)

def encriptar_clave(clave):
    return hashlib.sha256(clave.encode()).hexdigest()

conn, cursor = check_connection(conn, cursor, 'comun')
@login_bp.route('/login', methods=['GET', 'POST'])
def login():
    
    global conn, cursor

    if request.method == 'POST':
        if request.form.get('accion') == 'cerrar':
            return redirect(url_for('login.logout'))

        usuario = request.form.get('usuario')
        clave = request.form.get('clave')
        if not usuario or not clave:
            flash("Debe ingresar usuario y clave.", "danger")
            return render_template("login.html")
        clave_hash = encriptar_clave(clave.strip())

                # Verifica que la conexión esté activa ANTES de ejecutar cualquier consulta
        if not conn.is_connected():
            conn.reconnect(attempts=3, delay=2)
            cursor = conn.cursor()

        cursor.execute("SELECT DescOperario, Pass2 FROM comun.operarios WHERE IdOperario = %s", (usuario,))
        result = cursor.fetchone()

        if result:
            nombre, pass2 = result[0].strip(), result[1].strip() if result[1] else ''
            if not pass2:
                cursor.execute("UPDATE comun.operarios SET Pass2 = %s WHERE IdOperario = %s", (clave_hash, usuario))
                conn.commit()
            elif pass2 != clave_hash:
                flash('Credenciales inválidas', 'danger')
                return render_template('login.html')
            
            session['usuario'] = nombre
            session['id'] = usuario

            cursor.execute("""
                SELECT DISTINCT t.idjefatura, v.tipooperario 
                FROM comun.voperarios v
                JOIN comun.tiposoperarios t ON v.idtipooperario = t.idtipooperario
                JOIN comun.jefaturas j ON t.idjefatura = j.idjefatura
                WHERE v.idoperario = %s
            """, (usuario,))
            sectores = cursor.fetchall()
            session['sectores'] = [{'id': row[0], 'nombre': row[1]} for row in sectores]

            if len(sectores) == 1:
                session['id_sector'] = sectores[0][0]
                return redirect(url_for('menu_bp.menu_principal'))
            else:
                return render_template('login.html', seleccionar_sector=True, sectores=session['sectores'])

        flash('Credenciales inválidas', 'danger')

    return render_template('login.html')

@login_bp.route('/logout')
def logout():
    session.clear()
    response = make_response(redirect(url_for('login.login')))
    response.delete_cookie('session')
    return response

@login_bp.route('/seleccionar_sector', methods=['POST'])
def seleccionar_sector():
    sector = request.json.get('sector')
    session['id_sector'] = sector
    return jsonify({'status': 'ok'})
