# aquapi

`aquapi` は Raspberry Pi 3 Model B 上で DS18B20 防水温度センサーと SHT31 室内温湿度センサーを読み取り、水槽ごとの水温管理に使うための軽量 Python アプリケーションです。

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
- Open-Meteo 外部気象連携
- SHT31 室内温度・湿度ロギング
- GPIO 漏水センサー現在状態 API
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

## Raspberry Pi の I2C 有効化

SHT31 を使う場合は Raspberry Pi OS Lite で I2C を有効化してください。

```bash
sudo raspi-config
```

`Interface Options` から `I2C` を有効化し、必要に応じて再起動します。

SHT31 の基本配線:

- `VIN` または `VCC`: 3.3V
- `GND`: GND
- `SDA`: GPIO2 / SDA
- `SCL`: GPIO3 / SCL

I2C デバイスを確認します。

```bash
sudo apt install -y i2c-tools python3-smbus
i2cdetect -y 1
```

標準設定では SHT31 の I2C address は `0x44` です。`i2cdetect` の表に `44` が出れば認識されています。

## 漏水センサー GPIO

漏水センサーは GPIO17 を drive output、GPIO27 を sense input として使います。GPIO17 は BCM GPIO 17、物理 Pin 11 です。物理 Pin 17 は 3.3V 電源なので混同しないでください。

想定配線:

- GPIO17 / 物理 Pin 11: 漏水センサー drive/output 側
- GPIO27 / 物理 Pin 13: 漏水センサー sense/input 側
- GPIO27 側は pull-down 入力

GPIO ライブラリは venv にインストールします。Raspberry Pi OS のパッケージを使う場合は、venv が system site packages を参照できる構成にしてください。

```bash
python -m pip install gpiozero
sudo usermod -aG gpio aquapi
```

簡易確認では、漏水センサー用の電極端子同士を短時間ショートすると `wet` を再現できます。3.3V と GND は直接ショートしないでください。

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

センサーIDごとに `name`、`short_name`、`short_name_ascii`、`type`、`role`、`enabled`、`visible`、`sort_order`、`offset`、`min`、`max` を設定します。

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
  "weather": {
    "enabled": true,
    "source": "open-meteo",
    "latitude": 35.681236,
    "longitude": 139.767125,
    "timezone": "Asia/Tokyo",
    "interval_seconds": 3600,
    "forecast_days": 2,
    "retention_days": 365
  },
  "environment_sensors": {
    "sht31_room": {
      "name": "室内",
      "short_name": "室内",
      "short_name_ascii": "ROOM",
      "type": "sht31",
      "role": "indoor",
      "enabled": true,
      "visible": true,
      "sort_order": 200,
      "i2c_bus": 1,
      "i2c_address": "0x44",
      "read_interval_seconds": 60
    }
  },
  "leak_sensors": {
    "leak_main": {
      "name": "漏水センサー",
      "short_name": "漏水",
      "short_name_ascii": "LEAK",
      "type": "conductive_probe",
      "role": "leak",
      "enabled": true,
      "visible": true,
      "sort_order": 300,
      "drive_gpio": 17,
      "sense_gpio": 27,
      "pull": "down",
      "active_state": "high",
      "read_interval_seconds": 5,
      "debounce_seconds": 2
    }
  },
  "sensors": {
    "28-00000020f5ed": {
      "name": "増田川水槽",
      "short_name": "増田川",
      "short_name_ascii": "MASUDA",
      "type": "water",
      "role": "aquarium",
      "enabled": true,
      "visible": true,
      "sort_order": 10,
      "offset": 0.0,
      "min": 18.0,
      "max": 28.0
    }
  }
}
```

`offset` は読み取った生温度に加算されます。例: 生温度 `24.300`、`offset: -0.2` の場合、補正後温度は `24.100 C` です。

`min` 未満は `low`、`max` 超過は `high`、範囲内は `ok` です。設定にないセンサーは `unknown` として表示されます。

`type` は `water` / `air` / `unknown` のような測定対象の物理種別です。`role` は Viewer/API 上の役割で、`aquarium` / `outdoor` / `indoor` / `leak` / `disabled` / `unknown` を使います。未指定時は `type: "water"` なら `aquarium`、`type: "air"` なら `outdoor`、それ以外は `unknown` です。

`enabled: false` のセンサーは読み取り・ログ保存・API表示から除外します。`visible: false` のセンサーは読み取り・ログ保存は継続しますが、`/api/readings` と `/api/summary` には表示しません。水槽カード表示では `role: "aquarium"` かつ `enabled: true` かつ `visible: true` を表示対象にしてください。`sort_order` は Viewer/API の表示順で、`sort_order ASC`、`name ASC`、`sensor_id ASC` の順に並びます。

`short_name` は M5Stack などの小さい画面向けの短い表示名です。未指定時は `name` 末尾の `水槽` を除いた文字列を使い、除去後に空になる場合は `name` をそのまま使います。

`short_name_ascii` は、日本語フォント対応が難しい M5Stack などの端末向けの ASCII 短縮名です。macOS Viewer などでは `short_name` / `name` を使い、M5Stack では `short_name_ascii`、`short_name`、`name`、`sensor_id` の順に表示名を選ぶ想定です。未指定時は ASCII の `short_name` または `name` から補完し、それもない場合は空文字になります。M5Stack で使うセンサーには明示設定を推奨します。

API サーバーは `listen_addr` と `listen_port` で待ち受けます。初期ポートは `8080` です。

ログ保存は `logging` で設定します。標準の保存方式は SQLite です。`interval_seconds` は継続記録の間隔、`database_path` は SQLite DB ファイル、`retention_days` は保持日数です。`retention_days <= 0` の場合、古い readings は削除しません。

外部気象連携は `weather` で設定します。`configs/aquapi.example.json` では、個人の所在地を含めないため、仮の緯度経度として東京駅付近を設定しています。実運用では Git 管理しない `configs/aquapi.json` に自分の地域の座標を設定してください。

Open-Meteo の値は、水槽や室内で測った実測値ではなく、地域気象の参考値です。水温変化と外部条件を後から見比べるために、1時間ごとの気温・湿度・風・降水などを SQLite に保存します。

室内温度・湿度センサーは `environment_sensors` で設定します。現在の対応タイプは `sht31` です。`sensor_key` は object の key です。`i2c_bus` は通常 `1`、`i2c_address` は `"0x44"` または `68` のような整数で指定できます。`enabled: false` の環境センサーは読み取りません。`visible: false` の環境センサーは読み取りと保存を継続しますが、`/api/environment/latest` の通常レスポンスからは除外されます。非表示も含める場合は `include_hidden=true` を指定します。

漏水センサーは `leak_sensors` で設定します。現在の対応タイプは `conductive_probe` です。`drive_gpio` は検査用出力、`sense_gpio` は導通状態を読む入力です。今回の想定では `drive_gpio: 17`、`sense_gpio: 27`、`pull: "down"`、`active_state: "high"` を使います。`enabled: false` の漏水センサーは GPIO を初期化しません。`debounce_seconds` は短時間のノイズで `dry` / `wet` が揺れるのを抑えるための確定時間です。

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
      "short_name": "増田川",
      "short_name_ascii": "MASUDA",
      "type": "water",
      "role": "aquarium",
      "enabled": true,
      "visible": true,
      "sort_order": 10,
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

SHT31 室内温湿度を読み取ります。

```bash
python -m aquapi.cli read-environment --config configs/aquapi.json
python -m aquapi.cli read-environment --config configs/aquapi.json --json
```

漏水センサーを読み取ります。

```bash
python -m aquapi.cli read-leak --config configs/aquapi.json
python -m aquapi.cli read-leak --config configs/aquapi.json --json
```

出力例:

```text
leak_main status=dry raw=0
leak_main status=wet raw=1
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
- `GET /api/sensors`
- `GET /api/sensors/{sensor_id}`
- `GET /api/monitoring/compact`
- `GET /api/readings/series?sensor_id={sensor_id}&range=24h`
- `GET /api/readings/summary?range=24h`
- `GET /api/weather/latest`
- `GET /api/weather/series?range=24h`
- `GET /api/weather/summary?range=7d`
- `GET /api/environment/latest`
- `GET /api/environment/series?sensor_key=sht31_room&range=24h`
- `GET /api/environment/summary?range=24h`
- `GET /api/leak/latest`

curl 例:

```bash
curl http://aquapi.local:8080/api/health
curl http://aquapi.local:8080/api/readings
curl http://aquapi.local:8080/api/summary
curl http://aquapi.local:8080/api/sensors
curl http://aquapi.local:8080/api/sensors/28-00000020f5ed
curl http://aquapi.local:8080/api/monitoring/compact
curl http://aquapi.local:8080/api/weather/latest
curl http://aquapi.local:8080/api/environment/latest
curl "http://aquapi.local:8080/api/environment/series?sensor_key=sht31_room&range=24h"
curl http://aquapi.local:8080/api/leak/latest
```

存在しないセンサーIDは JSON エラー付きで 404 を返します。

`/api/readings` の各 sensor item には `short_name`、`short_name_ascii`、`role`、`enabled`、`visible`、`sort_order` が含まれます。`/api/sensors` は設定ファイル上のセンサーマスタを返し、Viewer が表示順や分類を判断するために使えます。

`/api/leak/latest` は漏水センサーの現在状態を返します。`status` は `dry` / `wet` / `unknown` です。`wet` の場合だけ `alert: true` になります。GPIO 初期化失敗や読み取り失敗時も API は 500 にせず、対象センサーを `unknown` として返します。

`/api/monitoring/compact` は M5Stack / Widget / 小型表示端末向けの軽量 API です。複数 API を組み合わせず、この API だけで水槽の最低限の状態を表示できます。対象は `role: "aquarium"`、`enabled: true`、`visible: true` のセンサーだけです。`tanks[]` には `short_name` と `short_name_ascii` が含まれます。漏水センサーが `wet` の場合は、水温状態より優先して全体 `level` が `critical` になります。

compact API の全体 `level` / `label`:

- `ok` -> `AQUA OK`
- `warning` -> `WARN`
- `critical` -> `DANGER`
- `unknown` -> `UNK`

水槽ごとの `status` は `temperature_c` と `min` / `max` から判定します。範囲内は `safety`、範囲外かつ ±2.0℃以内は `warning`、±2.0℃を超える逸脱は `danger` です。温度がない、`min` / `max` がない、CRC 失敗、読み取りエラーの場合は `unknown` です。

M5Stack 表示例:

```text
AQUA OK

増田川 21.4 SAFE
めだか 21.4 SAFE
ミニ   21.4 SAFE
金魚   21.4 SAFE
```

異常時:

```text
DANGER

ミニ   31.1 DANGER
増田川 21.4 SAFE
めだか 21.4 SAFE
金魚   21.4 SAFE
```

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

`collect` は水温 readings を `logging.interval_seconds` ごとに保存します。`environment_sensors` が設定されている場合は、同じ collector 内で SHT31 室内温湿度も `read_interval_seconds` に従って SQLite に保存します。

Open-Meteo の外部気象を1回だけ取得して SQLite に保存します。

```bash
python -m aquapi.cli fetch-weather-once --config configs/aquapi.json
```

出力例:

```text
Saved 48 hourly weather records to /var/lib/aquapi/aquapi.sqlite3
```

外部気象を `weather.interval_seconds` ごとに継続取得します。初期値は 3600 秒です。

```bash
python -m aquapi.cli weather-collect --config configs/aquapi.json
```

SQLite には `sensors`、`readings`、`metadata`、`weather_hourly`、`environment_readings` テーブルを作成します。`sensors` には `short_name`、`short_name_ascii`、`role`、`enabled`、`visible`、`sort_order` も保存します。水温は `23.187 C` を `23187` のようにミリ℃の整数として保存します。60秒間隔、5センサー、1年保存でも約 2,628,000 readings なので SQLite で現実的に扱えます。

DB ファイルは Git 管理しません。`*.sqlite`、`*.sqlite3`、`*.db`、`data/` は `.gitignore` に含まれています。

`retention_days` より古い readings は、保存時または DB 初期化時に削除されます。`sensors` table は削除しません。

`weather_hourly` は水温 readings とは別テーブルです。`ts` は Unix time seconds の主キーで、同じ時刻の外部気象は更新されます。緯度経度は micro degree、気温・相対湿度・風速・降水量・降雪量・気圧・蒸発散量・土壌温度・土壌水分は milli 単位の整数、日射量は小数なし相当の整数として保存します。`weather.retention_days` より古い weather records は取得時に削除されます。

`environment_readings` は SHT31 などの室内実測値用テーブルです。`sensor_key`、`sensor_name`、`temperature_milli_c`、`relative_humidity_milli_percent`、`crc_ok`、`error`、`measured_at`、`created_at` を保存します。温度と相対湿度は milli 単位の整数です。

取得対象の Open-Meteo hourly variables:

- `temperature_2m`
- `relative_humidity_2m`
- `wind_speed_10m`
- `wind_direction_10m`
- `precipitation`
- `snowfall`
- `cloud_cover`
- `surface_pressure`
- `shortwave_radiation`
- `et0_fao_evapotranspiration`
- `soil_temperature_0cm`
- `soil_moisture_0_to_1cm`

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
- `GET /api/weather/latest`
- `GET /api/weather/series?range=24h`
- `GET /api/weather/summary?range=7d`
- `GET /api/environment/latest`
- `GET /api/environment/latest?include_hidden=true`
- `GET /api/environment/series?sensor_key=sht31_room&range=24h`
- `GET /api/environment/summary?range=24h`
- `GET /api/leak/latest`
- `GET /api/leak/latest?include_hidden=true`

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
curl "http://aquapi.local:8080/api/weather/series?range=24h"
curl "http://aquapi.local:8080/api/weather/summary?range=7d"
curl "http://aquapi.local:8080/api/environment/latest"
curl "http://aquapi.local:8080/api/environment/series?sensor_key=sht31_room&range=24h"
curl "http://aquapi.local:8080/api/environment/summary?range=24h"
curl "http://aquapi.local:8080/api/leak/latest"
```

## systemd

Raspberry Pi 上で常駐させる場合は、API サーバー、水温 collector、外部気象 collector を別 service として起動します。

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
sudo cp deploy/systemd/aquapi-weather.service /etc/systemd/system/
sudo systemctl daemon-reload
```

起動前に手動確認します。

```bash
sudo -u aquapi /opt/aquapi/.venv/bin/python -m aquapi.cli read --config /etc/aquapi/aquapi.json
sudo -u aquapi /opt/aquapi/.venv/bin/python -m aquapi.cli read-environment --config /etc/aquapi/aquapi.json
sudo -u aquapi /opt/aquapi/.venv/bin/python -m aquapi.cli read-leak --config /etc/aquapi/aquapi.json
sudo -u aquapi /opt/aquapi/.venv/bin/python -m aquapi.cli db-init --config /etc/aquapi/aquapi.json
sudo -u aquapi /opt/aquapi/.venv/bin/python -m aquapi.cli log-once --config /etc/aquapi/aquapi.json
sudo -u aquapi /opt/aquapi/.venv/bin/python -m aquapi.cli fetch-weather-once --config /etc/aquapi/aquapi.json
sudo -u aquapi /opt/aquapi/.venv/bin/python -m aquapi.cli db-stats --config /etc/aquapi/aquapi.json
```

service を起動します。

```bash
sudo systemctl enable --now aquapi-api.service
sudo systemctl enable --now aquapi-collect.service
sudo systemctl enable --now aquapi-weather.service
```

状態確認:

```bash
systemctl status aquapi-api.service
systemctl status aquapi-collect.service
systemctl status aquapi-weather.service
journalctl -u aquapi-api.service -n 100 --no-pager
journalctl -u aquapi-collect.service -n 100 --no-pager
journalctl -u aquapi-weather.service -n 100 --no-pager
curl http://localhost:8080/api/health
curl http://localhost:8080/api/weather/latest
curl http://localhost:8080/api/environment/latest
curl http://localhost:8080/api/leak/latest
```

`journalctl` に `ModuleNotFoundError: No module named 'aquapi'` が出る場合は、systemd が使う venv に `aquapi` がインストールされていません。service を止めてから `/opt/aquapi/.venv` に入れ直します。

```bash
sudo systemctl stop aquapi-api.service aquapi-collect.service aquapi-weather.service
cd /opt/aquapi
/opt/aquapi/.venv/bin/python -m pip install -e .
/opt/aquapi/.venv/bin/python -c "import aquapi; print(aquapi.__file__)"
/opt/aquapi/.venv/bin/python -m aquapi.cli --help
sudo systemctl start aquapi-api.service aquapi-collect.service aquapi-weather.service
```

更新時は service を停止してから pull / install し、設定を確認して再起動します。

```bash
sudo systemctl stop aquapi-api.service aquapi-collect.service aquapi-weather.service
cd /opt/aquapi
git pull
.venv/bin/python -m pip install -e .
sudo systemctl daemon-reload
sudo systemctl restart aquapi-api.service aquapi-collect.service aquapi-weather.service
```
