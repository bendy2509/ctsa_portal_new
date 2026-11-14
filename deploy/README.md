# Déploiement sur VPS (Ubuntu / Debian)

Ce dossier contient les fichiers prêts à l'emploi pour héberger le portail CTSA RAMPE dans `/var/www/ctsa`.

## 1. Installation initiale

```bash
sudo apt update && sudo apt install -y python3-venv python3-pip git nginx certbot python3-certbot-nginx

sudo mkdir -p /var/www/ctsa
sudo chown "$USER:$USER" /var/www/ctsa
git clone <URL_DU_DEPOT> /var/www/ctsa
cd /var/www/ctsa

python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install gunicorn psycopg[binary]
```

## 2. Configuration `.env`

Copiez `.env.example` vers `/var/www/ctsa/.env` (`chmod 600 /var/www/ctsa/.env`) et complétez :

```ini
DJANGO_ENV=production
DJANGO_SECRET_KEY=<clé générée via get_random_secret_key()>
DJANGO_ALLOWED_HOSTS=rampe.votredomaine.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://rampe.votredomaine.com
```

Puis initialisez la base et les statiques :

```bash
set -a && source .env && set +a
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py createsuperuser
```

## 3. Activer Gunicorn (`systemd`) et Nginx

```bash
sudo chown -R www-data:www-data /var/www/ctsa
sudo cp deploy/ctsa.service /etc/systemd/system/ctsa.service
sudo systemctl daemon-reload
sudo systemctl enable --now ctsa

sudo cp deploy/nginx.conf /etc/nginx/sites-available/ctsa
# Remplacez rampe.votredomaine.com dans /etc/nginx/sites-available/ctsa
sudo ln -s /etc/nginx/sites-available/ctsa /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

sudo certbot --nginx -d rampe.votredomaine.com
```

## 4. Mises à jour suivantes

```bash
cd /var/www/ctsa
bash deploy/deploy.sh
```
