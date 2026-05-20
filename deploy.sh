#!/bin/bash
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

echo "==> Verificando cambios en GitHub..."
git fetch origin main -q

CHANGED=$(git diff --name-only HEAD origin/main)

if [ -z "$CHANGED" ]; then
    echo "   Sin cambios — nada que actualizar."
    docker ps --filter name=almacenes_web --format "  {{.Names}}  estado:{{.Status}}"
    exit 0
fi

echo "   Archivos modificados:"
echo "$CHANGED" | sed 's/^/     /'

echo "==> Aplicando cambios..."
git pull origin main -q

if echo "$CHANGED" | grep -qE "\.(py|txt|yml|env)$"; then
    echo "==> Rebuild necesario (cambios en Python/config)..."
    docker compose up -d --build
else
    echo "==> Solo templates/static — reinicio simple..."
    docker compose restart
fi

echo ""
echo "✅ Deploy completado."
docker ps --filter name=almacenes_web --format "  {{.Names}}  estado:{{.Status}}"
