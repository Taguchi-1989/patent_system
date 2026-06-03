# アーキテクチャ（改訂版 / 立て付けの修正）

> 本書は `patent_ai_requirements_revised.md`（要件定義）への**批判的レビューを反映した設計**です。
> 要件の「何を」に対し、本書は「どう現実的に作るか」を定義します。
> 前提条件の変更点：**JPO 特許情報取得API は新規受付終了（2024-08-09）のため使用しない。**

---

## 0. 設計原則（要件レビューから昇格させた必須事項）

1. **推測で断定しない (P-NO-GUESS)** — 全ての構造化・分析結果は出典スパン＋信頼度を持つ。
   弱い根拠は `UNCLEAR` として人へエスカレーションする。番号正規化も同様に、曖昧な入力は
   `needs_review` フラグを立てる（沈黙して誤接合しない）。
2. **「無人」ではなく「人の確認コスト最小化」** — `MATCH/MISSING/UNCLEAR` の自動確定はしない。
   人は「リスト出所確認・比較観点定義・最終法務レビュー」に集中する（要件§9.2を維持）。
3. **リスク先行・縦切り** — 層を横に積む前に、1番号が端から端まで通る縦スライスで不確実性を潰す。
4. **状態は明示的に持つ** — 差分監視は「過去の取得結果」を前提とする。状態層を第一級で持つ。

---

## 1. コンポーネント構成（要件§8.1 の4層 → 6コンポーネントへ）

要件の4層には**監視に必須の「状態/スナップショット層」と「評価層」が欠落**していた。補う。

```
┌─────────────────────────────────────────────────────────────┐
│ 1. Ingestion      番号取込 + 正準化（Office判別/kind/doc_type）  │  ← src/patentkit/normalize
│ 2. Retrieval      USPTO ODP / EPO OPS（+将来 BigQuery）         │  ← src/patentkit/connectors
│    └ Cache/RateLimit  4GB/週・スロットリング・再試行           │
│ 3. State/Snapshot 時系列バージョンストア（差分の土台）★新設     │  ← src/patentkit/state (未)
│ 4. Analysis       要約 / 請求項分解 / 比較 / 差分（出典+信頼度）  │  ← src/patentkit/analyze (未)
│ 5. Presentation   HTML UI / Markdown / PDF / NotebookLM        │  ← src/patentkit/export (未)
│ 6. Eval/Quality   golden set + 回帰測定 ★新設                  │  ← tests/eval (未)
└─────────────────────────────────────────────────────────────┘
Runtime:  GitHub Actions cron = 監視の定期実行（揮発するColabでは不可）
          Colab = 対話的な開発・検証のハーネスのみ
```

---

## 2. データソース戦略（鍵が取得できない前提の現実解）

**前提の更新**：APIキー/本人確認が取得できない可能性が高い。USPTO ODP は **ID.me 本人確認**が
必須で壁が高く、EPO OPS も開発者アカウント＋アプリ登録が要る。よって **鍵が要るAPIはMVPから外し、
鍵不要ルートを主軸**にする。

| 手段 | 必要なもの | カバレッジ | 採否 |
|---|---|---|---|
| **Google Patents Public Data on BigQuery（Sandbox）** | **Googleログインのみ**（カード不要・月1TB無料） | 世界の書誌/ファミリー + US全文。番号で直接クエリ可 | ✅ **主軸** |
| **USPTO 一括データ（bulkdata.uspto.gov）** | **不要**（直リンクDL） | US 全文XML（2002〜・週次） | ✅ 補助/バルク |
| **FixtureSource（ローカル標本）** | 不要 | 開発・テスト・デモ | ✅ 既定（鍵ゼロで全工程通す） |
| ~~USPTO ODP API~~ | USPTO.gov + **ID.me本人確認** | US全文・経過・継続 | ✗ 見送り |
| ~~EPO OPS~~ | 開発者アカウント+アプリ登録 | EP全文・INPADOC | △ 任意（取れたら追加） |
| ~~JPO API~~ | 申込（**受付終了**） | JP | ✗ 不可 |

**意思決定**：MVPは `BigQuery Sandbox（主）+ USPTO一括（補助）+ Fixture（既定）` の鍵不要構成。
EPO OPS は取得できた場合のみ任意で追加。JP日本語全文クレームは本質的難所のため Phase 2+ に後送り。
**LLMキーも不要**：要約・比較の意味判定はエージェント（Claude Code）が黒子として実施する。

### データ源の差し替え（Provider パターン）
パイプラインは `PatentSource` インターフェースにのみ依存する。`FixtureSource` → `BigQuerySource`
→（任意）`OpsSource` を**同じ口に差し替えるだけ**で、コード変更なしに鍵なし→実データへ昇格できる。
鍵が要るルート用の疎通ハーネス `scripts/verify_sources.py` は、EPO OPS を後で取得した場合のみ使う。

---

## 3. リポジトリ構成（要件§11を踏襲しつつ src 中心へ）

```text
patent_system/
  README.md
  pyproject.toml / requirements.txt / .env.example / .gitignore
  docs/
    architecture.md            # 本書
    requirements_review.md     # 批判的レビューの記録（追って整備）
  src/patentkit/
    normalize/                 # 1. Ingestion: 正準番号モデル（実装済み・鍵不要）
    connectors/                # 2. Retrieval: ODP / OPS（Phase 0で疎通）
    state/                     # 3. State/Snapshot（未）
    analyze/                   # 4. Analysis（未）
    export/                    # 5. Presentation（未）
  scripts/
    verify_sources.py          # Phase 0 疎通検証CLI
  tests/                       # 6. Eval/Quality（normalize は実装済み）
  samples/
    public_patent_numbers.csv
  outputs/.gitkeep             # 生成物（git-ignore）
```

---

## 4. 開発ロードマップ（リスク先行）

- **Phase 0 — 取得スパイク**：ODP/OPS を実在番号で素通し検証。`§13「圧縮可能」を事実で再判定`。
- **Phase 1 — 縦の最小スライス**：US 1庁で「CSV→取得→要約→出典付きMD→最小HTML一覧」。
- **Phase 2 — 横展開 + 状態層**：EPO接続 / Snapshotストア / ファミリー重複排除 / 比較表(MATCH等) / golden set評価開始。
- **Phase 3 — 監視自動化**：GitHub Actions cron / 差分抽出 / 通知文。JPバルク全文はここで検討。

---

## 5. 既知の難所（正直な限界）

- **JP日本語全文**：クリーンな無料リアルタイムAPIが現状無い。バルク（整理標準化データ）は重く登録制。
- **特許番号正準化**：特にJPの元号表記・登録番号は年を内包せず、文字列処理だけでは一意化できない場合がある
  → `normalize` は該当時 `confidence` を下げ `notes` に記録（沈黙しない）。最終的な庁フォーマット解決は
  取得APIに委ねる設計とする。
- **ファミリー重複**：1発明=多member。INPADOCファミリーで重複排除しないと比較表が溺れる。
- **コスト**：全文は1件数万トークン。全文丸投げでなく抽出前処理を入れる。
- **翻訳**：JPクレーム×英語仕様の比較は翻訳品質がMATCH精度を直撃。翻訳層の品質を明示管理する。
