# tools/prueba_humo.py — prueba rápida previa a publicar (solo LECTURA: no graba nada).
#
#   docker exec -w /app almacenes_web python tools/prueba_humo.py
#
# Entra a las pantallas principales con perfiles reales (jefe, empleado, Almacenes, gerencia…)
# y verifica que cada una responda como corresponde según el modelo de permisos
# (modulos/permisos.py): 200 si el perfil tiene la función, redirección/403 si no, y nunca 500.
# Atrapa errores de importación, de plantillas, de SQL y de permisos antes de llegar a producción.
import sys
sys.path.insert(0, '/app')

from app import app
import conexiones
from modulos import permisos as P

# (ruta, función que la habilita): None = cualquier usuario logueado
RUTAS = [
    ('/menu_principal',                          None),
    ('/retiro_materiales',                       'retiro'),
    ('/pedidos.pedido_interno',                  'pim'),
    ('/estado_pedido',                           'pedidos'),
    ('/estado_pedido/detalles?tipo=pim_lista',   'pedidos'),
    ('/imprimir',                                'pedidos'),
    ('/almacenes',                               'almacenes'),
    ('/almacenes/ingreso',                       'almacenes'),
    ('/almacenes/devoluciones',                  'almacenes'),
    ('/almacenes/movimientos',                   'almacenes'),
    ('/almacenes/informes',                      'almacenes'),
    ('/almacenes/baja',                          'almacenes'),
    ('/almacenes/baja/buscar?q=cable',           'almacenes'),
    ('/autorizaciones',                          'autorizar'),
    ('/autorizaciones/lista/pim',                'autorizar'),
]
# usuarios representativos: jefe de sector, empleado, Almacenes jefe y no jefe, secretaría de gerencia
USUARIOS = [114, 21, 871, 78, 704]
HTML = {'Accept': 'text/html'}


def permitido(funcion, caps):
    if funcion is None:
        return True
    if funcion == 'pedidos':
        return caps['retiro'] or caps['pim'] or caps['almacenes']
    return caps[funcion]


def main():
    cur = conexiones.cursor
    cl = app.test_client()
    fallas, total = [], 0

    r = cl.get('/login')
    total += 1
    if r.status_code != 200:
        fallas.append(f'/login → {r.status_code}')

    for uid in USUARIOS:
        cur.execute("SELECT TRIM(DescOperario) FROM comun.operarios WHERE IdOperario = %s", (uid,))
        fila = cur.fetchone()
        if not fila:
            continue
        perfiles, tipos = P.perfiles_de(cur, uid)
        for pf in perfiles[:3]:
            caps = P.capacidades(tipos, pf['tipo_id'])
            with cl.session_transaction() as s:
                s.clear()
                s.update(usuario=fila[0], id=str(uid), id_sector=pf['id'], tipo_id=pf['tipo_id'], tipos=tipos,
                         tipooperario=pf['tipo'], sector_nombre=pf['nombre'])
            for ruta, funcion in RUTAS:
                resp = cl.get(ruta, headers=HTML if 'buscar' not in ruta and 'detalles' not in ruta and 'lista' not in ruta else {})
                total += 1
                esperado_ok = permitido(funcion, caps)
                ok = resp.status_code == 200 if esperado_ok else resp.status_code in (302, 303, 403)
                if not ok:
                    fallas.append(f'{uid} {fila[0]} como {pf["tipo_id"]}/{pf["nombre"]}: {ruta} → {resp.status_code} '
                                  f'(esperaba {"200" if esperado_ok else "redirección/403"})')
    print(f'{total} comprobaciones, {len(fallas)} falla(s)')
    for f in fallas[:25]:
        print('  ✗', f)
    sys.exit(1 if fallas else 0)


if __name__ == '__main__':
    main()
