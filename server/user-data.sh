#!/bin/bash
set -euo pipefail
# cloud-init script for Amazon Linux 2 / Ubuntu variants (works on Amazon Linux 2)
APP_DIR=/opt/cloud25f
VENV_DIR=$APP_DIR/venv
USER=ec2-user   # ajustar se for ubuntu -> ubuntu
PORT=8080

# install deps
if [ -x "$(command -v yum)" ]; then
  yum update -y
  yum install -y python3 git
elif [ -x "$(command -v apt-get)" ]; then
  apt-get update -y
  apt-get install -y python3 python3-venv python3-pip git
fi

mkdir -p $APP_DIR
chown $USER:$USER $APP_DIR

# create a basic app files (app.py and requirements)
cat > $APP_DIR/app.py <<'PY'
# (cole aqui o conteúdo do app.py gerado anteriormente)
PY

cat > $APP_DIR/requirements.txt <<'REQ'
fastapi
uvicorn
boto3
pydantic
REQ

# create venv and install
python3 -m venv $VENV_DIR
$VENV_DIR/bin/pip install --upgrade pip setuptools
$VENV_DIR/bin/pip install -r $APP_DIR/requirements.txt

# create systemd service
cat > /etc/systemd/system/cloud25f.service <<'UNIT'
[Unit]
Description=Cloud25F FastAPI app
After=network.target

[Service]
User=ec2-user
Group=ec2-user
WorkingDirectory=/opt/cloud25f
Environment=AWS_REGION=us-east-2
Environment=UPLOAD_BUCKET=site-cloud25f-uploads
Environment=IMAGES_TABLE=Images
ExecStart=/opt/cloud25f/venv/bin/uvicorn app:app --host 0.0.0.0 --port 8080 --workers 1
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable cloud25f.service
systemctl start cloud25f.service
