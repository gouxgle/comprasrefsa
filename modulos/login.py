from flask import Blueprint, render_template, request, redirect, session, url_for, flash, jsonify, make_response
from conexiones import conn, cursor, check_connection
from modulos.permisos import perfiles_de

login_bp = Blueprint('login', __name__)

conn, cursor = check_connection(conn, cursor, 'comun')


def _cp1252_bytes(s):
    """
    Codifica a los bytes originales de 1 byte que tenía el dato en FoxPro.
    cp1252 tiene huecos sin asignar (0x81, 0x8D, 0x8F, 0x90, 0x9D) — la migración
    original dejaba esos bytes tal cual (equivalente a latin-1) en vez de
    reemplazarlos. Usar solo 'cp1252' con errors='replace' los convierte en '?'
    y corrompe la comparación para cualquier clave que contenga esos bytes,
    dejando a esos usuarios sin poder loguearse nunca (fix: 2026-07-29).
    """
    out = bytearray()
    for ch in s:
        try:
            out += ch.encode('cp1252')
        except UnicodeEncodeError:
            out.append(ord(ch) if ord(ch) < 256 else 0x3F)
    return bytes(out)


def _foxpro_pass_check(clave, nombre, pass_hex):
    """
    Verifica la clave contra el campo Pass del sistema FoxPro.
    Algoritmo extraído del exe:
        result += CHR(BITXOR(ASC(clave[i]), ASC(nombre[MOD(i, len(nombre))+1]) * 2))
    XOR simétrico: misma operación para cifrar y descifrar.
    Pass en MySQL viaja como UTF-8 → se convierte a los bytes originales de 1 byte.
    """
    if not pass_hex or not clave or not nombre:
        return False
    try:
        nombre = nombre.strip()
        lnombre = len(nombre)
        stored_bytes = _cp1252_bytes(bytes.fromhex(pass_hex).decode('utf-8'))
        if len(stored_bytes) != len(clave):
            return False
        result = b''
        for i, ch in enumerate(_cp1252_bytes(clave), start=1):
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

            # Perfiles = (sector, tipo) cuyo tipo habilita alguna función (listas del FoxPro: permisos.py)
            perfiles, tipos = perfiles_de(cursor, usuario)
            session['tipos']    = tipos
            session['sectores'] = perfiles

            if not perfiles:
                session.clear()
                flash(
                    f'El usuario {nombre} no tiene funciones habilitadas en este sistema. '
                    'Contacte al administrador.',
                    'danger'
                )
                return render_template('login.html')
            elif len(perfiles) == 1:
                _activar_perfil(perfiles[0])
                return redirect(url_for('menu_bp.menu_principal'))
            else:
                return render_template('login.html',
                                       seleccionar_sector=True,
                                       sectores=perfiles)

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


def _activar_perfil(perfil):
    session['id_sector']     = perfil['id']
    session['tipo_id']       = perfil['tipo_id']
    session['tipooperario']  = perfil['tipo']
    session['sector_nombre'] = perfil['nombre']
    session.pop('sectores', None)        # solo hacía falta para elegir; no engordar la cookie
    session.permanent = False


@login_bp.route('/seleccionar_sector', methods=['POST'])
def seleccionar_sector():
    datos     = request.json or {}
    sector_id = str(datos.get('sector', ''))
    tipo_id   = str(datos.get('tipo', '')).strip()
    candidatos = [s for s in session.get('sectores', []) if str(s['id']) == sector_id]
    # un mismo sector puede tener varios tipos: se elige el par exacto (sector, tipo)
    perfil = next((s for s in candidatos if s['tipo_id'] == tipo_id), None) or (candidatos[0] if candidatos and not tipo_id else None)
    if perfil:
        _activar_perfil(perfil)
        return jsonify({'status': 'ok'})
    return jsonify({'status': 'error'}), 400
