# modulos/permisos.py
#
# Modelo de permisos del sistema — réplica de la lógica de los programas FoxPro.
#
# En el FoxPro cada aplicación tiene su propio formulario de ingreso (ingreso.scx) con una
# lista blanca de tipos de operario; el usuario entra si ALGUNO de los tipos que tiene en
# comun.asignaciones está en la lista (si tiene varios, elige con cuál trabaja: tipousr.scx).
# De ese tipo sale el sector (tiposoperarios.idjefatura).
#
#   ALMACENES/ingreso.scx   "A,A0,A1,M0,M1,N0,N1"                     → personal de Almacenes
#   PEDIDOSV/ingreso.scx    lista de sectores + Interior I0..I9       → pedidos de sector
#   permisos.exe            "A,A0,J0,J1,D0,C0" + Interior I0..I7      → autorizaciones
#
# Dentro de "pedidos de sector" (PEDIDOSV/pedidosv.scx, Init):
#   IF VAL(RIGHT(tipo,1)) > 0 AND LEFT(tipo,1) != "I"   &&si no es jefe, solo puede retirar o transferir
#       → deshabilita "Pedido Interno" y "Retiro Materiales P.E."
# O sea: el último dígito del tipo marca el rol (0 = jefe, 1.. = empleado) y los empleados
# solo pueden hacer pedidos de RETIRO; los jefes además PEDIDOS INTERNOS (P.I.M.). Interior (I*)
# siempre es jefe. Los datos lo confirman: en 2 años nadie fuera de esta regla creó un P.I.M.
#
# Nota: el FoxPro compara con AT(tipo, lista) (subcadena), por lo que tipos de una sola letra
# ('G', 'T', 'R') entraban "de casualidad" por ser subcadena de 'G0', 'T0', 'R0'. Acá se compara
# el tipo exacto, que es lo que la lista quiere decir.
#
# Capacidades:
#   almacenes → alguno de sus tipos está en la lista de Almacenes
#   autorizar → alguno de sus tipos está en la lista de autorizaciones
#   retiro    → su tipo ACTIVO está en la lista de pedidos de sector
#   pim       → su tipo ACTIVO está en esa lista y es jefe
#   (retiro y pim dependen del tipo con el que ingresó porque el pedido se hace "como" ese sector)

TIPOS_ALMACENES = frozenset({'A', 'A0', 'A1', 'M0', 'M1', 'N0', 'N1'})

TIPOS_PEDIDOS = frozenset(
    'A,A0,A1,C0,C1,D0,D1,O0,O1,H0,H1,Z0,Z1,L0,L1,E0,R0,R1,B0,B1,B4,F0,F1,M0,M1,S0,P0,P1,N0,N1,U0,U1,G0,T0,V0,V1,K0'
    .split(',')) | frozenset(f'I{i}' for i in range(10))

TIPOS_AUTORIZAR = frozenset({'A', 'A0', 'J0', 'J1', 'D0', 'C0'} | {f'I{i}' for i in range(8)})

TIPOS_CON_FUNCION = TIPOS_ALMACENES | TIPOS_PEDIDOS | TIPOS_AUTORIZAR


def _t(tipo):
    return (tipo or '').strip().upper()


def es_jefe(tipo):
    """Réplica de: NOT (VAL(RIGHT(tipo,1)) > 0 AND LEFT(tipo,1) != "I")."""
    t = _t(tipo)
    if not t:
        return False
    no_jefe = t[-1].isdigit() and int(t[-1]) > 0 and t[0] != 'I'
    return not no_jefe


def capacidades(tipos, tipo_activo=None):
    """Qué puede hacer un usuario con esos tipos asignados y ese tipo activo."""
    tipos = {_t(x) for x in (tipos or [])}
    activo = _t(tipo_activo)
    en_pedidos = activo in TIPOS_PEDIDOS
    return {
        'almacenes': bool(tipos & TIPOS_ALMACENES),
        'autorizar': bool(tipos & TIPOS_AUTORIZAR),
        'retiro':    en_pedidos,
        'pim':       en_pedidos and es_jefe(activo),
    }


def perfiles_de(cursor, id_operario):
    """Perfiles (sector + tipo) con los que puede ingresar y todos sus tipos asignados.

    Un perfil sale de cada tipo asignado cuya lista blanca lo habilita a ALGO. Devuelve
    ([{id, tipo_id, tipo, nombre}], [tipos asignados]).
    """
    cursor.execute("SELECT TRIM(idtipooperario) FROM comun.asignaciones WHERE idoperario = %s", (id_operario,))
    tipos = [r[0] for r in cursor.fetchall()]
    cursor.execute("""
        SELECT DISTINCT t.idjefatura, TRIM(v.idtipooperario), v.tipooperario, j.jefatura
        FROM comun.voperarios v
        JOIN comun.tiposoperarios t ON t.idtipooperario = v.idtipooperario
        JOIN comun.jefaturas j      ON j.idjefatura     = t.idjefatura
        WHERE v.idoperario = %s
        ORDER BY j.jefatura, v.idtipooperario
    """, (id_operario,))
    perfiles = [{'id': r[0], 'tipo_id': r[1], 'tipo': (r[2] or '').strip(), 'nombre': (r[3] or '').strip()}
                for r in cursor.fetchall() if _t(r[1]) in TIPOS_CON_FUNCION]
    return perfiles, tipos
