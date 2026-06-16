import conexiones

bind = "0.0.0.0:8080"
workers = 2
timeout = 300
keepalive = 5
worker_class = "sync"


def post_fork(server, worker):
    """Reinicializar conexiones MySQL después del fork de cada worker.
    Evita que workers compartan el mismo socket TCP hacia MySQL."""
    try:
        conexiones.conn.close()
    except Exception:
        pass
    try:
        conexiones.conn_almacenes.close()
    except Exception:
        pass
    conexiones.conn, conexiones.cursor = conexiones.get_connection("comun")
    conexiones.conn_almacenes, conexiones.cursor_almacenes = conexiones.get_connection("almacenes")
    server.log.info(f"Worker {worker.pid}: conexiones MySQL reinicializadas")
