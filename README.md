# aquapi

`aquapi` は Raspberry Pi 3 Model B 上で DS18B20 防水温度センサーを読み取り、水槽ごとの水温管理に使うための軽量 Python アプリケーションです。

`/sys/bus/w1/devices/28-*` に認識された 1-Wire センサーを検出し、`w1_slave` から CRC が正常な温度を読み取る CLI を提供します。
設定ファイルを指定すると、センサーIDを水槽名へ対応付け、offset 補正と min/max しきい値に基づく status 判定を行います。

## 現在の範囲

対応済み:

- `/sys/bus/w1/devices/28-*` の探索
- `w1_slave` の読み取り
- CRC `YES` の確認
- `t=xxxxx` の摂氏変換
- 通常出力と JSON 出力
- 設定ファイルによるセンサーIDと水槽名の対応
- offset 補正
- min/max しきい値による `ok` / `low` / `high` 判定
- 未登録センサーの `unknown` 表示
- JSON API
- SQLite ログ保存
- systemd による API / collector の常駐

未対応:

- アラート
- M5Stack 連携
- netwatch-viewer 連携

## Raspberry Pi の 1-Wire 有効化

Raspberry Pi OS Lite で 1-Wire を有効化してください。

```bash
sudo raspi-config
```

`Interface Options` から `1-Wire` を有効化し、必要に応じて再起動します。

## センサー確認

認識済みデバイスを確認します。

```bash
ls /sys/bus/w1/devices/
```

個別センサーの生データを確認します。

```bash
cat /sys/bus/w1/devices/28-00000020f5ed/w1_slave
```

`w1_bus_master1` はセンサーではないため、CLI では対象外です。

## セットアップ

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

## 設定ファイル

設定例を実運用ファイルとしてコピーします。

```bash
cp configs/aquapi.example.json configs/aquapi.json
```

`configs/aquapi.json` はローカル運用設定として `.gitignore` に含まれています。

センサーIDごとに `name`、`type`、`offset`、`min`、`max` を設定します。

```json
{
  "listen_addr": "0.0.0.0",
  "listen_port": 8080,
  "logging": {
    "enabled": true,
    "interval_seconds": 60,
    "storage": "sqlite",
    "database_path": "/var/lib/aquapi/aquapi.sqlite3",
    "retention_days": 365
  },
  "sensors": {
    "28-00000020f5ed": {
      "name": "増田川水槽",
      "type": "water",
      "offset": 0.0,
      "min": 18.0,
      "max": 28.0
    }
  }
}
```

`offset` は読み取った生温度に加算されます。例: 生温度 `24.300`、`offset: -0.2` の場合、補正後温度は `24.100 C` です。

`min` 未満は `low`、`max` 超過は `high`、範囲内は `ok` です。設定にないセンサーは `unknown` として表示されます。

API サーバーは `listen_addr` と `listen_port` で待ち受けます。初期ポートは `8080` です。

ログ保存は `logging` で設定します。標準の保存方式は SQLite です。`interval_seconds` は継続記録の間隔、`database_path` は SQLite DB ファイル、`retention_days` は保持日数です。`retention_days <= 0` の場合、古い readings は削除しません。

## CLI 実行

通常出力:

```bash
python -m aquapi.cli read --config configs/aquapi.json
```

出力例:

```text
増田川水槽       23.187 C  ok       28-00000020f5ed
めだか水槽       23.250 C  ok       28-000000224fb6
外気             21.500 C  ok       28-00000023733a
```

JSON 出力:

```bash
python -m aquapi.cli read --config configs/aquapi.json --json
```

出力例:

```json
{
  "sensors": [
    {
      "sensor_id": "28-00000020f5ed",
      "name": "増田川水槽",
      "type": "water",
      "raw_temperature_c": 23.187,
      "temperature_c": 23.187,
      "offset": 0.0,
      "min": 18.0,
      "max": 28.0,
      "status": "ok",
      "crc_ok": true
    }
  ]
}
```

## テスト

```bash
python -m unittest discover -s tests
```

## JSON API

API サーバーを起動します。

```bash
python -m aquapi.cli serve --config configs/aquapi.json
```

CLI 引数で待ち受け先を上書きできます。

```bash
python -m aquapi.cli serve --config configs/aquapi.json --host 0.0.0.0 --port 8080
```

API 一覧:

- `GET /api/health`
- `GET /api/readings`
- `GET /api/summary`
- `GET /api/sensors/{sensor_id}`
- `GET /api/readings/series?sensor_id={sensor_id}&range=24h`
- `GET /api/readings/summary?range=24h`

curl 例:

```bash
curl http://aquapi.local:8080/api/health
curl http://aquapi.local:8080/api/readings
curl http://aquapi.local:8080/api/summary
curl http://aquapi.local:8080/api/sensors/28-00000020f5ed
```

存在しないセンサーIDは JSON エラー付きで 404 を返します。

## ログ保存

DB を初期化し、設定ファイル上のセンサー定義を同期します。

```bash
python -m aquapi.cli db-init --config configs/aquapi.json
```

現在値を1回だけ SQLite に保存します。

```bash
python -m aquapi.cli log-once --config configs/aquapi.json
```

出力例:

```text
Saved 5 readings to /var/lib/aquapi/aquapi.sqlite3
```

DB の保存状況を確認します。

```bash
python -m aquapi.cli db-stats --config configs/aquapi.json
```

指定間隔で継続的に保存します。

```bash
python -m aquapi.cli collect --config configs/aquapi.json
```

SQLite には `sensors`、`readings`、`metadata` テーブルを作成します。温度は `23.187 C` を `23187` のようにミリ℃の整数として保存します。60秒間隔、5センサー、1年保存でも約 2,628,000 readings なので SQLite で現実的に扱えます。

DB ファイルは Git 管理しません。`*.sqlite`、`*.sqlite3`、`*.db`、`data/` は `.gitignore` に含まれています。

`retention_days` より古い readings は、保存時または DB 初期化時に削除されます。`sensors` table は削除しません。

JSONL 互換が必要な場合は、明示的に `storage: "jsonl"` を指定してください。

```json
{
  "logging": {
    "enabled": true,
    "interval_seconds": 60,
    "storage": "jsonl",
    "data_dir": "/var/lib/aquapi",
    "file_pattern": "readings-%Y-%m-%d.jsonl",
    "retention_days": 30
  }
}
```

履歴 API:

- `GET /api/readings/series?sensor_id={sensor_id}&range=24h`
- `GET /api/readings/series?name={name}&range=24h`
- `GET /api/readings/summary?range=24h`

対応 range:

- `1h`
- `6h`
- `24h`
- `7d`
- `30d`
- `365d`
- `1y`

curl 例:

```bash
curl "http://aquapi.local:8080/api/readings/series?sensor_id=28-00000020f5ed&range=24h"
curl "http://aquapi.local:8080/api/readings/series?name=増田川水槽&range=24h"
curl "http://aquapi.local:8080/api/readings/summary?range=24h"
```

## systemd

Raspberry Pi 上で常駐させる場合は、API サーバーと collector を別 service として起動します。

想定配置:

- アプリケーション: `/opt/aquapi`
- 設定ファイル: `/etc/aquapi/aquapi.json`
- ログ保存先: `/var/lib/aquapi`
- 実行ユーザー: `aquapi`

初回配置:

```bash
sudo useradd --system --home /var/lib/aquapi --shell /usr/sbin/nologin aquapi
sudo mkdir -p /opt/aquapi /etc/aquapi /var/lib/aquapi
sudo chown -R "$USER:$USER" /opt/aquapi
sudo chown -R aquapi:aquapi /etc/aquapi /var/lib/aquapi

git clone ssh://git@github.com/youhey/aquapi.git /opt/aquapi
cd /opt/aquapi
python3 -m venv .venv
.venv/bin/python -m pip install -e .

sudo cp configs/aquapi.example.json /etc/aquapi/aquapi.json
sudo chown aquapi:aquapi /etc/aquapi/aquapi.json
sudo nano /etc/aquapi/aquapi.json
```

unit をインストールします。

```bash
sudo cp deploy/systemd/aquapi-api.service /etc/systemd/system/
sudo cp deploy/systemd/aquapi-collect.service /etc/systemd/system/
sudo systemctl daemon-reload
```

起動前に手動確認します。

```bash
sudo -u aquapi /opt/aquapi/.venv/bin/python -m aquapi.cli read --config /etc/aquapi/aquapi.json
sudo -u aquapi /opt/aquapi/.venv/bin/python -m aquapi.cli db-init --config /etc/aquapi/aquapi.json
sudo -u aquapi /opt/aquapi/.venv/bin/python -m aquapi.cli log-once --config /etc/aquapi/aquapi.json
sudo -u aquapi /opt/aquapi/.venv/bin/python -m aquapi.cli db-stats --config /etc/aquapi/aquapi.json
```

service を起動します。

```bash
sudo systemctl enable --now aquapi-api.service
sudo systemctl enable --now aquapi-collect.service
```

状態確認:

```bash
systemctl status aquapi-api.service
systemctl status aquapi-collect.service
journalctl -u aquapi-api.service -n 100 --no-pager
journalctl -u aquapi-collect.service -n 100 --no-pager
curl http://localhost:8080/api/health
```

`journalctl` に `ModuleNotFoundError: No module named 'aquapi'` が出る場合は、systemd が使う venv に `aquapi` がインストールされていません。service を止めてから `/opt/aquapi/.venv` に入れ直します。

```bash
sudo systemctl stop aquapi-api.service aquapi-collect.service
cd /opt/aquapi
/opt/aquapi/.venv/bin/python -m pip install -e .
/opt/aquapi/.venv/bin/python -c "import aquapi; print(aquapi.__file__)"
/opt/aquapi/.venv/bin/python -m aquapi.cli --help
sudo systemctl start aquapi-api.service aquapi-collect.service
```

更新時は service を停止してから pull / install し、設定を確認して再起動します。

```bash
sudo systemctl stop aquapi-api.service aquapi-collect.service
cd /opt/aquapi
git pull
.venv/bin/python -m pip install -e .
sudo systemctl daemon-reload
sudo systemctl restart aquapi-api.service aquapi-collect.service
```
