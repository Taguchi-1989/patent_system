# 「頭脳」の動かし方 — APIで動かす vs サブスクのエージェントで動かす

意味判定（請求項要素が自社仕様にカバーされるか）の「頭脳」は **3通り**で動かせます。
どれを選んでも、決定論チャネル（strict）がアンカー兼ガードレールとして**常に並走**し、
P-NO-GUESS（根拠は仕様の逐語引用のみ・捏造は破棄）は**全モード共通**です。

```
                         ┌──────────────────────────── 頭脳の差し替え地点（Judge プロトコル）
 正準化→取得→要素分解 ──┤  strict（決定論・常時）  ＋  recall（下のどれか）
                         └──────────────────────────────────────────────────────
   ① 鍵なし          recall = LenientJudge（語幹・部分一致）              … すぐ動く
   ② APIで動かす     recall = Azure OpenAI / GitHub Models（従量課金）    … 無人・自動向き
   ③ サブスクのagent recall = AgentJudge（Claude Code/Copilot が黒子で記入）… 定額・対話向き
```

## どれを選ぶ？（早見表）

| | ① 鍵なし | ② API（Azure / GitHub Models） | ③ サブスクのエージェント（Claude Code / Copilot） |
|---|---|---|---|
| 鍵 | 不要 | APIキー（従量課金） | サブスク（定額）。APIキー不要 |
| コスト | 0 | トークン従量 | 月額サブスクの範囲内 |
| 精度（意味判定） | 中（語彙ベース） | 高 | 高 |
| 無人・自動運用 | ◎（CI/cron） | ◎（CI/cron で回せる） | △（人/エージェントが回す前提） |
| どこで動く | どこでも | サーバ/Actions | 対話セッション（Claude Code 等） |
| ハルシネーション | 構造的にゼロ | コードで逐語検証 | コードで逐語検証 |
| 向いている場面 | まず動かす・CI・デモ | 大量・定期・無人 | 対話レビュー・鍵を持ちたくない・少量精査 |
| CLI | `--llm` 省略 | `--llm azure` / `--llm github` | `--emit-agent-worksheet` → 記入 → `--llm agent --verdicts` |

> ざっくり指針：**無人で回す＝②API**、**対話で精度を上げる/鍵を持ちたくない＝③エージェント**、
> **まず動かす・CI＝①鍵なし**。②と③は排他ではなく、用途で使い分け（同じ Judge シーム）。

---

## ② APIで動かす（従量課金・無人向き）

詳細は [docs/llm-setup.md](llm-setup.md)。要点だけ：

```bash
pip install -r requirements-llm.txt
# .env に鍵（Azure: AZURE_OPENAI_* / GitHub: GITHUB_MODELS_TOKEN もしくは GITHUB_TOKEN）
py scripts/build_site.py ... --spec spec.md --llm azure     # or --llm github / --llm auto
```

- GitHub Actions なら `permissions: models: read` を足すと組み込み `GITHUB_TOKEN` で GitHub Models が使える＝**定期監視ジョブにそのまま組み込める**。
- 月次監視 cron など「人がいない所で回す」用途はこれ。

---

## ③ サブスクのエージェントで動かす（定額・APIキー不要）

「いま動いているエージェント（Claude Code / GitHub Copilot）」が黒子で判定する経路。
**APIキーを刺さない**ぶん、定額サブスクの範囲で精度を上げられます。これはこのリポジトリ
本来の「黒子エージェント」構想（[docs/architecture.md](architecture.md) §0-2）の実体です。

### 流れ（3ステップ）

```bash
# 1) 判定ワークシート（請求項要素＋対象仕様）を出力
py scripts/run_pipeline.py samples/demo_numbers.csv --source fixture \
   --fixtures-dir samples/demo_fixtures --spec samples/target_spec_SAMPLE.md \
   --emit-agent-worksheet work.json

# 2) エージェントが work.json を読み、各要素を埋める（下記）

# 3) 埋めた work.json を頭脳として使う
py scripts/build_site.py samples/demo_numbers.csv --source fixture \
   --fixtures-dir samples/demo_fixtures --spec samples/target_spec_SAMPLE.md \
   --llm agent --verdicts work.json
```

### 2) でエージェントが埋めるもの

`work.json` の各 `elements[]` に対し、`target_spec` を読んで:

- `verdict`: `MATCH` / `MISSING` / `UNCLEAR`
- `evidence_span`: **target_spec からの逐語コピー**（無ければ空。**言い換え・創作は禁止**）
- `confidence`: 0.0–1.0
- `rationale`: 一言

**Claude Code での具体的な頼み方（例）**：

> 「`work.json` を読み、各 element について `target_spec` がカバーするか判定して、
> `verdict` / `evidence_span`（仕様からの逐語コピー）/ `confidence` / `rationale` を
> 埋めて保存して。根拠は必ず仕様本文をそのままコピーすること。」

GitHub Copilot（コーディングエージェント / Chat）でも同様に依頼できます。
APIキーは使わず、サブスクのセッション内で完結します。

### ④ 安全網は同じ

`AgentJudge` は API 経路と**同一の検証**（`compare.coerce_verdict`）を通します：
逐語非一致の `evidence_span` は破棄され、根拠なき MATCH は UNCLEAR に降格。
未記入の要素は UNCLEAR（要確認）になり、勝手に埋めません。さらに strict（決定論）が
並走するので、エージェントの判定が語彙ベースと食い違えば **SN比が下がって「要確認」**に出ます。

---

## まとめ

- 頭脳の差し替え地点は 1 つ（`Judge` プロトコル / `build_channels()`）。
- ②API＝無人・従量、③サブスクのエージェント＝対話・定額。**鍵を入れる/入れないはここで選ぶ**。
- どのモードでも決定論アンカー＋P-NO-GUESS は不変。最終判断は専門家確認が前提です。
