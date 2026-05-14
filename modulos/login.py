from flask import Blueprint, render_template, request, redirect, session, url_for, flash, jsonify, make_response
from conexiones import conn, cursor, check_connection

login_bp = Blueprint('login', __name__)

conn, cursor = check_connection(conn, cursor, 'comun')


def _foxpro_pass_check(clave, nombre, pass_hex):
    """
    Verifica la clave contra el campo Pass del sistema FoxPro.
    Algoritmo extraído del exe:
        result += CHR(BITXOR(ASC(clave[i]), ASC(nombre[MOD(i, len(nombre))+1]) * 2))
    XOR simétrico: misma operación para cifrar y descifrar.
    Pass en MySQL viaja como UTF-8 → se convierte a cp1252 para obtener los bytes originales.
    """
    if not pass_hex or not clave or not nombre:
        return False
    try:
        nombre = nombre.strip()
        lnombre = len(nombre)
        stored_bytes = bytes.fromhex(pass_hex).decode('utf-8').encode('cp1252', errors='replace')
        if len(stored_bytes) != len(clave):
            return False
        result = b''
        for i, ch in enumerate(clave.encode('cp1252', errors='replace'), start=1):
            key_byte = (ord(nombre[i % lnombre]) * 2) & 0xFF
            result += bytes([ch ^ key_byte])
        return result == stored_bytes
    except Exception:
        return False


@login_bp.route('/login', methods=['GET', 'POST'])
def login():
    global conn, cursor
    conn, cursor = check_connection(conn, cursor, 'comun')

    if request.method == 'POST':
        if request.form.get('accion') == 'cerrar':
            return redirect(url_for('login.logout'))

        usuario = request.form.get('usuario', '').strip()
        clave   = request.form.get('clave',   '').strip()
        if not usuario or not clave:
            flash('Debe ingresar usuario y clave.', 'danger')
            return render_template('login.html')

        cursor.execute(
            "SELECT DescOperario, Tipo, HEX(Pass) FROM comun.operarios WHERE IdOperario = %s",
            (usuario,)
        )
        result = cursor.fetchone()

        if result:
            nombre   = result[0].strip()
            tipo     = (result[1] or '').strip()
            pass_hex = result[2] or ''

            if not _foxpro_pass_check(clave, nombre, pass_hex):
                flash('Credenciales inválidas', 'danger')
                return render_template('login.html')

            session.clear()
            session.permanent = False
            session['usuario'] = nombre
            session['id']      = usuario
            session['tipo']    = tipo

            cursor.execute("""
                SELECT DISTINCT t.idjefatura, v.tipooperario, j.jefatura
                FROM comun.voperarios v
                JOIN comun.tiposoperarios t ON v.idtipooperario = t.idtipooperario
                JOIN comun.jefaturas j      ON t.idjefatura     = j.idjefatura
                WHERE v.idoperario = %s
            """, (usuario,))
            sectores = cursor.fetchall()

            session['sectores'] = [
                {'id': row[0], 'tipo': (row[1] or '').strip(), 'nombre': (row[2] or '').strip()}
                for row in sectores
            ]

            if len(sectores) == 0:
                session.clear()
                flash(
                    f'El usuario {nombre} no tiene sectores asignados. '
                    'Contacte al administrador del sistema.',
                    'danger'
                )
                return render_template('login.html')
            elif len(sectores) == 1:
                session['id_sector']     = sectores[0][0]
                session['tipooperario']  = (sectores[0][1] or '').strip()
                session['sector_nombre'] = (sectores[0][2] or '').strip()
                return redirect(url_for('menu_bp.menu_principal'))
            else:
                return render_template('login.html',
                                       seleccionar_sector=True,
                                       sectores=session['sectores'])

        flash('Credenciales inválidas', 'danger')

    return render_template('login.html')


@login_bp.route('/logout')
def logout():
    session.clear()
    response = make_response(redirect(url_for('login.login')))
    response.delete_cookie('session')
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    return response


@login_bp.route('/seleccionar_sector', methods=['POST'])
def seleccionar_sector():
    sector_id = str(request.json.get('sector', ''))
    sector    = next(
        (s for s in session.get('sectores', []) if str(s['id']) == sector_id),
        None
    )
    if sector:
        session['id_sector']     = sector['id']
        session['tipooperario']  = sector['tipo']
        session['sector_nombre'] = sector['nombre']
    session.permanent = False
    return jsonify({'status': 'ok'})
