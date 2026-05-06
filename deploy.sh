#!/bin/bash
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

echo "==> Bajando cambios de GitHub..."
git pull origin main

echo "==> Reiniciando contenedor..."
docker compose up -d --build

echo "==> Listo. Contenedor activo:"
docker ps --filter name=almacenes_web --format "  {{.Names}}  estado:{{.Status}}"
