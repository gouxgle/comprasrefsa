INSTRUCCIONES DE USO - MIGRACIÓN FoxPro a Flask + Docker

1. Requisitos:
   - Docker y docker-compose instalados

2. Archivos:
   - app.py: lógica de la aplicación Flask
   - templates/formprin.html: reemplazo del formulario principal
   - Dockerfile: define cómo construir la imagen
   - docker-compose.yml: configuración para levantar la app y conectarse a MySQL remoto

3. Configuración:
   - Editá 'MYSQL_HOST' en docker-compose.yml con la IP o hostname de tu servidor MySQL

4. Para ejecutar:
   - Abrí una terminal en esta carpeta y corré:
     docker-compose up --build

5. Accedé a la app en tu navegador:
   http://localhost:8080
