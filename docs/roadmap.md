# 実装ロードマップ（マイルストーン計画）

> 方針：**鍵不要主軸**（[docs/architecture.md](architecture.md) §2）。各マイルストーンは
> 「**動くデモ＋テストで終える**」を必須にし、`PatentSource` などの抽象の裏で実装する。
> 全工程で **P-NO-GUESS（曖昧は flag）** と **出典付与（§7.2）** を守る。

凡例： ✅ 完了 / 🟡 進行中 / ⬜ 未着手

| M | 名前 | 状態 | 要件Phase |
|---|---|---|---|
| M0 | 基盤（正準化・Providerパターン・抽出要約・MD出力・通し） | ✅ | Phase 1 骨格 |
| M1 | 実データ（鍵不要）＝ BigQuery Patents | ✅ | Phase 1 |
| M2 | USPTO一括データ補助（US全文・バルク） | ✅ | Phase 1 |
| M3 | 状態/スナップショット層 ＋ 差分エンジン | ✅ | Phase 2 |
| M4 | 意味比較（MATCH/MISSING/UNCLEAR・黒子エージェント）＋ golden set評価 | ✅ | Phase 2 |
| M5 | HTML UI（一覧→詳細→比較→差分履歴）＋ NotebookLM出力整形 | ✅ | Phase 1〜2 |
| M6 | 監視自動化（GitHub Actions cron・差分通知） | ✅ | Phase 2〜3 |
| M7 | 評価・品質ハードニング（回帰・コスト/レイテンシ） | ✅ | 横断 |
| M8 | 調査（検索）モジュール＝番号を「もらう」から「見つける」へ | ✅ | 研究実用化 |
| M9 | 法的状態・JP対応（INPADOC/OPS・J-PlatPat導線） | ⬜ | 研究実用化 |
| M10 | 意味検索（embedding再ランク・類似特許探索） | ⬜ | 研究実用化 |
| M11 | 調査レポート様式（SDI監視テーマ・先行技術調査テンプレ） | ⬜ | 研究実用化 |

---

## M0 — 基盤 ✅（完了）

- **成果物**：`normalize`（11/11テスト）、`PatentSource`＋`FixtureSource`、抽出要約、Markdown出力、`run_pipeline.py` で端から端まで通し。
- **受け入れ**：`py scripts/run_pipeline.py` が `outputs/report.md`（注記・比較表・要素分解・未取得処理）を生成。✅

## M1 — 実データ（鍵不要）＝ BigQuery Patents ✅（完了）

- **目標**：`patents-public-data.patents.publications` から実在番号で書誌/要約/クレーム/ファミリーを取得。
  正準番号がBigQueryの `publication_number`（`US-10123456-B2` 形式）と**ほぼ一致**するため接続が素直。
- **成果物**：
  - `connectors/bigquery.py`：`record_from_bq_row()`（純関数・テスト対象）、`BigQuerySource`（ライブ）、`BigQueryExportSource`（コンソールからのエクスポートJSONを読む＝**インストール不要**）。
  - `sql/publications_by_number.sql`：番号INクエリ（必要列のみ・コスト注記）。
  - `run_pipeline.py` のソース選択（`--source fixture|bq|bq-export`）。
- **キーレス手段（2択）**：
  - (a) **ゼロインストール**：BigQueryコンソールにGoogleログイン → 同梱SQLを実行 → 結果をJSON保存 → `BigQueryExportSource` が読む。
  - (b) **自動化**：`gcloud auth application-default login`（無料・カード不要）→ `BigQuerySource` がライブクエリ。
- **受け入れ**：実在の US/EP 番号で `PatentRecord` が出典URL（`patents.google.com/...`）付きで生成され、`report.md` に実データが載る。コスト：Sandbox 1TB/月内（必要列限定）。
- **正直な限界**：法的状態はこのデータセットに無い（→ M後続でINPADOC/別ソース）。JP日本語全文は機械翻訳依存・後送り。

## M2 — USPTO一括データ補助 ✅（完了）

- **目標**：US全文の確実な取得とバルク監視の土台。`bulkdata.uspto.gov` の Patent Grant Full-Text XML（週次）を**アカウント不要**でDL・パース。
- **成果物**：`connectors/bulk_uspto.py`（週次ZIP取得→XML→`PatentRecord`）、番号→週次ファイル解決、ローカルキャッシュ。
- **受け入れ**：既知のUS登録番号の独立請求項全文をバルクから取得できる。

## M3 — 状態/スナップショット層 ＋ 差分エンジン ✅（完了）

- **目標**：継続監視の土台。各取得を時系列・ハッシュ付きで永続化し、差分を計算。
- **成果物**：`state/`（SQLite or JSONスナップショット）、`diff`（法的状態/追加書類/継続出願/引用変化）、差分通知文の雛形。
- **受け入れ**：再実行で「変更なし／変更あり」を判定し、変更点のMarkdown差分を出力。

## M4 — 意味比較（黒子エージェント）＋ 評価 ✅（完了）

- **目標**：製品の核。技術説明（自社仕様）× 請求項要素 を `MATCH/MISSING/UNCLEAR` で対応付け。
- **成果物**：構造化スキーマ（要素・判定・**出典スパン**・信頼度）、判定はエージェント（Claude Code）が実施＝**LLMキー不要**、`UNCLEAR`は自動エスカレーション、golden set（5〜10件）と一致率測定。
- **受け入れ**：比較表に意味列が付き、各セルが出典スパンを引用。根拠なき断定はゼロ（P-NO-GUESS）。

## M5 — HTML UI ＋ NotebookLM出力 ✅（完了）

- **目標**：一覧→詳細→比較表→差分履歴 の最小導線。GitHub Pages で配信可能な静的HTML。
- **成果物**：`export/html.py`、テンプレート、NotebookLM向けMarkdown整形の仕上げ。
- **受け入れ**：一覧から詳細に入り、比較表と差分履歴が見える。

## M6 — 監視自動化（GitHub Actions cron）✅（完了）

- **目標**：黒子の定期実行。揮発するColabではなくActionsで回す。
- **成果物**：`.github/workflows/monitor.yml`（cron）、スナップショット更新、差分レポート＆通知文生成。
- **受け入れ**：スケジュール実行でスナップショット更新＋差分レポートが生成される。

## M7 — 評価・品質ハードニング ✅（完了）

- **目標**：「AI自走」を信頼するための回帰とコスト管理。
- **成果物**：golden set拡張、回帰テスト、コスト/レイテンシ計測、失敗時の再試行・代替導線（§7.2）。

## M8 — 調査（検索）モジュール ✅（完了）

- **目標**：研究開発の実調査で使える入口。これまで「該当しそうな番号リスト」は外から
  もらう前提だったが、**検索ブリーフ（キーワード概念×CPC×出願人×期間）から候補番号を
  自分で見つける**。プロのサーチ式の作法（概念内OR・概念間AND）をそのままJSONにした。
- **成果物**：
  - `search/query.py`：検索ブリーフJSON → BigQueryコンソールに貼れるSQL（純関数・エスケープ機械保証）。
  - `search/rank.py`：決定論ランキング（タイトル3点/要約1点/CPC2点、逐語根拠スニペット、
    概念の取りこぼしは `needs_review`、ファミリー集約で重複排除）。
  - `search/report.py`：**調査ログMarkdown**（サーチ式記録・再現用SQL・根拠付き候補表）と
    `number,note` 形式CSV（既存パイプラインの入力にそのまま接続）。
  - `scripts/search_patents.py`：SQL発行 → コンソール実行 → エクスポートJSONをランク付け、の鍵ゼロ導線。`--live` も対応。
- **副産物（バグ修正）**：`normalize` が自分の正準出力（`US-9500000-B2`）をラウンドトリップ
  できない結合キー破壊バグを発見・修正（種別コード前のダッシュ対応＋回帰テスト）。
- **受け入れ**：`samples/search_query_SAMPLE.json` → SQL生成 → サンプルエクスポートのランク付け →
  `outputs/candidates.csv` を `run_pipeline.py`/`build_site.py` に直結。✅（テスト17件追加）

## M9 — 法的状態・JP対応 ⬜（次）

- 消滅/存続の法的状態（EPO OPS INPADOC、無料キー登録）と、JP実務の導線（BigQueryのJP行
  ＋ J-PlatPat参照URL）。failした特許を調査対象から自動で落とす（根拠つき）。

## M10 — 意味検索 ⬜

- キーワード検索の取りこぼし（言い換え・上位概念）を、要約embeddingの近傍探索で再ランク・
  補完。決定論チャネル（キーワード）をアンカーに、意味チャネルを recall 専用で重ねる
  ——スコアリングと同じ二channel思想。

## M11 — 調査レポート様式 ⬜

- 先行技術調査・FTO・SDI（定期監視テーマ）それぞれの定型レポート。監視（M6）と検索（M8）を
  つなぎ、「このサーチ式を毎週回して新着だけ通知」を1コマンド化。

---

## 進め方（各マイルストーン共通の作法）

1. 抽象（`PatentSource` 等）の裏に実装し、`run_pipeline` から差し替えで効く形にする。
2. **純関数（マッピング/判定整形）を切り出してテスト**（ライブ依存なしで検証できる単位を作る）。
3. マイルストーンは必ず **デモ実行＋テスト** で締める。
4. 出典・信頼度・`needs_review` を全成果物に貫通させる。
