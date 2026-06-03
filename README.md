# patent_system — AI自走前提の特許調査支援システム

別システム等から流入する「該当しそうな特許番号リスト」を入力に、公開特許を自動取得し、
要約・比較表・継続監視レポートを極力人手なしで生成する基盤。

- 要件定義：[patent_ai_requirements_revised.md](patent_ai_requirements_revised.md)
- **設計（批判的レビュー反映済み）：[docs/architecture.md](docs/architecture.md)** ← まずこれ
- **決定論パイプライン化＋自校とワークフロー化の解説：[docs/workflow.html](docs/workflow.html)**（ブラウザで開く）

## 前提（重要な現実制約）

- **APIキー/本人確認は取得できない前提**で設計。USPTO ODP（**ID.me本人確認**必須）・JPO API（**受付終了**）は使わない。
- データ取得の主軸は **鍵不要ルート**：**Google Patents on BigQuery Sandbox**（Googleログインのみ・カード不要・月1TB無料）＋ **USPTO一括データ**（アカウント不要）。開発・デモは **FixtureSource**（鍵ゼロ）。
- **LLMキーも不要**：要約・比較の意味判定はエージェント（Claude Code）が黒子として実施。確定的な抽出・構造化はコードで実行。
- パイプラインは `PatentSource` 抽象にのみ依存（**Providerパターン**）。`FixtureSource` を `BigQuerySource` に差し替えるだけで実データが流れる。
- 継続監視の定期実行は **GitHub Actions cron**（揮発する Colab では行わない）。

## いま動くもの（鍵不要）

```bash
py tests/test_numbers.py          # 正準番号エンジンのテスト（11/11 green）
py scripts/normalize_csv.py       # samples の番号を正準化して表示（曖昧入力は REVIEW 表示）
py scripts/run_pipeline.py        # 全工程を通す: 正準化→取得(Fixture)→抽出要約→比較表→outputs/report.md
py scripts/pipeline_selfcheck.py  # 決定論パイプラインを2回走らせ8つの不変条件ゲートを自己検証（CIゲート・合否で終了コード）
```

`scripts/pipeline_selfcheck.py` は決定論バックボーン（正準化→取得→要約→比較(HeuristicJudge)→
スナップショット→出力）を回し、P-NO-GUESS・出典・決定性・免責などの不変条件を機械的に検証します。
「形」と各ゲートは `src/patentkit/pipeline/contract.py` に宣言され、解説は [docs/workflow.html](docs/workflow.html)。

`src/patentkit/normalize` は **依存ゼロ（純標準ライブラリ）** で、Colab の素のセルでも動きます。
US / EP / WO / JP（西暦・元号・登録番号）を構造化し、曖昧な場合は `needs_review` を立てます
（沈黙して誤接合しない = P-NO-GUESS 原則）。

## 次の一歩（鍵不要で実データへ）

最小の手間で実データに繋ぐ＝**Google Patents on BigQuery Sandbox**（既存Gmailでログインするだけ・カード不要・月1TB無料）。
`BigQuerySource` を実装すれば、`run_pipeline.py` の `source = FixtureSource()` を差し替えるだけで実データが流れます。
（補助として、アカウント不要の **USPTO一括データ**を `BulkDataSource` で追加可能。）

（任意）EPO OPS の鍵が取得できた場合のみ、`.env` に入れて `py scripts/verify_sources.py` で疎通確認。

## 構成

| パス | 役割（docs/architecture.md の番号） |
|---|---|
| `src/patentkit/normalize/` | 1. Ingestion：正準番号モデル ✅ |
| `src/patentkit/connectors/` | 2. Retrieval：Fixture / **BigQuery** / **USPTO一括** ✅（鍵不要） |
| `src/patentkit/state/` | 3. State/Snapshot：スナップショット＋差分エンジン ✅ |
| `src/patentkit/analyze/` | 4. Analysis：抽出要約＋意味比較 MATCH/MISSING/UNCLEAR ✅ |
| `src/patentkit/export/` | 5. Presentation：Markdown / 差分 / **静的HTMLサイト** ✅ |
| `src/patentkit/pipeline/` | 決定論パイプラインの「形」宣言＋自校ゲート（contract.py）✅ |
| `scripts/` | run_pipeline / monitor / build_site / eval_compare / **pipeline_selfcheck** / verify_sources |
| `.github/workflows/monitor.yml` | 6. 監視自動化（GitHub Actions cron）✅ |
| `tests/` | Eval/Quality（90 tests） |

進捗は [docs/roadmap.md](docs/roadmap.md)（M0–M6 完了、M7 残）。

## 継続監視の自動化（M6）

`.github/workflows/monitor.yml` が **GitHub Actions の cron**（毎週水曜 06:00 UTC）で監視を回す。
揮発する Colab ではなく Actions を使う（[docs/architecture.md](docs/architecture.md) §1）。

- スナップショットは `monitor_state/` に保存し、**Actions が変更をコミットして戻す**＝Git履歴が監視の監査証跡（§7.1）。
- 差分レポートは成果物（artifact）としてアップロード。
- **有効化**：このリポジトリを GitHub に push するだけで cron が動く。手動実行は Actions タブの "Run workflow"。
- **実データへ切替**：`monitor.yml` の monitor ステップの `--source` を差し替え
  （`--source bulk --bulk-files <DLした週次XML>` 等。ヘッダのコメント参照）。

## 免責

本システムの出力は **AIによる支援結果であり、侵害の有無や法的結論を確定しません。
最終判断は弁理士・弁護士等の専門家確認を前提とします。**
