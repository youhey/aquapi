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

未対応:

- JSON API
- ログ保存
- daemon 化 / systemd
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
