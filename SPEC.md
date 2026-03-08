# NFC Emu 仕様・実装メモ

## 概要

RC-S380 (Sony PaSoRi) を使って NFC カードをエミュレートするライブラリおよびツール群。
現在は FeliCa (Type-F) に対応しており、将来的に Type-A 等への拡張が可能な設計となっている。

---

## プロジェクト構造

```text
/
├── nfc_emu/           # コアライブラリ & 実行スクリプト
│   ├── base.py        # 基底クラス（Card, Protocol, Emulator, Hook）
│   ├── dump_card.py   # 実カードのダンプ（マルチシステム対応）
│   ├── emulate_card.py # ダンプデータからのエミュレート
│   ├── inspect_dump.py # ダンプデータの hexdump 表示
│   ├── probe_card.py  # 実カード観察ツール
│   ├── utils.py       # 共通ユーティリティ（リーダー選択、フックロード）
│   └── felica/        # FeliCa (Type-F) 実装
│       ├── card.py    # FeliCa カードデータモデル（サイクリック対応）
│       ├── const.py   # FeliCa プロトコル定数・オフセット定義
│       ├── protocol.py # FeliCa プロトコルハンドラ（イベント駆動）
│       └── emulator.py # FeliCa エミュレータ本体（UI層）
├── cards/             # ダンプデータ保存先
├── hooks/             # フックスクリプト（BaseHook 継承）
├── tests/             # ユニットテスト (pytest)
├── pyproject.toml     # パッケージ定義
└── flake.nix          # 開発環境定義
```

---

## コア設計 (nfc_emu/base.py)

### 1. ProtocolResult (Enum)
プロトコルハンドラの処理結果ステータスを定義。
- `RESPONSE`: 応答データあり
- `CONTINUE`: 無応答でセッション継続 (SC 不一致、エコーバック等)
- `UNKNOWN`: 未知のコマンド
- `ERROR`: プロトコルエラー（切断を推奨）

### 2. BaseProtocol (Event-Driven)
UI とロジックを分離するため、イベント発行の仕組みを搭載。
- `set_event_handler(handler)`: UI層などでイベント（log, polling, read, write 等）を購読するために使用。
- `handle(cmd)`: 生のコマンドペイロードを受け取り、`(ProtocolResult, response)` を返す。

### 3. BaseHook (Structured Hooks)
クラスベースのフックシステム。ユーザーはこれを継承して `hook.py` を作成する。
- `on_command(code, cmd)`: 全コマンドに対する低レベル介入。
- `on_polling(req_sc)`: IDm/PMm の動的な上書き。
- `on_read(svc, blk, data)`: 読み取りデータの動的な変更。
- `on_write(svc, blk, data)`: 書き込みの拒否（バリデーション）やデータの加工。

---

## FeliCa (Type-F) 実装詳細

### マルチシステムコードのスキャンとエミュレート
- **ダンプ時**: カードが持つ全システムコード (SC) を検出し、SC ごとに個別に Polling してサービス構造を網羅的にスキャンする。
- **保存時**: SC ごとに異なる IDm/PMm を `sys_details` フィールドに保存。
- **エミュレート時**: リーダーからの Polling に応じて動的に IDm/PMm を切り替えて応答する。

### エリア終端コード (Area End Codes)
- Search Service Code (0x0A) の精度を向上させるため、ダンプ時に実カードから取得した正確なエリア終端コードを保存し、エミュレーションに使用する。

### サイクリックサービス (Cyclic Services)
- 書き込みが発生すると、既存のブロックデータを自動的に後ろへシフトし、常に最新のデータをブロック 0 に配置する実カードの挙動を再現。

---

## JSON ダンプフォーマット (拡張版)

```json
{
  "idm": "0123456789ABCDEF",
  "pmm": "0123456789ABCDEF",
  "sys_code": "FE00",
  "sys_codes": ["FE00", "0003"],
  "sys_details": {
    "FE00": { "idm": "0123456789ABCDEF", "pmm": "0123456789ABCDEF" },
    "0003": { "idm": "012E456789ABCDEF", "pmm": "01004B024B47AAFF" }
  },
  "service_list": [0, 256, 264, 267, 512, 521, 584, 587],
  "service_attrs": {
    "267": "plain", "584": "protected", "587": "plain"
  },
  "area_ends": {
    "0": 65534,
    "256": 511
  },
  "memory": {
    "267": { "0": "303032...", "1": "323032..." }
  },
  "patches": []
}
```

---

## 使い方

### 1. カードダンプ
```sh
nfc-dump [output.json]
```
（複数 SC を持つカードの場合、全ての SC を順番にスキャンします）

### 2. エミュレート
```sh
nfc-emu [input.json] --hooks hooks/my_hook.py
```

### 3. ユニットテストの実行
```sh
pytest tests/
```
