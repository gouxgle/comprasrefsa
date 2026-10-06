# modulos/baja.py
#
# Sección "Baja" de Almacenes — mantenimiento del catálogo de materiales.
# Ingeniería inversa de fox/bajar.exe (renombrar, stock/total/baja) y de la pestaña
# Materiales de almacenes3.exe (alta "Subir" + habilitar en sector).
#
#   Renombrar  (bajar.exe Command1)   UPDATE materiales SET material = nuevo
#   Stock      (bajar.exe Grid1)      UPDATE materiales SET stock, total, baja
#   Alta       (almacenes3 Page9)     INSERT materiales (+ INSERT materialesdesectores)
#
# Diferencias deliberadas con el FoxPro (que no pedía usuario y escribía a ciegas):
#   - "Dale a todo" reescribía las ~14.600 filas con los valores viejos de la grilla, pisando lo
#     que otros cambiaran mientras tanto → acá se guarda UN material y solo lo que cambió,
#     verificando que no haya cambiado en la base desde que se cargó (409 si cambió).
#   - `baja` no es un sí/no: 0 activo · 1 de baja · 5/6/7/8 marcas de migración. Solo se toca
#     al dar de baja (→ 1) o reactivar (→ 0); el valor anterior queda en la auditoría.
#   - El código cd2 automático del FoxPro (MAX+1 con VAL) fallaba con códigos no numéricos y
#     ante dos altas simultáneas → acá solo cuenta códigos de 4 dígitos y reintenta.
#   - Cada ajuste de stock exige un motivo y queda en logs/auditoria_baja.log.
#
import json
import os
import re
from datetime import date, datetime
from decimal import Decimal

import mysql.connector
from flask import Blueprint, render_template, session, jsonify, request, current_app
from conexiones import conn_almacenes, cursor_almacenes, check_connection
from modulos.utils import almacenes_requerido as login_requerido

baja_bp = Blueprint('baja', __name__)

LIMITE      = 300
EPS         = 0.005
NUM_MAX     = 999999.99            # float(8,2)
PRECIO_MAX  = 99999999.99          # double(10,2)
NOMBRE_MAX  = 120                  # varchar(120)
RE_CD2      = re.compile(r'^[0-9]{4}$')
RUTA_AUDIT  = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'logs', 'auditoria_baja.log')
SECTOR_ALMACEN_LEGACY = 26


class BajaError(Exception):
    def __init__(self, msg, status=400):
        super().__init__(msg)
        self.status = status


def _conectar():
    global conn_almacenes, cursor_almacenes
    conn_almacenes, cursor_almacenes = check_connection(conn_almacenes, cursor_almacenes, 'almacenes')


def _num(v):
    return float(v) if isinstance(v, Decimal) else v


def _auditar(accion, **datos):
    """Registro permanente de cada cambio (quién, cuándo, antes/después)."""
    reg = {'fecha': datetime.now().isoformat(timespec='seconds'), 'accion': accion,
           'usuario': session.get('usuario'), 'id_usuario': session.get('id'), **datos}
    linea = json.dumps(reg, ensure_ascii=False, default=str)
    current_app.logger.info('AUDITORIA BAJA %s', linea)
    try:
        os.makedirs(os.path.dirname(RUTA_AUDIT), exist_ok=True)
        with open(RUTA_AUDIT, 'a', encoding='utf-8') as f:
            f.write(linea + '\n')
    except OSError:
        current_app.logger.warning('No se pudo escribir %s', RUTA_AUDIT)


def _cantidad(valor, campo, minimo=0.0, maximo=NUM_MAX, obligatorio=True):
    if valor in (None, ''):
        if obligatorio:
            raise BajaError(f'Falta completar {campo}')
        return 0.0
    try:
        v = round(float(str(valor).replace(',', '.')), 2)
    except ValueError:
        raise BajaError(f'{campo}: número inválido')
    if v < minimo - EPS:
        raise BajaError(f'{campo} no puede ser menor a {minimo:g}')
    if v > maximo + EPS:
        raise BajaError(f'{campo} supera el máximo permitido')
    return v


def _material(cd1, cd2, bloquear=False):
    cursor_almacenes.execute(
        "SELECT cd1, cd2, material, unidad, marca, minimo, medio, maximo, stock, total, precio, codigobarra, baja "
        "FROM almacenes.materiales WHERE cd1 = %s AND cd2 = %s" + (" FOR UPDATE" if bloquear else ""), (cd1, cd2))
    r = cursor_almacenes.fetchone()
    if not r:
        raise BajaError(f'El material {cd1}-{cd2} no existe', 404)
    return {'cd1': r[0], 'cd2': r[1], 'material': (r[2] or '').strip(), 'unidad': (r[3] or '').strip(),
            'marca': r[4], 'minimo': _num(r[5]), 'medio': _num(r[6]), 'maximo': _num(r[7]),
            'stock': _num(r[8]), 'total': _num(r[9]), 'precio': _num(r[10]),
            'codigobarra': (r[11] or '').strip(), 'baja': r[12], 'de_baja': r[12] == 1}


def _responder(e):
    return jsonify({'ok': False, 'msg': str(e)}), e.status


def _codigo(texto):
    """'AL-24', 'al 0024' → ('AL', '0024') si parece un código cd1-cd2; si no, None."""
    partes = texto.replace('-', ' ').split()
    if len(partes) == 2 and 1 <= len(partes[0]) <= 3 and partes[1].isdigit() and len(partes[1]) <= 4:
        return partes[0].upper(), partes[1].zfill(4)
    return None


# ── Pantalla ──────────────────────────────────────────────────────────────────

@baja_bp.route('/almacenes/baja')
@login_requerido
def panel():
    _conectar()
    cursor_almacenes.execute("""
        SELECT idgrupomaterial, COALESCE(NULLIF(grupomaterial, ''), idgrupomaterial) FROM almacenes.gruposmateriales
        UNION
        SELECT DISTINCT cd1, CONCAT(cd1, ' (sin nombre de grupo)') FROM almacenes.materiales
        WHERE cd1 NOT IN (SELECT idgrupomaterial FROM almacenes.gruposmateriales)
        ORDER BY 1""")
    grupos = [{'id': r[0], 'nombre': r[1]} for r in cursor_almacenes.fetchall()]
    cursor_almacenes.execute("SELECT idunidad, unidad FROM almacenes.unidades ORDER BY unidad")
    unidades = [{'id': r[0], 'nombre': r[1]} for r in cursor_almacenes.fetchall()]
    cursor_almacenes.execute("SELECT idmarca, marca FROM almacenes.marcas ORDER BY marca")
    marcas = [{'id': r[0], 'nombre': r[1]} for r in cursor_almacenes.fetchall()]
    cursor_almacenes.execute("SELECT idjefatura, jefatura FROM comun.jefaturas ORDER BY jefatura")
    sectores = [{'id': r[0], 'nombre': (r[1] or '').strip()} for r in cursor_almacenes.fetchall()]
    return render_template('baja.html', usuario=session.get('usuario', ''), grupos=grupos, unidades=unidades,
                           marcas=marcas, sectores=sectores, nombre_max=NOMBRE_MAX)


# ── Búsqueda (grilla de bajar.exe, con filtros: la tabla tiene 14.600 filas) ──

@baja_bp.route('/almacenes/baja/buscar')
@login_requerido
def buscar():
    _conectar()
    q = (request.args.get('q') or '').strip().upper()
    grupo = (request.args.get('grupo') or '').strip().upper()
    estado = request.args.get('estado', 'todos')
    conds, params = [], []
    cod = _codigo(q)
    if cod:
        conds.append('m.cd1 = %s AND m.cd2 = %s'); params += list(cod)
    else:
        for palabra in q.split():                      # como el FoxPro: cada palabra, AND
            conds.append('m.material LIKE %s'); params.append(f'%{palabra}%')
    if grupo:
        conds.append('m.cd1 = %s'); params.append(grupo)
    if estado == 'activos':
        conds.append('m.baja <> 1')
    elif estado == 'baja':
        conds.append('m.baja = 1')
    if not conds:
        return jsonify({'ok': True, 'filas': [], 'truncado': False,
                        'msg': 'Escriba una palabra, un código o elija un grupo'})
    cursor_almacenes.execute(
        "SELECT m.cd1, m.cd2, m.material, m.unidad, m.stock, m.total, m.baja FROM almacenes.materiales m "
        f"WHERE {' AND '.join(conds)} ORDER BY m.cd1, m.cd2 LIMIT {LIMITE + 1}", params)
    filas = [{'cd1': r[0], 'cd2': r[1], 'material': (r[2] or '').strip(), 'unidad': (r[3] or '').strip(),
              'stock': _num(r[4]), 'total': _num(r[5]), 'baja': r[6], 'de_baja': r[6] == 1}
             for r in cursor_almacenes.fetchall()]
    return jsonify({'ok': True, 'filas': filas[:LIMITE], 'truncado': len(filas) > LIMITE})


@baja_bp.route('/almacenes/baja/material/<cd1>/<cd2>')
@login_requerido
def ficha(cd1, cd2):
    _conectar()
    try:
        return jsonify({'ok': True, 'material': _material(cd1, cd2)})
    except BajaError as e:
        return _responder(e)


# ── Renombrar ─────────────────────────────────────────────────────────────────

@baja_bp.route('/almacenes/baja/renombrar', methods=['POST'])
@login_requerido
def renombrar():
    _conectar()
    d = request.get_json(silent=True) or {}
    try:
        cd1, cd2 = str(d.get('cd1') or '').strip(), str(d.get('cd2') or '').strip()
        nuevo = str(d.get('nuevo') or '').strip()
        if not nuevo:
            raise BajaError('Debe haber algo escrito en la casilla de Detalle')
        if len(nuevo) > NOMBRE_MAX:
            raise BajaError(f'El nombre admite hasta {NOMBRE_MAX} caracteres (tiene {len(nuevo)})')
        actual = _material(cd1, cd2, bloquear=True)
        if str(d.get('actual') or '').strip() != actual['material']:
            raise BajaError('El material fue modificado por otro usuario; vuelva a cargarlo', 409)
        if nuevo == actual['material']:
            raise BajaError('El nombre nuevo es igual al actual')
        cursor_almacenes.execute(
            "UPDATE almacenes.materiales SET material = %s WHERE cd1 = %s AND cd2 = %s", (nuevo, cd1, cd2))
        cursor_almacenes.execute(
            "SELECT cd1, cd2 FROM almacenes.materiales WHERE material = %s AND NOT (cd1 = %s AND cd2 = %s) LIMIT 5",
            (nuevo, cd1, cd2))
        iguales = [f'{r[0]}-{r[1]}' for r in cursor_almacenes.fetchall()]
        conn_almacenes.commit()
        _auditar('renombrar', cd1=cd1, cd2=cd2, antes=actual['material'], despues=nuevo)
        return jsonify({'ok': True, 'msg': f'Material {cd1}-{cd2} renombrado', 'material': nuevo,
                        'aviso': (f'Ya existe otro material con el mismo nombre: {", ".join(iguales)}' if iguales else None)})
    except BajaError as e:
        conn_almacenes.rollback()
        return _responder(e)
    except Exception as e:
        conn_almacenes.rollback()
        current_app.logger.exception('Error al renombrar')
        return jsonify({'ok': False, 'msg': f'No se pudo renombrar: {e}'}), 500


# ── Stock / total / baja ──────────────────────────────────────────────────────

@baja_bp.route('/almacenes/baja/stock', methods=['POST'])
@login_requerido
def stock():
    _conectar()
    d = request.get_json(silent=True) or {}
    try:
        cd1, cd2 = str(d.get('cd1') or '').strip(), str(d.get('cd2') or '').strip()
        orig = d.get('original') or {}
        stock_n = _cantidad(d.get('stock'), 'Stock', 0.0)                      # columna sin signo
        total_n = _cantidad(d.get('total'), 'Total', -NUM_MAX)                 # el total sí admite negativos
        de_baja = bool(d.get('de_baja'))
        motivo = str(d.get('motivo') or '').strip()

        actual = _material(cd1, cd2, bloquear=True)
        for campo in ('stock', 'total'):
            if abs(float(orig.get(campo, 0)) - actual[campo]) > EPS:
                raise BajaError(f'El {campo} de {cd1}-{cd2} cambió ({actual[campo]:g}) desde que lo cargó; '
                                'vuelva a cargarlo para no pisar ese cambio', 409)
        if bool(orig.get('de_baja')) != actual['de_baja']:
            raise BajaError('El estado de baja cambió desde que lo cargó; vuelva a cargarlo', 409)

        cambios, sets, params = {}, [], []
        if abs(stock_n - actual['stock']) > EPS:
            cambios['stock'] = (actual['stock'], stock_n); sets.append('stock = %s'); params.append(stock_n)
        if abs(total_n - actual['total']) > EPS:
            cambios['total'] = (actual['total'], total_n); sets.append('total = %s'); params.append(total_n)
        if de_baja != actual['de_baja']:
            nuevo_baja = 1 if de_baja else 0                                    # reactivar → 0 (el código previo queda en la auditoría)
            cambios['baja'] = (actual['baja'], nuevo_baja); sets.append('baja = %s'); params.append(nuevo_baja)
        if not cambios:
            raise BajaError('No hay cambios para guardar')
        if ('stock' in cambios or 'total' in cambios) and len(motivo) < 3:
            raise BajaError('Indique el motivo del ajuste de stock')

        cursor_almacenes.execute(
            f"UPDATE almacenes.materiales SET {', '.join(sets)} WHERE cd1 = %s AND cd2 = %s", (*params, cd1, cd2))
        resultado = _material(cd1, cd2)                 # se lee dentro de la transacción: nunca falla tras confirmar
        conn_almacenes.commit()
        _auditar('stock', cd1=cd1, cd2=cd2, material=actual['material'], cambios=cambios, motivo=motivo)
        return jsonify({'ok': True, 'msg': f'Material {cd1}-{cd2} actualizado', 'material': resultado})
    except BajaError as e:
        conn_almacenes.rollback()
        return _responder(e)
    except Exception as e:
        conn_almacenes.rollback()
        current_app.logger.exception('Error al ajustar stock')
        return jsonify({'ok': False, 'msg': f'No se pudo actualizar: {e}'}), 500


# ── Alta (Materiales → Subir) + habilitar en sector ──────────────────────────

def _siguiente_cd2(cd1):
    cursor_almacenes.execute(
        "SELECT MAX(CAST(cd2 AS UNSIGNED)) FROM almacenes.materiales WHERE cd1 = %s AND cd2 REGEXP '^[0-9]{4}$'", (cd1,))
    mx = cursor_almacenes.fetchone()[0]
    sig = (mx or 0) + 1
    if sig > 9999:
        raise BajaError(f'El grupo {cd1} ya no tiene códigos libres')
    return str(sig).zfill(4)


@baja_bp.route('/almacenes/baja/nuevo', methods=['POST'])
@login_requerido
def nuevo():
    _conectar()
    d = request.get_json(silent=True) or {}
    try:
        nombre = str(d.get('material') or '').strip().upper()                  # el FoxPro lo graba en mayúsculas
        if not nombre:
            raise BajaError('Debe haber algo escrito en la casilla de Detalle')
        if len(nombre) > NOMBRE_MAX:
            raise BajaError(f'El nombre admite hasta {NOMBRE_MAX} caracteres (tiene {len(nombre)})')
        cd1 = str(d.get('cd1') or '').strip().upper()
        cursor_almacenes.execute(
            "SELECT 1 FROM almacenes.gruposmateriales WHERE idgrupomaterial = %s UNION "
            "SELECT 1 FROM almacenes.materiales WHERE cd1 = %s LIMIT 1", (cd1, cd1))
        if not cd1 or not cursor_almacenes.fetchone():
            raise BajaError('Elija el tipo (grupo) del material')

        unidad = str(d.get('unidad') or '').strip()
        if unidad:
            cursor_almacenes.execute("SELECT 1 FROM almacenes.unidades WHERE idunidad = %s", (unidad,))
            if not cursor_almacenes.fetchone():
                raise BajaError('La unidad elegida no existe')
        marca = d.get('marca')
        marca = int(marca) if str(marca or '').isdigit() and int(marca) > 0 else None
        if marca is not None:
            cursor_almacenes.execute("SELECT 1 FROM almacenes.marcas WHERE idmarca = %s", (marca,))
            if not cursor_almacenes.fetchone():
                raise BajaError('La marca elegida no existe')

        minimo = _cantidad(d.get('minimo'), 'Stock mínimo', 0, obligatorio=False)
        medio  = _cantidad(d.get('medio'),  'Stock medio',  0, obligatorio=False)
        maximo = _cantidad(d.get('maximo'), 'Stock máximo', 0, obligatorio=False)
        stock_i = _cantidad(d.get('stock'), 'Stock', 0, obligatorio=False)
        total_i = _cantidad(d.get('total'), 'Total', -NUM_MAX, obligatorio=False)
        precio = _cantidad(d.get('precio'), 'Precio', 0, PRECIO_MAX, obligatorio=False)
        barras = str(d.get('codigobarra') or '').strip()
        if len(barras) > 20:
            raise BajaError('El código de barras admite hasta 20 caracteres')
        if barras and barras != '0':
            cursor_almacenes.execute(
                "SELECT cd1, cd2 FROM almacenes.materiales WHERE codigobarra = %s LIMIT 1", (barras,))
            r = cursor_almacenes.fetchone()
            if r:
                raise BajaError(f'El código de barras ya pertenece al material {r[0]}-{r[1]}')

        sec = d.get('sector') or None
        if sec:
            try:
                sec_id = int(sec.get('id'))
            except (TypeError, ValueError):
                raise BajaError('Elija el sector donde habilitarlo')
            cursor_almacenes.execute("SELECT 1 FROM comun.jefaturas WHERE idjefatura = %s", (sec_id,))
            if not cursor_almacenes.fetchone():
                raise BajaError('El sector elegido no existe')
            s_min = _cantidad(sec.get('minimo'), 'Mínimo del sector', 0, obligatorio=False)
            s_med = _cantidad(sec.get('medio'),  'Medio del sector',  0, obligatorio=False)
            s_max = _cantidad(sec.get('maximo'), 'Máximo del sector', 0, obligatorio=False)
            s_stk = _cantidad(sec.get('stock'),  'Stock del sector',  0, obligatorio=False)

        auto = bool(d.get('auto', True))
        if not auto:
            digitos = re.sub(r'\D', '', str(d.get('cd2') or ''))
            if not digitos or len(digitos) > 4:
                raise BajaError('El código debe tener hasta 4 dígitos')
            manual = digitos.zfill(4)

        hoy = date.today()
        if not auto:                                   # código manual: avisar con el nombre del que ya lo tiene
            cursor_almacenes.execute(
                "SELECT cd1, cd2, material FROM almacenes.materiales WHERE cd1 = %s AND cd2 = %s", (cd1, manual))
            ya = cursor_almacenes.fetchone()
            if ya:
                raise BajaError(f"El material {ya[0]}-{ya[1]} ya existe ({(ya[2] or '').strip()})", 409)
        for intento in range(5):                       # dos altas simultáneas pueden elegir el mismo número
            cd2 = _siguiente_cd2(cd1) if auto else manual
            try:
                cursor_almacenes.execute(
                    "INSERT INTO almacenes.materiales (cd1, cd2, material, unidad, marca, stockini, minimo, medio, maximo, "
                    "stock, total, precio, fechaprecio, codigobarra, baja, demanda, prioridad) "
                    "VALUES (%s, %s, %s, %s, %s, 0, %s, %s, %s, %s, %s, %s, %s, %s, 0, 0, '')",
                    (cd1, cd2, nombre, unidad or None, marca, minimo, medio, maximo, stock_i, total_i, precio,
                     hoy if precio > 0 else None, barras or None))
                break
            except mysql.connector.IntegrityError as e:
                if e.errno != 1062:
                    raise
                if not auto:
                    raise BajaError(f'El material {cd1}-{cd2} ya existe', 409)
                conn_almacenes.rollback()              # cierra la instantánea: el MAX siguiente verá el alta del otro usuario
        else:
            raise BajaError('No se pudo asignar un código libre; intente nuevamente', 409)

        if sec:
            cursor_almacenes.execute(
                "INSERT INTO almacenes.materialesdesectores (sector, cd1, cd2, stockini, minimo, medio, maximo, stock, "
                "stockant, demanda, precio, fechaprecio, baja, prioridad, obs) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 0, 0, %s, %s, 0, '', '')",
                (sec_id, cd1, cd2, s_stk, s_min, s_med, s_max, s_stk, precio, hoy if precio > 0 else None))
        resultado = _material(cd1, cd2)
        conn_almacenes.commit()
        _auditar('alta', cd1=cd1, cd2=cd2, material=nombre, stock=stock_i, total=total_i,
                 sector=({'id': sec_id, 'stock': s_stk} if sec else None))
        return jsonify({'ok': True, 'msg': f"Producto '{cd1}-{cd2}' subido", 'cd1': cd1, 'cd2': cd2,
                        'material': resultado})
    except BajaError as e:
        conn_almacenes.rollback()
        return _responder(e)
    except Exception as e:
        conn_almacenes.rollback()
        current_app.logger.exception('Error al crear material')
        return jsonify({'ok': False, 'msg': f'Producto NO subido: {e}'}), 500
