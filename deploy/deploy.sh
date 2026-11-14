#!/usr/bin/env bash
# Script de mise à jour / déploiement du portail CTSA RAMPE sur VPS.
# Usage (depuis /var/www/ctsa) : bash deploy/deploy.sh
set -euo pipefail

APP_DIR="/var/www/ctsa"
VENV_DIR="$APP_DIR/.venv"
SERVICE_NAME="ctsa"

cd "$APP_DIR"

if [ ! -f "$APP_DIR/.env" ]; then
    echo "Erreur : $APP_DIR/.env introuvable. Copiez .env.example et renseignez les variables."
    exit 1
fi

echo "==> Récupération du code"
git pull --ff-only

echo "==> Mise à jour des dépendances Python"
source "$VENV_DIR/bin/activate"
pip install --upgrade pip
pip install -r requirements.txt

echo "==> Chargement de l'environnement (.env)"
set -a
source "$APP_DIR/.env"
set +a

echo "==> Application des migrations"
python manage.py migrate --noinput

echo "==> Collecte des fichiers statiques"
python manage.py collectstatic --noinput

echo "==> Vérification de la configuration production"
python manage.py check --deploy

echo "==> Redémarrage du service $SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"
sudo systemctl status "$SERVICE_NAME" --no-pager

echo "==> Déploiement terminé avec succès."
