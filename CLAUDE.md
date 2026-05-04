# CLAUDE.md — Sistema Almacenes REFSA

Contexto del proyecto para Claude Code. Este archivo se carga automáticamente en cada sesión.

---

## Stack técnico

- **Backend:** Flask + mysql-connector-python (cursores directos, sin ORM)
- **Base de datos:** MySQL en `192.168.0.7` — BDs `comun` y `almacenes`
- **Contenedor:** Docker, `container_name=almacenes_web`, `network_mode=host`, puerto 8080
- **Volume mount:** `.:/app` — hot-reload activo para templates y módulos Python sin rebuild
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

- Los blueprints usan `global conn, cursor` + `check_connection()` al inicio de cada ruta
- `conexiones.py` expone `conn`, `cursor` (BD `comun`) y `conn_almacenes`, `cursor_almacenes` (BD `almacenes`)
- Las rutas de retiro y estado importan de `conexiones` directamente sus propias referencias globales
- Formularios usan Bootstrap 5; JS vanilla (sin jQuery ni frameworks)
- Los templates extienden `base.html` con bloques `title`, `extra_css`, `content`, `extra_js`
