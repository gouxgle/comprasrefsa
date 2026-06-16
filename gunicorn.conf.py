import conexiones

bind = "0.0.0.0:8080"
workers = 2
timeout = 300
graceful_timeout = 60
keepalive = 5
worker_class = "sync"
accesslog = "-"
access_log_format = '%(h)s %(r)s %(s)s %(b)sB %(D)sμs'


def post_fork(server, worker):
    """Cada worker crea sus propias conexiones MySQL al nacer (no comparte sockets con otros workers)."""
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
    server.log.info(f"Worker {worker.pid}: conexiones MySQL listas")
