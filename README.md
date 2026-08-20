# seikyu — 発注書 → 適格請求書 自動生成

発注書（PDF・Excel・CSV・スキャン画像）を渡すと、内容を読み取って
**適格請求書（インボイス）** の PDF を作り、Mac 内のフォルダへ保存します。

- Mac のなかで完結。請求書と台帳は外に出ません
- 発注書の読み取りだけ Claude API を使います（後述）
- 発行前に必ず確認画面が出ます。お金の書類なので既定では全自動にしません

```
発注書 ──▶ 読み取り(Claude) ──▶ 確認・編集 ──▶ 請求書PDF ──▶ 保存 + 台帳
```

---

## 1. 準備

### インストール

```bash
cd ~/seikyu
uv venv --python 3.12
uv pip install -e .
```

`~/seikyu/.venv/bin/seikyu` が入ります。どこからでも使えるようにするなら:

```bash
ln -s ~/seikyu/.venv/bin/seikyu ~/bin/seikyu     # ~/bin が PATH にある前提
```

### Claude API キー

発注書の読み取りに Claude API を使います。**Claude Code の Max プランとは別枠**の
従量課金なので、コンソールでキーを発行してください。

1. https://console.anthropic.com/settings/keys でキーを作成
2. `~/.zshrc` に追記（※ Claude Code 用に `unset ANTHROPIC_API_KEY` している場合は、
   その行より **後ろ** に置くか、`seikyu` 実行時だけ渡す形にしてください）

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

読み取り 1 枚あたりの目安は **10円前後**（既定の `claude-opus-5`）です。
`config/company.toml` の `[api] model` を `claude-sonnet-5` にすると
半分以下に下がります（精度とのトレードオフ）。

### 設定ファイル

```bash
seikyu init          # config/company.toml のひな形を作る
vi config/company.toml
seikyu doctor        # 設定と環境の点検
```

`company.toml` には最低限これが必要です。空だと発行を止めます。

| 項目 | なぜ必須か |
|---|---|
| `[company] name` / `registration_number` | 適格請求書の必須記載事項（発行側はこの2つだけ） |
| `[bank]` 振込先 | 振込先のない請求書は実務で使えないため |

`[company] address`（自社住所）は**必須ではありません**。適格請求書の記載事項に住所は
含まれないためです。空にしておけば請求書にも出ません（`doctor` が注意だけ出します）。

`registration_number` は `T` + 13桁。
[国税庁の公表サイト](https://www.invoice-kohyo.nta.go.jp/) で確認できます。

> `config/company.toml` は `.gitignore` 済みです（登録番号と口座番号が入るため）。

---

## 2. 使う

### 発注書から発行する

```bash
seikyu issue ~/Downloads/発注書.pdf
seikyu issue *.pdf                    # まとめて
seikyu issue 発注書.xlsx --date 2026-08-31   # 発行日を指定
seikyu issue 発注書.pdf --dry-run     # 発行せず内容だけ見る
```

確認画面で `Enter`＝発行、`e`＝`$EDITOR` で修正、`q`＝中止。
`e` を押すと読み取り結果が JSON で開くので、直して保存すれば再計算されます。

### フォルダに入れるだけで発行する

```bash
seikyu watch
```

`_発注書受信` フォルダを見張り、ファイルが置かれたら読み取って確認画面を出します。
処理後は `_処理済み` / `_要確認` へ自動で振り分けます。
確認も飛ばして完全自動にするなら `seikyu watch --yes`。

### 発注書なしで作る

```bash
seikyu new           # 対話式の手入力
```

### 台帳

```bash
seikyu list                       # 発行済み一覧
seikyu list --unpaid              # 未入金だけ（期限超過は赤字）
seikyu list --buyer アルファ       # 取引先で絞る
seikyu paid INV-2026-0001         # 入金済みにする
seikyu index --year 2026          # 索引簿 CSV を出す
```

### 対応している発注書の形式

PDF / Excel(.xlsx) / CSV / 画像（PNG・JPEG・HEIC・TIFF）。
iPhone で撮った HEIC はそのまま渡せます（内部で JPEG に変換）。
旧形式の `.xls` は非対応なので `.xlsx` で保存し直してください。

---

## 3. 出力先

```
~/Documents/請求書/
├── 2026/
│   └── 2026-08/
│       ├── INV-2026-0001_株式会社ABC_20260818_1724000.pdf
│       └── INV-2026-0001_株式会社ABC_20260818_1724000_発注書.pdf   ← 元の発注書
└── _台帳/
    ├── seikyu.db          ← 採番・発行履歴・入金状況
    └── 索引簿_2026.csv     ← 電子帳簿保存法の検索要件用
```

保存先とファイル名は `config/company.toml` の `[output]` で変えられます。

---

## 4. 計算まわりで気をつけていること

| 項目 | 実装 |
|---|---|
| 消費税の端数処理 | **一の請求書につき、税率ごとに 1 回だけ**。行ごとに丸めてから合計しません（インボイス制度の要件） |
| 税率 | 発注書の記載に従い、10% / 8%（軽減）/ 0% を行ごとに保持。8% 行には `※` と凡例を自動で付けます |
| 金額の型 | すべて `Decimal`。`float` は一切通しません |
| 源泉徴収 | 100万円以下 10.21%、超過分 20.42%。既定は税抜ベース、円未満切捨て |
| 支払期限 | 締め日 → Nヶ月後 → 支払日。月末指定は各月の実際の末日に丸めます。土日祝の調整も設定可 |
| 検算 | 発注書に印字された合計と計算結果がずれていたら確認画面で警告します |
| 二重発行 | 同じ発注書ファイル（SHA-256）で再発行しようとすると警告します |
| 採番 | 年ごとの連番。確認を通ってから採番するので、中止しても欠番になりません |

---

## 5. 開発

```bash
uv pip install -e ".[dev]"
.venv/bin/python -m pytest        # 52 tests
```

| ファイル | 役割 |
|---|---|
| `models.py` | 発注書・請求書のデータ構造（Decimal・日付の正規化） |
| `config.py` | `company.toml` の読み込みと検証 |
| `extract.py` | 発注書 → 構造化データ（Claude API / structured outputs） |
| `tax.py` | 消費税・源泉徴収・支払期限 |
| `render.py` | HTML → PDF（ヘッドレス Chrome） |
| `ledger.py` | SQLite 台帳・採番・索引簿 |
| `review.py` | 発行前の確認と編集 |
| `cli.py` | コマンド定義 |

PDF 化は Chrome のヘッドレス印刷を使っています。日本語の禁則処理とフォント埋め込みを
ブラウザに任せられるためです。macOS では Chrome が起動済みだと印刷後にプロセスが
終了しないことがあるので、出力ファイルの完成を検知して側から止めています。
