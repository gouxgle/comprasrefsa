from flask import Blueprint, render_template, session
from conexiones import conn_almacenes, cursor_almacenes, check_connection
from modulos.utils import login_requerido
from datetime import datetime, date

menu_bp = Blueprint('menu_bp', __name__)

MESES = ['','Enero','Febrero','Marzo','Abril','Mayo','Junio',
         'Julio','Agosto','Septiembre','Octubre','Noviembre','Diciembre']
DIAS  = ['Lunes','Martes','Miércoles','Jueves','Viernes','Sábado','Domingo']


@menu_bp.route('/menu_principal')
@login_requerido
def menu_principal():
    global conn_almacenes, cursor_almacenes
    conn_almacenes, cursor_almacenes = check_connection(conn_almacenes, cursor_almacenes, 'almacenes')

    hora = datetime.now().hour
    if hora < 12:
        saludo = 'Buenos días'
    elif hora < 19:
        saludo = 'Buenas tardes'
    else:
        saludo = 'Buenas noches'

    hoy = date.today()
    hoy_str = f"{DIAS[hoy.weekday()]}, {hoy.day} de {MESES[hoy.month]} de {hoy.year}"

    # KPIs
    cursor_almacenes.execute("""
        SELECT
            COUNT(*)                                                  AS total_mes,
            SUM(estado IN (30, 31))                                   AS pendientes,
            SUM(estado = 32)                                          AS entregados,
            SUM(estado IN (30,31) AND DATEDIFF(CURDATE(), fechapedido) > 3) AS criticos
        FROM almacenes.retiromateriales
        WHERE MONTH(fechapedido) = MONTH(CURDATE())
          AND YEAR(fechapedido)  = YEAR(CURDATE())
    """)
    row = cursor_almacenes.fetchone()
    kpi = {
        'total_mes':  int(row[0] or 0),
        'pendientes': int(row[1] or 0),
        'entregados': int(row[2] or 0),
        'criticos':   int(row[3] or 0),
    }

    # Actividad reciente — últimos 8 vales
    cursor_almacenes.execute("""
        SELECT r.idretiro, r.estado, r.fechapedido,
               COALESCE(op.DescOperario, '')  AS quien,
               COALESCE(j.jefatura, '')       AS sector
        FROM almacenes.retiromateriales r
        LEFT JOIN comun.operarios op ON op.IdOperario = r.quienpidio
        LEFT JOIN comun.jefaturas j  ON j.idjefatura  = r.sector
        ORDER BY r.idretiro DESC
        LIMIT 8
    """)
    actividad = [
        {
            'id':     r[0],
            'estado': r[1],
            'fecha':  r[2].strftime('%d/%m') if r[2] else '',
            'quien':  (r[3] or '').strip(),
            'sector': (r[4] or '').strip(),
        }
        for r in cursor_almacenes.fetchall()
    ]

    return render_template(
        'menu.html',
        usuario=session.get('usuario', ''),
        sector_nombre=session.get('sector_nombre', ''),
        tipooperario=session.get('tipooperario', ''),
        saludo=saludo,
        hoy=hoy_str,
        kpi=kpi,
        actividad=actividad,
    )
