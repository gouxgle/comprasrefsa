FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Instala dependencias del sistema necesarias para mysqlclient
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        default-libmysqlclient-dev \
        python3-dev \
        pkg-config \
        libmariadb-dev \
        libmariadb-dev-compat && \
    rm -rf /var/lib/apt/lists/*

# Copiá primero solo el archivo de dependencias para aprovechar la caché
COPY requirements.txt .

# Instalá los paquetes de Python
RUN pip install --no-cache-dir -r requirements.txt

# Copiá el resto del código
COPY . .

EXPOSE 8080

CMD ["python", "app.py"]

