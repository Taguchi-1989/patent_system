# LLM「頭脳」セットアップガイド（鍵あり / 鍵なし）

このシステムのスコアリングは **2チャネル構成**です。

- **決定論チャネル（strict）** … 語彙の重なり。再現可能な**アンカー兼ガードレール**。常に動く。
- **意味チャネル（recall）** … 言い換え・上位概念を拾う。ここに **LLM を「頭脳」として差し込める**。

LLM を入れても入れなくても動きます。下表から選んでください。

| モード | 鍵 | CLI | いつ使う |
|---|---|---|---|
| 鍵なし（既定） | 不要 | （`--llm` 省略） | まず動かす・CI・デモ。ハルシネーション・ゼロ |
| Azure OpenAI | `AZURE_OPENAI_*` | `--llm azure` | 自前の Azure リソースがある |
| GitHub Models | `GITHUB_MODELS_TOKEN` か `GITHUB_TOKEN` | `--llm github` | GitHub / Copilot の鍵で手早く精度を上げたい |
| 自動 | 上のどちらか | `--llm auto` | 鍵があれば使い、無ければ鍵なしに自動フォールバック |

> **設計の肝**：LLM はあくまで recall チャネル。strict（決定論）が並走して **SN比＝両者の一致度**を出し、
> 乖離・中間は自動で「要確認」に落ちます。だから LLM が外しても決定論が引き戻す。

---

## 0. 鍵なしモード（何もしなくて良い）

```bash
py scripts/build_site.py samples/demo_numbers.csv --source fixture \
   --fixtures-dir samples/demo_fixtures --spec samples/target_spec_SAMPLE.md
py -m http.server --directory site   # → http://localhost:8000
```

`requirements-llm.txt` のインストールも `.env` も不要。`openai` パッケージも import されません。

---

## 1. 鍵ありモード共通の準備

```bash
pip install -r requirements-llm.txt     # openai パッケージ（任意依存）
cp .env.example .env                     # 雛形をコピー（.env は git 管理外）
```

`.env` に**使うプロバイダのブロックだけ**を埋めます（下記 2 / 3）。
`--llm` を付けて実行すれば、その鍵が頭脳になります。鍵が見つからなければ、
必要な環境変数名を表示して安全に停止します（捏造で進めません）。

---

## 2. Azure OpenAI

`.env`：

```ini
AZURE_OPENAI_ENDPOINT=https://<your-resource>.openai.azure.com
AZURE_OPENAI_API_KEY=<your-key>
AZURE_OPENAI_DEPLOYMENT=<chat-model-deployment-name>   # 例: gpt-4o-mini を指す自分のデプロイ名
AZURE_OPENAI_API_VERSION=2024-10-21                    # リソースが対応する版に合わせる
```

実行：

```bash
py scripts/build_site.py samples/demo_numbers.csv --source fixture \
   --fixtures-dir samples/demo_fixtures --spec samples/target_spec_SAMPLE.md --llm azure
```

- `AZURE_OPENAI_DEPLOYMENT` は **モデル名ではなくデプロイ名**（Azure では `model=` にデプロイ名を渡す）。
- `AZURE_OPENAI_API_VERSION` はリソースが対応する版に合わせる（既定 `2024-10-21`）。

---

## 3. GitHub Models（GitHub / Copilot トークン）

OpenAI 互換。**GitHub の鍵で動く**ので、Copilot 利用者や GitHub ユーザはすぐ試せます。

### 3a. ローカル（PAT）

1. GitHub → **Settings → Developer settings → Personal access tokens → Fine-grained tokens**。
2. 新規トークンを作り、**Permissions に `Models: read`** を付与。
3. `.env`：

```ini
GITHUB_MODELS_TOKEN=github_pat_xxx          # models:read 権限の PAT
GITHUB_MODELS_MODEL=openai/gpt-4o-mini      # publisher/model 形式
GITHUB_MODELS_ENDPOINT=https://models.github.ai/inference
```

実行：

```bash
py scripts/build_site.py samples/demo_numbers.csv --source fixture \
   --fixtures-dir samples/demo_fixtures --spec samples/target_spec_SAMPLE.md --llm github
```

`GITHUB_MODELS_TOKEN` が無ければ `GITHUB_TOKEN` を使います。

### 3b. GitHub Actions（組み込みトークン）

ワークフローに `models: read` 権限を付けるだけで、組み込みの `GITHUB_TOKEN` が使われます。

```yaml
permissions:
  contents: read
  models: read          # ← これで GITHUB_TOKEN が GitHub Models を叩ける
steps:
  - uses: actions/checkout@v4
  - run: pip install -r requirements.txt -r requirements-llm.txt
  - run: |
      py scripts/build_site.py samples/demo_numbers.csv --source fixture \
        --fixtures-dir samples/demo_fixtures --spec samples/target_spec_SAMPLE.md --llm github
```

> 既存の監視ワークフロー（`.github/workflows/monitor.yml`）は鍵なしのまま回ります。
> LLM を回したいジョブにだけ `models: read` と `--llm github` を足してください。

---

## 4. ハルシネーション対策（鍵ありでも維持）

LLM は自由に文章を作れるため、対策を**コード側で強制**しています
（[`analyze/llm_judge.py`](../src/patentkit/analyze/llm_judge.py)）。

1. モデルへの指示：「根拠は仕様からの**逐語コピー**のみ。言い換え・創作禁止」。
2. 返ってきた `evidence_span` が対象仕様の**実在の部分文字列でなければコード側で破棄**。
3. 根拠が空の MATCH は `ElementVerdict.__post_init__` が **UNCLEAR に降格**。

→ どのプロバイダでも「根拠なき断定」は構造的に出せません。
ネットワーク障害・JSON 崩れも UNCLEAR（要確認）に倒し、決して捏造で前進しません。

---

## 5. トラブルシュート

| 症状 | 原因 / 対処 |
|---|---|
| `--llm azure\|github` で「鍵が見つかりません」と停止 | `.env` の該当変数が未設定。表示された変数名を設定する |
| `openai パッケージが必要です` | `pip install -r requirements-llm.txt` |
| Azure で 404 / DeploymentNotFound | `AZURE_OPENAI_DEPLOYMENT` がモデル名になっている。**デプロイ名**にする |
| Azure で api-version エラー | `AZURE_OPENAI_API_VERSION` をリソース対応版に変更 |
| GitHub で 401 / 403 | PAT に `models:read` が無い／Actions に `permissions: models: read` が無い |
| `response_format` 非対応モデル | コードが自動で JSON モード無しに**リトライ**（プロンプトで JSON を強制し、頑健にパース） |
| 結果がぶれる | LLM は非決定的。`temperature=0` で運用。最終判断は専門家確認が前提 |

---

## 6. 別プロバイダを足したいとき（拡張）

意味チャネルは `Judge` プロトコル（`judge(element, target_spec, claim_context) -> ElementVerdict`）を
満たせば何でも差し込めます。OpenAI 互換 API なら共通実装をそのまま使えます：

```python
from openai import OpenAI
from patentkit.analyze.llm_judge import OpenAICompatibleJudge
from patentkit.analyze.score import build_channels, score_all

client = OpenAI(base_url="https://your-endpoint/v1", api_key="...")
judge = OpenAICompatibleJudge(client=client, model="your-model")
channels = build_channels(judge)            # {"strict": 決定論, "recall": あなたのLLM}
scores = score_all(target_spec, summaries, channels=channels)
```

完全に独自の頭脳（OpenAI 非互換）でも、`judge()` を実装したクラスを `build_channels()` に渡すだけです。
`evidence_span` は対象仕様の逐語部分文字列を返すこと（P-NO-GUESS）。
