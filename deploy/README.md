# التشغيل الدائم على الخادم

## 1) التحضير

```bash
sudo adduser --system --group alkhal
sudo mkdir -p /opt/alkhal-factory && sudo chown alkhal: /opt/alkhal-factory
# ارفع ملفات المصنع إلى /opt/alkhal-factory
cd /opt/alkhal-factory
sudo -u alkhal python3 -m venv venv
sudo -u alkhal venv/bin/pip install -r requirements.txt
sudo -u alkhal python3 setup_runtime.py alkhal_bot_v6_2.zip
```

## 2) الأسرار

```bash
sudo -u alkhal tee /opt/alkhal-factory/.env >/dev/null <<'ENV'
FACTORY_TOKEN=التوكن
FACTORY_OWNER_ID=8818060362
FACTORY_BRAND=مصنع بوتات الخال
ENV
sudo chmod 600 /opt/alkhal-factory/.env
```

## 3) الخدمة

```bash
sudo cp deploy/alkhal-factory.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now alkhal-factory
journalctl -u alkhal-factory -f
```

## 4) السجلات

```bash
sudo cp deploy/logrotate.conf /etc/logrotate.d/alkhal-factory
sudo logrotate -d /etc/logrotate.d/alkhal-factory
```

## 5) مفتاح التوقيع

```bash
sudo -u alkhal python3 -c "from app import deploy; deploy.load_key(create=True)"
```

## 6) الاستعادة

```bash
sudo systemctl stop alkhal-factory
sudo -u alkhal venv/bin/python -c "from app import backup; backup.restore('data/backups/factory-auto-XXXX.zip')"
sudo systemctl start alkhal-factory
```
