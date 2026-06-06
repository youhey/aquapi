# aquapi

`aquapi` は Raspberry Pi 3 Model B 上で DS18B20 防水温度センサーを読み取り、水槽ごとの水温管理に使うための軽量 Python アプリケーションです。

Phase 1 では、`/sys/bus/w1/devices/28-*` に認識された 1-Wire センサーを検出し、`w1_slave` から CRC が正常な温度だけを読み取る CLI を提供します。

## Phase 1 の範囲

対応済み:

- `/sys/bus/w1/devices/28-*` の探索
- `w1_slave` の読み取り
- CRC `YES` の確認
- `t=xxxxx` の摂氏変換
- 通常出力と JSON 出力

未対応:

- 設定ファイル
- センサーIDと水槽名の対応
- 温度補正 offset
- しきい値
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

## CLI 実行

通常出力:

```bash
python -m aquapi.cli read
```

出力例:

```text
28-00000020f5ed  23.187 C
28-000000224fb6  23.250 C
28-000000230ee6  23.125 C
```

JSON 出力:

```bash
python -m aquapi.cli read --json
```

出力例:

```json
{
  "sensors": [
    {
      "sensor_id": "28-00000020f5ed",
      "temperature_c": 23.187,
      "crc_ok": true
    }
  ]
}
```

## テスト

```bash
python -m unittest discover -s tests
```
