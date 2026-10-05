# CLAUDE.md — Sistema Almacenes REFSA

Contexto del proyecto para Claude Code. Este archivo se carga automáticamente en cada sesión.

---

## Stack técnico

- **Backend:** Flask + mysql-connector-python (cursores directos, sin ORM)
- **Base de datos:** MySQL en `192.168.0.7` — BDs `comun` y `almacenes`
- **Contenedor:** Docker, `container_name=almacenes_web`, `network_mode=host`, puerto 8080
- **Volume mount:** `.:/app` — gunicorn (4 workers) cachea templates y módulos: tras cambiar Python **o templates** recargar: `docker exec almacenes_web python -c "import os,signal; os.kill(1, signal.SIGHUP)"` (graceful, sin corte)
- **PDFs:** ReportLab (`SimpleDocTemplate`, `Table`, `Paragraph`, `HRFlowable`, `Image`)
- **Credenciales:** guardadas en `.env` (excluido del repo). Copiar `.env.example` → `.env` para desplegar

---

## Estructura del proyecto

```
app.py                  # Entry point, registra todos los blueprints
conexiones.py           # Pool de conexiones MySQL (conn/cursor para comun y almacenes)
modulos/
  login.py              # Blueprint login_bp — autenticación SHA256
  menu.py               # Blueprint menu_bp — pantalla principal
  pedidos.py            # Blueprint pedidos_bp — Pedidos Internos (PIM)
  retiro.py             # Blueprint retiro_bp — Vales de Retiro de Materiales
  estado.py             # Blueprint estado_bp — consulta estado de pedidos
  imprimir.py           # Blueprint imprimir_bp — generación de PDFs
  almacenes.py          # Blueprint almacenes — pestaña Retiro (autorizar/entregar vales)
  ingreso.py            # Blueprint ingreso — pestaña Ingreso (OC → PIM → stock)
  devoluciones.py       # Blueprint devoluciones — pestaña Devoluciones (Page10 FoxPro)
  movimientos.py        # Blueprint movimientos — pestaña Movimientos, solo consultas (Page11 FoxPro)
  buscar.py             # Blueprint buscar — búsquedas select2 de personal y materiales
  modificar.py          # Blueprint modificar — modificar P.I.M. y vales de retiro ya generados
  autorizaciones.py     # Blueprint autorizaciones — autorización de pedidos firmados (permisos.exe)
  utils.py              # Decorador @login_requerido
templates/              # Jinja2, extienden base.html (Bootstrap 5 + Font Awesome)
static/                 # CSS (style.css) + Logo_REFSA.jpg
```

---

## Sesión de usuario

| Clave | Tipo | Descripción |
|---|---|---|
| `session['usuario']` | str | Nombre de pantalla (DescOperario) |
| `session['id']` | str | Legajo numérico (IdOperario) |
| `session['id_sector']` | int | idjefatura del sector activo |
| `session['sectores']` | list | `[{id, nombre}]` — para usuarios con múltiples sectores |
| `session['pedidos']` | list | Carrito temporal de PIM en curso |

---

## Bases de datos

### `comun`
- `operarios` — login, `IdOperario`, `DescOperario`, clave SHA256
- `jefaturas` — sectores/jefaturas (`idjefatura`, `jefatura`)
- `personal` — legajos de empleados
- `voperarios` — JOIN operarios + tiposoperarios + jefaturas

### `almacenes`
- `pedidosvirtuales` — cabecera PIM
- `detallespedidosvirtuales` — ítems PIM
- `retiromateriales` — cabecera vale de retiro
- `detallesretiromateriales` — ítems retiro
- `materiales` — catálogo (`cd1` char3, `cd2` char4, `material`, `unidad`, `stock`)
- `vmaterialesdesectores` — materiales filtrados por sector
- `autorizaciones` — estados de autorización

---

## Lógica de negocio crítica

### PIM — Pedido Interno de Materiales
Al crear un PIM nuevo, los valores correctos son:

```sql
-- pedidosvirtuales
INSERT ... VALUES (%s, %s, %s, 0, 0, 0, %s, 2, %s)
--                                            ^-- autorizacion=2 (Pend. Gerencia)

-- detallespedidosvirtuales
INSERT ... VALUES (%s, %s, %s, %s, %s, %s, 0, 0, 1, 0, 0, %s)
--                                                  ^-- autorizacion=1 (Pend. Sub-Gerencia)
```

Usar `autorizacion=0` en ambas tablas hace que el PIM **NO aparezca** en la lista de autorización del sistema FoxPro.

### Retiro de Materiales
Al crear un retiro nuevo:
- `estado = 30` ("Pedido Sin Retirar")
- `detallesretiromateriales.autorizacion = 2` al insertar

### Estados relevantes
| Valor | Significado |
|---|---|
| 0 | PIM realizado |
| 9 | PIM dado de baja |
| 30 | Retiro sin retirar (pendiente) |
| 32 | Retiro total |
| 37 | Retiro dado de baja manualmente |

### Jefaturas especiales
| idjefatura | Nombre | Comportamiento especial |
|---|---|---|
| 3 | Distribución | Requiere campo `motivo` en retiro |
| 5 | Zonas | Requiere campo `motivo` en retiro |
| 20 | Oficina Técnica | Requiere campo `motivo` en retiro |
| 27 | Higiene y Seguridad | `cargo=1`, detalles incluyen legajo del empleado |

---

### Ingreso de mercadería (`/almacenes/ingreso`) — réplica de Page2 de `fox/almacenes3.exe`
- Flujo: Proveedor → OC pendientes (`pedidosreales.estado < 23`) → renglón (`detallespedidosreales`) → PIM (`detallespedidosvirtuales.ordendecompra/renglonodc`) → cantidad + comprobante
- Comprobante = `almacenes.comprobantes` (factura/remito por proveedor); en `ingresospedidos*` y `movisproyectosespeciales` se guarda **idcomprobante**
- Estados: 22 = ingresada parcial, 23 = ingresada total (detalle y cabecera de OC y PIM)
- Stock: sector 26 (`almacenes ss`) → `materiales.stock`; otro sector → `materialesdesectores.stock`. `materiales.total` solo se suma si sector=26 (ingreso por renglón) o siempre (OC completa) — igual que FoxPro
- PIM con `idproyectoespecial` → no toca stock; actualiza `detallesproyectosespeciales` + `movisproyectosespeciales`
- Máximo por ingreso = MIN(resta OC, resta PIM). Renglones OC sin PIM no se pueden ingresar
- Diferencias deliberadas con FoxPro: cabecera OC pasa a 22 también en ingresos parciales; "OC completa" rechazada si hubo cualquier ingreso previo; error si falta la fila en `materialesdesectores` (FoxPro perdía el stock en silencio); corregidos estados 32/31 de proyectos especiales y el COUNT sin filtro por PIM

### Devoluciones (`/almacenes/devoluciones`) — réplica de Page10
- Listado = equivalente a vista `vdevoluciones1` (ítems de retiro + SUM devuelto). Se consulta con SQL directo: la vista agrupa 430k filas y tarda 4-8 s. Exige al menos un filtro, máx. 1000 filas
- Devolver: `INSERT devoluciones (retiro, renglon, cantdevuelta, fechadevolucion, quiendevolvio, motivo)`. **No** toca `detallesretiromateriales`; lo devuelto = SUM(devoluciones)
- Destino: Sector → `materialesdesectores.stock` (sector del retiro) + `materiales.total`; Almacén → `materiales.stock`; No reintegrable → nada
- Retiro de proyecto especial → `detallesproyectosespeciales` (cantidadout −, cantidadactual +, estadoout 30/31)
- "Sin orden de retiro" inserta sin retiro/renglón (en FoxPro nunca funcionó: 0 filas en la BD)

### Movimientos (`/almacenes/movimientos`) — réplica de Page11 (solo lectura)
- Consultas definidas en `CONSULTAS` de `movimientos.py`; filtros Sector / Material / Fecha (solo "desde" = ese día; ambas = BETWEEN)
- Stock exige sector; "Con falta entregar" usa `vmatsdesectoressiningresar`
- Ingresos y Salidas son maestro → detalle; Ingresos material y Salidas material muestran totales
- Máx. 3000 filas en pantalla, 50000 en "A Excel" (CSV con `;` y BOM)
- `salidas_material` necesita `STRAIGHT_JOIN` (sin él >10 s y corta por read_timeout)

### Autorizaciones (`/autorizaciones`) — réplica de `fox/permisos.exe`
- **Acceso:** solo operarios con tipo `A, A0, J0, J1, D0, C0, I0–I7` en `comun.asignaciones` (`TIPOS_HABILITADOS`). El ítem del menú superior sale del context processor `puede_autorizar` (cacheado en `session['aut_tipos']`). Con varios tipos elige con cuál trabaja (se propone primero el de gerencia)
- **Tipo → alcance:** `tiposoperarios.idjefatura` = 1 (gerencia) → Pedidos / Retiros / Compras, autoriza con **4**; otra jefatura → Pedidos / Retiros de su subgerencia, autoriza con **3**. `autorizaciones`: 1 req. Sub-Gerencia, 2 req. Gerencia, 3/4 autorizado
- **J1 (secretaría, ej. QUIROS JULIO 703):** debe elegir el gerente J0 que firmó → queda en `autorizadopor`. El FoxPro guardaba el nombre en un campo numérico (quedaba 0); acá se guarda el IdOperario
- **Perfil "solo Autorizaciones":** los IdOperario de `SOLO_AUTORIZACIONES` (en `.env`, por defecto `703`) solo ven y acceden a Autorizaciones — `before_request` en `app.py` redirige toda otra página (Accept `text/html`) y devuelve 403 a las llamadas de datos. Reemplaza el uso del ejecutable independiente `permisos.exe`
- Pedidos: `vpedidosvirtuales1` estado 0 · Retiros: `estado < 2` (con los estados actuales 30/32/39 la lista sale siempre vacía, igual que en FoxPro) · Compras: `vpedidosreales1 idautorizacion < 2`
- Autorizar P.I.M. actualiza cabecera + detalles (+ P.E.); Compra pone `estado = 21` y `fechaautorizado`. El servidor revalida que siga pendiente y en el alcance del usuario

### Modificar P.I.M. y vales de retiro (`modulos/modificar.py`, plantilla `modificar_pedido.html`)
- **P.I.M.** (`/pim/modificar/<id>`, botón en *Estado de Pedidos → Pedido Interno*): solo quien lo generó, con `estado = 0`, **sin autorizar** (autorizacion 0/1/2), sin proyecto especial y sin renglones en el circuito de compras (pedido de precio / O.C. / ingresos / comparativas). Permite cambiar cantidades, cambiar/quitar/agregar materiales (del catálogo del sector del pedido) y editar comentarios. Renumera los renglones 1..n
- **Vale de retiro** (`/retiro/modificar/<id>`, botones en *Estado de Pedidos → Retiro* y *Almacenes → MODIFICAR VALE*): quien lo generó o personal de Almacenes (`puede_almacenes`), vale en estado 30/31 y solo ítems **no entregados** (`estado = 30` y `cantidadretirada = 0`). Reducir/quitar siempre se permite (ajuste al stock real); **subir o agregar** valida el stock disponible (misma cuenta que al crear: stock − pedidos de vales abiertos) y deja el ítem con `autorizacion = 2`. No renumera si hay ítems entregados o referencias en medidores/precintos/devoluciones/cargos/rollos/formularios
- Todo en una transacción con `FOR UPDATE` (el FoxPro usa las mismas tablas); el servidor revalida las reglas al abrir y al guardar. Cada cambio queda en el log de la app (`docker logs almacenes_web`: «P.I.M. N modificado por …»)
- *Estado de Pedidos → Retiro* lista los **2000 ítems más recientes** con consulta directa (la vista `vdetallesretiromateriales2` tardaba >10 s y devolvía 75.000 filas)

## PDFs con ReportLab

### PIM (`/imprimir_pim/<id>`) — A4 apaisado (landscape)
- 3 columnas en cabecera: empresa | título+nro | lugar+fecha
- Tabla de 9 columnas: Item, Cantidad, Existencia, Unid., Codigo, Descripcion, Destino, Fecha Necesidad, PD
- `N_DATA=18` filas, `rowHeights=[18]+[20]*18`
- **Sin INNERGRID** — solo `LINEBELOW` (bajo cabecera) + `LINEAFTER` (separadores verticales)
- Pie: ALMACENES | PREPARO | AUTORIZO | P/COMPRAS | OBSERVACIONES

### Retiro (`/imprimir_retiro/<id>`) — A4 vertical (portrait)
- Logo REFSA (`/app/static/Logo_REFSA.jpg`) + fecha alineada a la derecha
- Cabecera: REFSA bold, N° Orden + Realizada por, Destino + Ubicación, `HRFlowable`, Sector + Operario
- Tabla de 4 columnas: #, Codigo, Material, Cantidad
- `N_DATA=30` filas, `rowHeights=[16]+[18]*30`
- **Sin INNERGRID** — solo `LINEBELOW` + `LINEAFTER`
- Pie: `KeepTogether([Spacer, pie])` — 2 filas: ALMACENES | JEFE | RECIBI CONFORME + firmas

---

## Iniciar / detener el entorno

```bash
# Hay otro contenedor (refsa_web) que ocupa el puerto 8080 — detenerlo primero
docker stop refsa_web

# Iniciar el proyecto correcto
cd /home/sistemas/docker/app_web_flask
docker compose up -d

# Ver logs en tiempo real
docker logs -f almacenes_web
```

> **No confundir** con `/home/sistemas/Descargas/refsa_web_completo/` — es una versión antigua con SQLAlchemy, no se usa.

---

## Convenciones del código

- **`conexiones.py` lo importa el maestro de gunicorn** (`gunicorn.conf.py`): si se modifica, el HUP no alcanza → `docker restart almacenes_web`
- **Transacciones:** mysql-connector trabaja con autocommit apagado; un SELECT deja la transacción abierta. `app.teardown_request` llama a `conexiones.cerrar_transacciones()` (rollback al final de cada request). Sin eso, un worker ocioso retiene el metadata lock y el `ALTER TABLE … AUTO_INCREMENT` que hace el FoxPro (ej. `pedidosvirtuales`) queda esperando → **todo almacenes bloqueado**. Toda escritura debe hacer `commit()` antes de responder
- **Sesión MySQL:** `conexiones._configurar_sesion` fija `lock_wait_timeout = 8` (el global del servidor es 1 año). Sin eso, cada consulta que espera un lock queda huérfana en MySQL cuando el cliente corta a los 10 s, y se agotan las 300 conexiones → caída de todos los sistemas. Errores de MySQL → página `error_bd.html` / JSON 503
- **Producción real:** almacenes.refsa.com.ar corre en la VM **mototrbo (192.168.0.42)**, no en este equipo (deb12sis). Se actualiza con `deploy.sh` (git pull de `main` + rebuild). Sin acceso SSH desde acá
- **Vigía temporal:** contenedor `almacenes_vigia` (`tools/vigia_bloqueos.py`) corta conexiones ociosas de solo lectura de la app de mototrbo cuando hay un metadata lock esperando ≥ 20 s. Quitar (`docker rm -f almacenes_vigia`) cuando producción tenga el hotfix (`tools/hotfix_bloqueo_mysql.patch`)
- Diagnóstico de bloqueos: `information_schema.processlist` (estado "Waiting for table metadata lock") + `information_schema.innodb_trx` (transacciones abiertas hace horas). Todas las conexiones llegan desde 192.168.0.21 (NAT): las de este servidor se ven con `ss -tn '( dport = :3306 )'`

- Los blueprints usan `global conn, cursor` + `check_connection()` al inicio de cada ruta
- `conexiones.py` expone `conn`, `cursor` (BD `comun`) y `conn_almacenes`, `cursor_almacenes` (BD `almacenes`)
- Las rutas de retiro y estado importan de `conexiones` directamente sus propias referencias globales
- Formularios usan Bootstrap 5; JS vanilla (sin jQuery ni frameworks)
- Los templates extienden `base.html` con bloques `title`, `extra_css`, `content`, `extra_js`
