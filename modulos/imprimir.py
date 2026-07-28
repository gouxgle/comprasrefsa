# modulos/imprimir.py
from flask import Blueprint, render_template, session, make_response, request
from conexiones import cursor, check_connection, conn, conn_almacenes, cursor_almacenes
from modulos.utils import login_requerido, puede_almacenes
from io import BytesIO

from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable, Image, KeepTogether
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT

imprimir_bp = Blueprint('imprimir', __name__)

conn, cursor = check_connection(conn, cursor, 'comun')
conn_almacenes, cursor_almacenes = check_connection(conn_almacenes, cursor_almacenes, 'almacenes')


@imprimir_bp.route('/imprimir')
@login_requerido
def imprimir():
    global conn, cursor, conn_almacenes, cursor_almacenes
    conn, cursor = check_connection(conn, cursor, 'comun')
    conn_almacenes, cursor_almacenes = check_connection(conn_almacenes, cursor_almacenes, 'almacenes')

    usuario_id      = session.get('id')
    id_sector       = session.get('id_sector')
    es_almacen      = puede_almacenes()
    solo_pendientes = request.args.get('todos', '0') != '1'

    # Almacén/admin ve todos los PIMs; usuarios regulares ven los suyos + su sector
    if es_almacen:
        cursor_almacenes.execute("""
            SELECT p.idpedidovirtual, p.fecha, COUNT(d.renglon) AS total_items,
                   p.autorizacion AS auth_id, a.autorizacion AS auth_texto
            FROM almacenes.pedidosvirtuales p
            LEFT JOIN almacenes.detallespedidosvirtuales d
                   ON d.idpedidovirtual = p.idpedidovirtual
            LEFT JOIN almacenes.autorizaciones a
                   ON a.idautorizacion = p.autorizacion
            GROUP BY p.idpedidovirtual, p.fecha, p.autorizacion, a.autorizacion
            ORDER BY p.idpedidovirtual DESC
            LIMIT 500
        """)
    else:
        cursor_almacenes.execute("""
            SELECT p.idpedidovirtual, p.fecha, COUNT(d.renglon) AS total_items,
                   p.autorizacion AS auth_id, a.autorizacion AS auth_texto
            FROM almacenes.pedidosvirtuales p
            LEFT JOIN almacenes.detallespedidosvirtuales d
                   ON d.idpedidovirtual = p.idpedidovirtual
            LEFT JOIN almacenes.autorizaciones a
                   ON a.idautorizacion = p.autorizacion
            WHERE p.quienpidio = %s OR p.jefatura = %s
            GROUP BY p.idpedidovirtual, p.fecha, p.autorizacion, a.autorizacion
            ORDER BY p.idpedidovirtual DESC
            LIMIT 200
        """, (usuario_id, id_sector))
    pims = [
        {
            'id':         r[0],
            'fecha':      r[1].strftime('%d/%m/%Y') if r[1] else '',
            'total':      r[2],
            'auth_id':    r[3],
            'auth_texto': r[4] or '',
        }
        for r in cursor_almacenes.fetchall()
    ]

    # Almacén/admin ve todos los retiros; usuarios regulares ven los suyos + su sector
    filtro_estado = "AND r.estado = 30" if solo_pendientes else ""
    if es_almacen:
        cursor_almacenes.execute(f"""
            SELECT r.idretiro, r.fechapedido,
                   COALESCE(op.DescOperario, '') AS nombre_retira,
                   j.jefatura AS sector_nombre,
                   COUNT(d.renglon) AS total_items,
                   r.estado
            FROM almacenes.retiromateriales r
            LEFT JOIN almacenes.detallesretiromateriales d
                   ON d.idretiro = r.idretiro
            LEFT JOIN comun.operarios op
                   ON op.IdOperario = r.quienretiro
            LEFT JOIN comun.jefaturas j
                   ON j.idjefatura = r.destino
            WHERE 1=1 {filtro_estado}
            GROUP BY r.idretiro, r.fechapedido, op.DescOperario, j.jefatura, r.estado
            ORDER BY r.idretiro DESC
            LIMIT 500
        """)
    else:
        cursor_almacenes.execute(f"""
            SELECT r.idretiro, r.fechapedido,
                   COALESCE(op.DescOperario, '') AS nombre_retira,
                   j.jefatura AS sector_nombre,
                   COUNT(d.renglon) AS total_items,
                   r.estado
            FROM almacenes.retiromateriales r
            LEFT JOIN almacenes.detallesretiromateriales d
                   ON d.idretiro = r.idretiro
            LEFT JOIN comun.operarios op
                   ON op.IdOperario = r.quienretiro
            LEFT JOIN comun.jefaturas j
                   ON j.idjefatura = r.destino
            WHERE (r.quienpidio = %s OR r.sector = %s) {filtro_estado}
            GROUP BY r.idretiro, r.fechapedido, op.DescOperario, j.jefatura, r.estado
            ORDER BY r.idretiro DESC
            LIMIT 200
        """, (usuario_id, id_sector))
    retiros = [
        {
            'id':      r[0],
            'fecha':   r[1].strftime('%d/%m/%Y') if r[1] else '',
            'retira':  r[2],
            'sector':  r[3] or '',
            'total':   r[4],
            'estado':  r[5],
        }
        for r in cursor_almacenes.fetchall()
    ]

    return render_template(
        'imprimir.html',
        usuario=session['usuario'],
        pims=pims,
        retiros=retiros,
        solo_pendientes=solo_pendientes,
    )


def _build_pim_pdf(id_pedido):
    """Genera el PDF de un PIM en A4 apaisado replicando el formato FoxPro."""
    global conn, cursor
    conn, cursor = check_connection(conn, cursor, 'comun')

    # ── Cabecera del pedido ──────────────────────────────────
    cursor.execute("""
        SELECT p.idpedidovirtual, p.fecha, p.quienpidio, p.jefatura,
               o.DescOperario
        FROM almacenes.pedidosvirtuales p
        LEFT JOIN comun.operarios o ON o.IdOperario = p.quienpidio
        WHERE p.idpedidovirtual = %s
    """, (id_pedido,))
    cab = cursor.fetchone()
    if not cab:
        return None

    id_pim, fecha_pedido, quienpidio, jefatura, desc_operario = cab

    # ── Ítems del pedido ─────────────────────────────────────
    cursor.execute("""
        SELECT d.renglon, d.cd1, d.cd2, d.cantidad,
               COALESCE(m.material, CONCAT(d.cd1,'-',d.cd2)) AS material,
               COALESCE(m.unidad, 'C/U') AS unidad,
               d.destino
        FROM almacenes.detallespedidosvirtuales d
        LEFT JOIN almacenes.materiales m ON m.cd1 = d.cd1 AND m.cd2 = d.cd2
        WHERE d.idpedidovirtual = %s
        ORDER BY d.renglon
    """, (id_pedido,))
    items = cursor.fetchall()

    # ── Construcción PDF ─────────────────────────────────────
    buf = BytesIO()

    def st(name, size=7, align=TA_LEFT, bold=False):
        fn = 'Courier-Bold' if bold else 'Courier'
        return ParagraphStyle(name, fontName=fn, fontSize=size,
                              alignment=align, leading=size + 2)

    mono7  = st('pm7')
    tdc    = st('ptdc', align=TA_CENTER)
    tdl    = st('ptdl')
    th     = st('pth',  align=TA_CENTER, bold=True)

    doc = SimpleDocTemplate(buf, pagesize=landscape(A4),
                            leftMargin=1.5*cm, rightMargin=1.5*cm,
                            topMargin=1.5*cm, bottomMargin=1.5*cm)
    W = doc.width   # ~756 pt (≈26.7 cm) en A4 apaisado

    fecha_str = fecha_pedido.strftime('%d/%m/%Y') if fecha_pedido else ''

    # ── Encabezado (3 columnas) ───────────────────────────────
    empresa = Paragraph(
        'R.E.F.S.A.<br/>Av. Gorleri 580<br/>(P3600CAS)FORMOSA CAPITAL<br/>'
        'TEL/FAX (+54-370) 443-9800<br/>C.U.I.T. 30-70.966.875-3',
        mono7)

    titulo = Paragraph(
        '<b>PEDIDO DE MATERIALES</b><br/><br/>'
        f'<b><font size="14">Nro.:&nbsp;&nbsp;&nbsp;&nbsp;{id_pim}</font></b>',
        ParagraphStyle('ptit', fontName='Courier-Bold', fontSize=11,
                       alignment=TA_CENTER, leading=14))

    # Caja derecha: "Lugar y Fecha" arriba + "Hoja Nº:" arriba-derecha + localidad/fecha abajo
    rw = W * 0.28 - 8   # ancho disponible dentro de la celda derecha
    right_inner = Table([
        [Paragraph('<b>Lugar y Fecha</b>',
                   ParagraphStyle('plf', fontName='Courier-Bold', fontSize=7,
                                  alignment=TA_LEFT, leading=9)),
         Paragraph(f'<b>Hoja Nº:</b>&nbsp;&nbsp;1/1',
                   ParagraphStyle('phn', fontName='Courier-Bold', fontSize=7,
                                  alignment=TA_RIGHT, leading=9))],
        [Paragraph('FORMOSA',
                   ParagraphStyle('ploc', fontName='Courier', fontSize=8,
                                  alignment=TA_CENTER, leading=10)), ''],
        [Paragraph(fecha_str,
                   ParagraphStyle('pfec', fontName='Courier', fontSize=8,
                                  alignment=TA_CENTER, leading=10)), ''],
    ], colWidths=[rw * 0.58, rw * 0.42])
    right_inner.setStyle(TableStyle([
        ('SPAN',         (0, 1), (1, 1)),
        ('SPAN',         (0, 2), (1, 2)),
        ('LINEBELOW',    (0, 0), (1, 0), 0.4, colors.black),
        ('TOPPADDING',   (0, 0), (-1, -1), 1),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 1),
        ('LEFTPADDING',  (0, 0), (-1, -1), 2),
        ('RIGHTPADDING', (0, 0), (-1, -1), 2),
    ]))

    encabezado = Table(
        [[empresa, titulo, right_inner]],
        colWidths=[W * 0.32, W * 0.40, W * 0.28]
    )
    encabezado.setStyle(TableStyle([
        ('BOX',          (0, 0), (0, 0), 0.75, colors.black),
        ('BOX',          (1, 0), (1, 0), 0.75, colors.black),
        ('BOX',          (2, 0), (2, 0), 0.75, colors.black),
        ('VALIGN',       (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING',   (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 5),
        ('LEFTPADDING',  (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
    ]))

    # ── Tabla de ítems ────────────────────────────────────────
    # Item | Cantidad | Existencia | Unid. | Codigo | Descripcion | Destino | Fecha Necesidad | PD
    cw = [W*0.04, W*0.07, W*0.07, W*0.05, W*0.10, W*0.44, W*0.09, W*0.09, W*0.05]
    filas = [[
        Paragraph('Item',             th),
        Paragraph('Cantidad',         th),
        Paragraph('Existencia',       th),
        Paragraph('Unid.',            th),
        Paragraph('Codigo',           th),
        Paragraph('Descripcion',      th),
        Paragraph('Destino',          th),
        Paragraph('Fecha\nNecesidad', th),
        Paragraph('PD',               th),
    ]]

    pd_str = str(quienpidio) if quienpidio else ''
    for renglon, cd1, cd2, cantidad, material, unidad, destino in items:
        cant_fmt = f'{float(cantidad):.2f}'.replace('.', ',')
        filas.append([
            Paragraph(str(renglon),              tdc),
            Paragraph(cant_fmt,                  tdc),
            Paragraph('',                        tdc),
            Paragraph(unidad or '',              tdc),
            Paragraph(f'{cd1}-{cd2}',            tdc),
            Paragraph(material,                  tdl),
            Paragraph(str(destino) if destino else '', tdc),
            Paragraph('',                        tdc),
            Paragraph(pd_str,                    tdc),
        ])

    N_DATA = 18
    while len(filas) < N_DATA + 1:
        filas.append([Paragraph('', tdc)] * 9)

    # Alturas fijas para llenar la página: encabezado de tabla + N_DATA filas de datos
    # A4 apaisado: ~510pt disponibles − encabezado (~65pt) − spacers (17pt) − pie (~50pt) ≈ 378pt
    # 1 fila header (18pt) + 18 filas datos (20pt) = 18 + 360 = 378pt ✓
    rh = [18] + [20] * N_DATA
    tabla_items = Table(filas, colWidths=cw, rowHeights=rh, repeatRows=1)
    tabla_items.setStyle(TableStyle([
        ('BOX',          (0, 0), (-1, -1), 0.75, colors.black),
        ('LINEBELOW',    (0, 0), (-1,  0), 0.75, colors.black),   # borde bajo cabecera
        ('LINEAFTER',    (0, 0), (7,  -1), 0.4,  colors.black),   # separadores verticales
        ('FONTNAME',     (0, 0), (-1,  0), 'Courier-Bold'),
        ('ALIGN',        (0, 0), (-1, -1), 'CENTER'),
        ('ALIGN',        (5, 1), (5,  -1), 'LEFT'),
        ('VALIGN',       (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING',   (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 2),
        ('LEFTPADDING',  (0, 0), (-1, -1), 2),
        ('RIGHTPADDING', (0, 0), (-1, -1), 2),
    ]))

    # ── Pie de firmas ─────────────────────────────────────────
    pie_s   = ParagraphStyle('pps',  fontName='Courier-Bold', fontSize=7,
                             alignment=TA_CENTER, leading=10)
    pie_obs = ParagraphStyle('ppo',  fontName='Courier',      fontSize=7,
                             alignment=TA_LEFT,   leading=10)

    pie = Table([[
        Paragraph('ALMACENES<br/>FECHA<br/>/ /',  pie_s),
        Paragraph('PREPARO<br/>FECHA<br/>/ /',    pie_s),
        Paragraph('AUTORIZO<br/>FECHA<br/>/ /',   pie_s),
        Paragraph('P/COMPRAS<br/>FECHA<br/>/ /',  pie_s),
        Paragraph(
            'OBSERVACIONES: ......................................<br/>'
            '.....................................................<br/>'
            '.....................................................',
            pie_obs),
    ]], colWidths=[W*0.12, W*0.12, W*0.12, W*0.12, W*0.52])
    pie.setStyle(TableStyle([
        ('BOX',          (0, 0), (-1, -1), 0.75, colors.black),
        ('INNERGRID',    (0, 0), (-1, -1), 0.5,  colors.black),
        ('VALIGN',       (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING',   (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 4),
        ('LEFTPADDING',  (0, 0), (-1, -1), 3),
        ('RIGHTPADDING', (0, 0), (-1, -1), 3),
    ]))

    doc.build([encabezado, Spacer(1, 0.3*cm), tabla_items, Spacer(1, 0.3*cm), pie])
    buf.seek(0)
    return buf


@imprimir_bp.route('/imprimir_pim/<int:id_pedido>')
@login_requerido
def imprimir_pim(id_pedido):
    buf = _build_pim_pdf(id_pedido)
    if buf is None:
        return "Pedido no encontrado", 404
    nro = str(id_pedido).zfill(4)
    response = make_response(buf.read())
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'inline; filename=PIM-{nro}.pdf'
    return response


def _build_retiro_pdf(id_retiro):
    """Genera el PDF de un Vale de Retiro en A4 vertical replicando el formato FoxPro."""
    global conn, cursor, conn_almacenes, cursor_almacenes
    conn, cursor = check_connection(conn, cursor, 'comun')
    conn_almacenes, cursor_almacenes = check_connection(conn_almacenes, cursor_almacenes, 'almacenes')

    cursor_almacenes.execute("""
        SELECT r.idretiro, r.fechapedido,
               r.ubicacion, r.detalles,
               j1.jefatura AS sector_nombre,
               j2.jefatura AS destino_nombre,
               op_pide.DescOperario AS realizada_por,
               per.nombre AS operario
        FROM almacenes.retiromateriales r
        LEFT JOIN comun.jefaturas j1  ON j1.idjefatura   = r.sector
        LEFT JOIN comun.jefaturas j2  ON j2.idjefatura   = r.destino
        LEFT JOIN comun.operarios op_pide ON op_pide.IdOperario = r.quienpidio
        LEFT JOIN comun.personal  per     ON per.idlegajo       = r.quienretiro
        WHERE r.idretiro = %s
    """, (id_retiro,))
    cab = cursor_almacenes.fetchone()
    if not cab:
        return None

    id_ret, fecha_ret, ubicacion, detalles, sector_nombre, destino_nombre, realizada_por, operario = cab

    cursor_almacenes.execute("""
        SELECT d.renglon, d.cd1, d.cd2, d.cantidadpedida,
               COALESCE(m.material, CONCAT(d.cd1,' ',d.cd2)) AS material
        FROM almacenes.detallesretiromateriales d
        LEFT JOIN almacenes.materiales m ON m.cd1 = d.cd1 AND m.cd2 = d.cd2
        WHERE d.idretiro = %s
        ORDER BY d.renglon
    """, (id_retiro,))
    items = cursor_almacenes.fetchall()

    buf = BytesIO()

    def st(name, size=10, align=TA_LEFT, bold=False):
        fn = 'Courier-Bold' if bold else 'Courier'
        return ParagraphStyle(name, fontName=fn, fontSize=size,
                              alignment=align, leading=size + 3)

    mono8  = st('vm8')
    mono8r = st('vm8r', align=TA_RIGHT)
    th     = st('vth',  align=TA_CENTER, bold=True)
    tdc    = st('vtdc', align=TA_CENTER)
    tdl    = st('vtdl')
    tdr    = st('vtdr', align=TA_RIGHT)

    _np = TableStyle([                            # sin relleno
        ('TOPPADDING',    (0, 0), (-1, -1), 1),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
        ('LEFTPADDING',   (0, 0), (-1, -1), 0),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 0),
    ])

    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=1.5*cm, rightMargin=1.5*cm,
                            topMargin=1.5*cm, bottomMargin=1.5*cm)
    W = doc.width   # ~510pt en A4 vertical

    fecha_str    = fecha_ret.strftime('%d/%m/%Y') if fecha_ret else ''
    sector_str   = (sector_nombre  or '').lower()
    destino_str  = (destino_nombre or '')
    ubic_str     = (ubicacion      or '').upper()
    realiza_str  = (realizada_por  or '').strip().upper()
    operario_str = (operario       or '').strip().upper()

    # ── Bloque superior: logo + Fecha ────────────────────────────────────────
    try:
        logo = Image('/app/static/Logo_REFSA.jpg', height=28, width=28)
    except Exception:
        logo = Paragraph('', mono8)

    hdr_top = Table(
        [[logo, Paragraph(f'Fecha:{fecha_str}', mono8r)]],
        colWidths=[W * 0.65, W * 0.35]
    )
    hdr_top.setStyle(_np)

    hdr_refsa = Paragraph(
        '<b>REFSA</b>',
        ParagraphStyle('vrefsa', fontName='Courier-Bold', fontSize=13, leading=16))

    # ── Líneas de info ────────────────────────────────────────────────────────
    orden_row = Table([[
        Paragraph(f'N° Orden de Retiro de Materiales:   {id_ret}', mono8),
        Paragraph(f'Realizada por:{realiza_str}', mono8),
    ]], colWidths=[W * 0.52, W * 0.48])
    orden_row.setStyle(_np)

    dest_row = Table([[
        Paragraph(f'Destino: {destino_str}', mono8),
        Paragraph(f'Ubicacion: {ubic_str}', mono8),
    ]], colWidths=[W * 0.52, W * 0.48])
    dest_row.setStyle(_np)

    sector_row = Table([[
        Paragraph(f'Sector: {sector_str}', mono8),
        Paragraph(f'Operario: {operario_str}', mono8),
    ]], colWidths=[W * 0.52, W * 0.48])
    sector_row.setStyle(_np)

    hr_flow = HRFlowable(width='100%', thickness=0.75, color=colors.black,
                         spaceBefore=2, spaceAfter=2)
    spacer_pre_tabla = Spacer(1, 0.12*cm)
    spacer_pre_pie   = Spacer(1, 0.12*cm)

    # ── Pie de firmas (se arma antes para poder medir su altura real) ──────────
    pie_s = ParagraphStyle('vps', fontName='Courier-Bold', fontSize=10,
                           alignment=TA_CENTER, leading=13)
    pie_f = ParagraphStyle('vpf', fontName='Courier',      fontSize=9,
                           alignment=TA_LEFT,   leading=12)

    pie = Table([
        [Paragraph('ALMACENES',      pie_s),
         Paragraph('JEFE',           pie_s),
         Paragraph('RECIBI CONFORME',pie_s)],
        [Paragraph('Firma.........................<br/>Aclaracion...................', pie_f),
         Paragraph('Firma.........................<br/>Aclaracion...................', pie_f),
         Paragraph('Firma.........................<br/>Aclaracion...................', pie_f)],
    ], colWidths=[W / 3, W / 3, W / 3], rowHeights=[22, 40])
    pie.setStyle(TableStyle([
        ('BOX',          (0, 0), (-1, -1), 0.75, colors.black),
        ('LINEAFTER',    (0, 0), (1,  -1), 0.5,  colors.black),
        ('LINEBELOW',    (0, 0), (-1,  0), 0.5,  colors.black),
        ('VALIGN',       (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING',   (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 4),
        ('LEFTPADDING',  (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
    ]))

    # ── Tabla de ítems ────────────────────────────────────────────────────────
    # Cols: # | Codigo | Material | Cantidad
    cw = [W * 0.05, W * 0.12, W * 0.71, W * 0.12]
    BASE_H  = 18   # alto fijo de fila de 1 línea (cabecera, ítems cortos y relleno)
    PAD_V   = 4    # TOPPADDING + BOTTOMPADDING de la tabla (2 + 2)
    MAT_W   = cw[2] - 6   # ancho útil col. Material (padding izq+der = 3+3)

    filas = [[
        Paragraph('#',        th),
        Paragraph('Codigo',   th),
        Paragraph('Material', th),
        Paragraph('Cantidad', th),
    ]]

    item_heights = []
    for renglon, cd1, cd2, cant_ped, material in items:
        cant_fmt = f'{float(cant_ped):.2f}'.replace('.', ',')
        p_mat = Paragraph(material, tdl)
        _, h_mat = p_mat.wrap(MAT_W, 10000)   # altura real necesaria (1 o 2+ líneas)
        item_heights.append(max(BASE_H, h_mat + PAD_V))
        filas.append([
            Paragraph(str(renglon),   tdc),
            Paragraph(f'{cd1} {cd2}', tdc),
            p_mat,
            Paragraph(cant_fmt,       tdr),
        ])
    n_items = len(item_heights)

    # Medir el espacio real disponible en la página para la tabla de ítems
    # (resto de la hoja descontando cabecera y pie ya armados) — así el
    # formulario siempre ocupa la hoja completa, con relleno de alto fijo.
    used_h = 0.0
    for flw in (hdr_top, hdr_refsa, orden_row, dest_row, hr_flow, sector_row, spacer_pre_tabla):
        used_h += flw.wrap(W, 10000)[1]
    _, pie_h = pie.wrap(W, 10000)
    used_h += spacer_pre_pie.wrap(W, 10000)[1] + pie_h

    # doc.height no incluye el padding interno por defecto del Frame de
    # SimpleDocTemplate (6pt arriba + 6pt abajo) — se descuenta + margen de seguridad.
    disponible   = doc.height - used_h - 14
    filler_count = max(0, int((disponible - BASE_H - sum(item_heights)) // BASE_H))
    for _ in range(filler_count):
        filas.append([Paragraph('', tdc)] * 4)
        item_heights.append(BASE_H)

    rh = [BASE_H] + item_heights
    tabla = Table(filas, colWidths=cw, rowHeights=rh, repeatRows=1)
    tabla.setStyle(TableStyle([
        ('BOX',          (0, 0), (-1, -1), 0.75, colors.black),
        ('LINEBELOW',    (0, 0), (-1,  0), 0.75, colors.black),          # borde bajo cabecera
        ('LINEBELOW',    (0, 1), (-1, n_items), 0.4, colors.black),      # separador solo entre renglones con texto
        ('LINEAFTER',    (0, 0), (2,  -1), 0.4,  colors.black),          # separadores verticales
        ('FONTNAME',     (0, 0), (-1,  0), 'Courier-Bold'),
        ('ALIGN',        (0, 0), (1,  -1), 'CENTER'),
        ('ALIGN',        (2, 1), (2,  -1), 'LEFT'),
        ('ALIGN',        (3, 0), (3,  -1), 'RIGHT'),
        ('VALIGN',       (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING',   (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 2),
        ('LEFTPADDING',  (0, 0), (-1, -1), 3),
        ('RIGHTPADDING', (0, 0), (-1, -1), 3),
    ]))

    doc.build([
        hdr_top,
        hdr_refsa,
        orden_row,
        dest_row,
        hr_flow,
        sector_row,
        spacer_pre_tabla,
        tabla,
        KeepTogether([spacer_pre_pie, pie]),
    ])
    buf.seek(0)
    return buf


@imprimir_bp.route('/imprimir_retiro/<int:id_retiro>')
@login_requerido
def imprimir_retiro(id_retiro):
    buf = _build_retiro_pdf(id_retiro)
    if buf is None:
        return "Retiro no encontrado", 404
    nro = str(id_retiro).zfill(4)
    response = make_response(buf.read())
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'inline; filename=Retiro-{nro}.pdf'
    return response


@imprimir_bp.route('/imprimir_popup/retiro/<int:id_retiro>')
@login_requerido
def imprimir_popup_retiro(id_retiro):
    """Vale de retiro como HTML con CSS de impresión. window.print() funciona
    100% sobre HTML a diferencia del embed PDF."""
    global conn, cursor, conn_almacenes, cursor_almacenes
    conn, cursor = check_connection(conn, cursor, 'comun')
    conn_almacenes, cursor_almacenes = check_connection(conn_almacenes, cursor_almacenes, 'almacenes')

    cursor_almacenes.execute("""
        SELECT r.idretiro, r.fechapedido,
               r.ubicacion,
               j1.jefatura AS sector_nombre,
               j2.jefatura AS destino_nombre,
               op_pide.DescOperario AS realizada_por,
               per.nombre AS operario
        FROM almacenes.retiromateriales r
        LEFT JOIN comun.jefaturas j1     ON j1.idjefatura  = r.sector
        LEFT JOIN comun.jefaturas j2     ON j2.idjefatura  = r.destino
        LEFT JOIN comun.operarios op_pide ON op_pide.IdOperario = r.quienpidio
        LEFT JOIN comun.personal  per     ON per.idlegajo       = r.quienretiro
        WHERE r.idretiro = %s
    """, (id_retiro,))
    cab = cursor_almacenes.fetchone()
    if not cab:
        return "Vale no encontrado", 404

    id_ret, fecha_ret, ubicacion, sector_nombre, destino_nombre, realizada_por, operario = cab

    cursor_almacenes.execute("""
        SELECT d.renglon, d.cd1, d.cd2, d.cantidadpedida,
               COALESCE(m.material, CONCAT(d.cd1,' ',d.cd2)) AS material
        FROM almacenes.detallesretiromateriales d
        LEFT JOIN almacenes.materiales m ON m.cd1 = d.cd1 AND m.cd2 = d.cd2
        WHERE d.idretiro = %s
        ORDER BY d.renglon
    """, (id_retiro,))
    items = cursor_almacenes.fetchall()

    fecha_str   = fecha_ret.strftime('%d/%m/%Y') if fecha_ret else ''
    sector_str  = (sector_nombre  or '').upper()
    destino_str = (destino_nombre or '').upper()
    ubic_str    = (ubicacion      or '').upper()
    quien_str   = (realizada_por  or '').upper()
    operario_str_h = (operario   or '').upper()

    filas_html = ''
    for renglon, cd1, cd2, cant_ped, material in items:
        cant_fmt = f'{float(cant_ped):.2f}'.replace('.', ',')
        filas_html += f'''
        <tr class="item-row">
          <td class="tc">{renglon}</td>
          <td class="tc">{cd1} {cd2}</td>
          <td class="tl">{material}</td>
          <td class="tr">{cant_fmt}</td>
        </tr>'''

    # Rellenar hasta 25 filas para mantener el layout de página
    # (sin separador de línea — solo se marca entre renglones con texto)
    filled = len(items)
    for _ in range(max(0, 25 - filled)):
        filas_html += '<tr class="filler-row"><td>&nbsp;</td><td></td><td></td><td></td></tr>'

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<title>Retiro {id_ret}</title>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}

  body {{
    font-family: 'Courier New', Courier, monospace;
    font-size: 12pt;
    background: #e0e0e0;
    display: flex; flex-direction: column; align-items: center;
    padding: 16px;
  }}

  /* hoja A4 en pantalla */
  .page {{
    background: #fff;
    width: 210mm; min-height: 297mm;
    padding: 14mm 14mm 12mm;
    box-shadow: 0 2px 12px rgba(0,0,0,.25);
    position: relative;
  }}

  /* ── cabecera ── */
  .hdr-top {{ display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:2mm; }}
  .hdr-fecha {{ font-size:11pt; text-align:right; }}
  .refsa-title {{ font-size:15pt; font-weight:bold; margin:1mm 0; }}
  .info-row {{ display:flex; justify-content:space-between; margin-bottom:1mm; font-size:11pt; }}
  hr {{ border:none; border-top:1px solid #000; margin:2mm 0; }}

  /* ── tabla ── */
  table {{ width:100%; border-collapse:collapse; margin-top:2mm; }}
  thead tr th {{
    border:1px solid #000; padding:3px 4px;
    font-size:11pt; font-weight:bold; text-align:center;
    background:#f0f0f0;
  }}
  tbody tr td {{
    border-left:1px solid #000; border-right:1px solid #000;
    padding:3px 4px; font-size:11pt; height:8mm;
  }}
  tbody tr.item-row td {{ border-bottom:1px solid #000; }}   /* separador solo entre renglones con texto */
  tbody tr:last-child td {{ border-bottom:1px solid #000; }} /* cierre inferior de la tabla */
  .tc {{ text-align:center; }}
  .tl {{ text-align:left; }}
  .tr {{ text-align:right; }}

  /* ── firmas ── */
  .firmas {{ display:flex; border:1px solid #000; margin-top:4mm; }}
  .firma-col {{
    flex:1; padding:3mm 4mm;
    border-right:1px solid #000;
    font-size:11pt;
  }}
  .firma-col:last-child {{ border-right:none; }}
  .firma-titulo {{ font-weight:bold; text-align:center; margin-bottom:6mm; }}
  .firma-linea {{ border-top:1px solid #000; margin-top:1mm; font-size:9pt; color:#555; }}

  /* ── barra de estado (solo pantalla) ── */
  #status-bar {{
    position:fixed; top:0; left:0; right:0;
    background:#1565c0; color:#fff;
    text-align:center; padding:8px;
    font:bold 13px sans-serif; z-index:999;
    font-family: sans-serif;
  }}

  /* ── print ── */
  @media print {{
    body {{ background:none; padding:0; }}
    #status-bar {{ display:none !important; }}
    .page {{
      box-shadow:none; width:100%; min-height:auto;
      padding:8mm 10mm;
    }}
    thead tr th {{ background:#f0f0f0 !important; -webkit-print-color-adjust:exact; }}
  }}
</style>
</head>
<body>

<div id="status-bar">Enviando a impresora…</div>

<div class="page">

  <!-- cabecera -->
  <div class="hdr-top">
    <img src="/static/Logo_REFSA.jpg" height="32" style="border-radius:3px">
    <div class="hdr-fecha">Fecha: {fecha_str}</div>
  </div>

  <div class="refsa-title">REFSA</div>

  <div class="info-row">
    <span>N° Orden de Retiro de Materiales: &nbsp;<strong>{id_ret}</strong></span>
    <span>Realizada por: {quien_str}</span>
  </div>
  <div class="info-row">
    <span>Destino: {destino_str}</span>
    <span>Ubicacion: {ubic_str}</span>
  </div>

  <hr>

  <div class="info-row">
    <span>Sector: {sector_str}</span>
    <span>Operario: {operario_str_h}</span>
  </div>

  <!-- tabla -->
  <table>
    <thead>
      <tr>
        <th style="width:5%">#</th>
        <th style="width:12%">Codigo</th>
        <th style="width:71%">Material</th>
        <th style="width:12%">Cantidad</th>
      </tr>
    </thead>
    <tbody>
      {filas_html}
    </tbody>
  </table>

  <!-- firmas -->
  <div class="firmas">
    <div class="firma-col">
      <div class="firma-titulo">ALMACENES</div>
      <div class="firma-linea">Firma: ____________________</div>
      <div class="firma-linea">Aclaración: _______________</div>
    </div>
    <div class="firma-col">
      <div class="firma-titulo">JEFE</div>
      <div class="firma-linea">Firma: ____________________</div>
      <div class="firma-linea">Aclaración: _______________</div>
    </div>
    <div class="firma-col">
      <div class="firma-titulo">RECIBI CONFORME</div>
      <div class="firma-linea">Firma: ____________________</div>
      <div class="firma-linea">Aclaración: _______________</div>
    </div>
  </div>

</div><!-- /page -->

<script>
window.addEventListener('load', function() {{
  window.focus();
  setTimeout(function() {{
    window.print();
    window.onafterprint = function() {{ window.close(); }};
  }}, 400);
}});
</script>
</body>
</html>"""

    response = make_response(html)
    response.headers['Content-Type'] = 'text/html; charset=utf-8'
    return response
