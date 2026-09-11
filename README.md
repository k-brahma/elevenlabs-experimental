# elevenlabs voice lab

同じ音声エージェントを **2 通りで作って比べる**ための実験場。「どちらの構成をいつ選ぶか」を、
数字で言えるようにするのが目的。

- **A: Agents Platform** … ElevenLabs の Agent（音声認識・応答生成・発話・割り込みを丸ごと任せる）
- **B: 自前構成** … 自前の検索 + Gemini + ストリーミング TTS を自分でつなぐ

比べるのは 3 つ。

| 観点 | 測り方 |
|---|---|
| 遅延 | 質問を投げてから最初の音が出るまで（ms）。自前で計る（[計測の定義](#計測の定義)） |
| 費用 | 1 往復あたりのクレジット。A は会話ごとの `cost`（分単位）、B は TTS の文字数（[B の設計](#b-の設計)）。どちらも実行前後の残高の差と突き合わせる |
| 実装量 | 行数と、自分で面倒を見る必要があるものの数（割り込み、無音判定、再接続…） |

## なぜ web app ではないのか

共有できる形の音声対話は `obsidian-vault-web` の `/voice`（ブラウザ + WebRTC）で既に動いている。
ここで欲しいのは触れるデモではなく**数字**なので、ブラウザの音声処理層を挟まない Python にしている。

## 状態

**A・B とも 5 問流して並べ、正誤も入れた（2026-09-11）。残りは音声入力（Scribe）の追加。**

- [x] 題材データ（`corpus/`）と質問集（`scenarios/questions.json`）
- [x] クレジット残高の記録（`voicelab/credits.py`）
- [x] 計測結果の記録と表の出力（`voicelab/metrics.py`）
- [x] ノートの検索（`voicelab/search.py`）と Agent 用の client tool
- [x] Agent とツールの作成・更新（`voicelab/agent_setup.py`）
- [x] A: Agents Platform で 1 往復する（`voicelab/agents_path.py`）… 5 問 + 再測 1 回、計 6 往復
- [x] B: 検索 + Gemini + ストリーミング TTS で 1 往復する（`voicelab/custom_path.py`）… **実行待ち**
- [x] 5 問 × 2 構成を流して表にする（下の「A と B を並べる」）

## 使い方

### 最初の 1 回だけ（環境を作る）

```powershell
uv venv                       # .venv を作る（python -m venv .venv でも同じ）
uv sync --extra dev           # 依存 + テスト用を入れる
copy .env.example .env        # ELEVENLABS_API_KEY と GEMINI_API_KEY を書く
```

### 2 回目以降（uv を使わない）

`.venv` は**ごく普通の venv** で、uv 固有のものは入っていない。一度有効化すれば、
あとは素の Python のコマンドだけで完結する。

```powershell
.venv\Scripts\activate       # 有効化（プロンプトの頭に (elevenlabs) が付く）

python run.py credits                       # 残高
python run.py scenarios                     # 質問集
python run.py run agents --scenario rollback        # A で 1 問（課金あり）
python run.py run custom --scenario rollback        # B で 1 問（課金あり）
python run.py run agents --dry-run                  # 接続せず確認だけ（課金なし）
python run.py report                        # 表を作る
pytest -q                                   # テスト
```

`deactivate` で抜ける。

### モジュールを直接叩く

CLI のサブコマンドを覚えなくても、**モジュール 1 つを名指しで**動かせる。
どれも `if __name__ == '__main__':` が数行あるだけで、中身は下に書いた関数を呼ぶだけ。

| コマンド | 呼ばれる関数 | 課金 |
|---|---|---|
| `python -m voicelab.credits` | `credits.read_subscription()` | なし |
| `python -m voicelab.search ロールバック` | `search.search()` | なし |
| `python -m voicelab.metrics` | `metrics.render_report(load_runs())` | なし |
| `python -m voicelab.agent_setup` | `agent_setup.setup()` | なし |
| `python -m voicelab.agents_path rollback` | `agents_path.run_scenario()` | **あり** |
| `python -m voicelab.custom_path rollback` | `custom_path.run_scenario()` | **あり** |

下 2 つは 1 往復ぶん課金される。**残高の見張りは `cli.py` にあるので、この叩き方では効かない。**

REPL からでも同じ。CLI を通さずに関数を直接呼べるよう、表示と引数解析は `cli.py` に、
処理は各モジュールに分けてある（テスト 73 件も CLI を通さず関数を直接呼んでいる）。

```python
from voicelab import credits, search
from voicelab.config import load_env

search.search('ロールバック')                                  # 課金なし
credits.read_subscription(load_env()['ELEVENLABS_API_KEY'])  # 鍵を貼らずに済む形
```

### 同じことをする 4 つの書き方

どれも中身は同じ（`pyproject.toml` の `[project.scripts]` が
`voicelab = "voicelab.cli:main"` を宣言しているだけ）。

| 書き方 | 有効化 | 備考 |
|---|---|---|
| `python run.py credits` | 要 | 素の Python らしい形。`run.py` は 3 行 |
| `python -m voicelab credits` | 要 | `voicelab/__main__.py` を通る |
| `voicelab credits` | 要 | インストール時に作られた `.venv\Scripts\voicelab.exe` |
| `uv run voicelab credits` | 不要 | uv が `.venv` を選んでから実行する |

`python voicelab/cli.py` だけは**動かない**。ファイルを直接指定するとパッケージの一部
として読まれず、中の `from . import ...` が解決できないため。`run.py` はそれを避けるために置いてある。

依存を足すときだけ uv（または有効化した状態で `pip install`）が要る。
`uv add <パッケージ>` は `pyproject.toml` と `uv.lock` も更新するので、そちらが本筋。

### uv と pyproject.toml の関係

`pyproject.toml` は **Python 標準の設定ファイル**（PEP 621）で、uv 固有のものではない。
pip も setuptools も同じものを読む。uv は「そこに書いてあるとおりに `.venv` を揃える道具」。

| 場所 | 何を書くか | 誰が読むか |
|---|---|---|
| `[project] dependencies` | 必須の依存 | uv / pip |
| `[project.optional-dependencies]` | 任意の依存（ここでは `dev` と `conversation`） | `uv sync --extra dev` |
| `[project.scripts]` | コマンド名 → 関数（`voicelab = "voicelab.cli:main"`） | インストール時に `.exe` を作る |
| `[build-system]` | パッケージを組む道具（setuptools） | ビルド時 |
| `uv.lock` | 依存の**正確な版**。uv 固有（`package-lock.json` に相当） | uv |

uv のコマンドが実際にやること:

- `uv venv` … `.venv` を作る。名前を省くと `.venv`（ドット付き）。`python -m venv .venv` と同じ
- `uv sync` … `pyproject.toml` と `uv.lock` のとおりに `.venv` を**揃える**。
  足りないものを入れ、**余計なものを消す**。`--extra dev` を付け忘れると pytest が消えるのはこのため
- `uv add <パッケージ>` … `pyproject.toml` に 1 行足し、`uv.lock` を更新し、`.venv` に入れる。
  有効化した状態の `pip install` でも `.venv` には入るが、`pyproject.toml` は更新されない
- `uv run <コマンド>` … `.venv` を選んでから実行する。有効化していれば要らない

つまり **uv が要るのは環境を作るときと依存を足すときだけ**で、それ以外は素の Python でよい。


## 計測の定義

| 名前 | 意味 |
|---|---|
| `first_audio_ms` | 質問を送ってから、**最初の音の断片**が届くまで。体感の遅延はこれ |
| `reply_done_ms` | 質問を送ってから、**最後の音の断片**が届くまで |
| `credits` | 会話の `metadata.cost`。取れなければ 0 にして `note` に「費用未取得」と書く |

- 質問は**テキストで送る**（`send_user_message`）。マイクを使うと、部屋の雑音と無音判定の
  ばらつきがそのまま数字に乗る。代わりに、**この数字に音声認識の時間は含まれない**。
  B と比べるときは、B も同じくテキスト投入から測ること
- 「言い終わり」は、最初の音が来てから **1.5 秒**音が途切れたら、と決めている。サーバの終了イベント
  ではなく音の途切れで決めるのは、B でも同じ判定が書けるから。判定が違うと数字を並べられない
- 最初の音が **40 秒**来なければ諦める。記録は残し、`note` にタイムアウトと書く
- SDK の `callback_latency_measurement` は**使っていない**。あれは WebSocket の ping（ms）で、
  会話の遅延ではない

## 無音を送る必要があるか

**要らなかった。** マイクを開かず、音声を 1 バイトも送らずに `send_user_message` でテキストを
送るだけで、Agent はツールを呼んで音声で答えた（5 問すべて）。`run_scenario(..., send_silence=True)`
は残してあるが、既定は「送らない」のままでよい。

## なぜトンネルが要らないのか

検索ツールは **client tool**（Python 側で実行）にしている。Agent がツールを呼ぶと、その呼び出しは
会話の WebSocket 経由でこちらに届き、手元の関数の戻り値がそのまま LLM に渡る。ElevenLabs から
HTTP でこちらへ届く経路が無いので、公開 URL も cloudflared のようなトンネルも要らない。
（サーバ側で実行する webhook tool を使う構成なら、ローカル検証にはトンネルが要る。）

## B の設計

> LLM は当初 `gemini-2.5-flash` だったが、2026-09-11 に Gemini API が「新規ユーザーには提供終了」（404）を返したため、A・B とも `gemini-3.6-flash` に揃えた。A の最初の結果（上の表）は 2.5-flash のもので、3.6-flash で取り直した表は下にある。


`voicelab/custom_path.py`。直列のパイプラインで、A と同じ `Run` を返し、WAV と書き起こしも
同じ場所に置く（`results/audio/<id>_custom_<UTC>.wav`、`results/transcripts/<id>_custom_<UTC>.txt`）。

```
質問テキスト → 検索（上位 3 件） → Gemini をストリーミング → 文が確定するたびに TTS へ → 音
```

### A との違い

| | A: Agents Platform | B: 自前構成 |
|---|---|---|
| 検索の呼び方 | **LLM がツールを選ぶ**（client tool） | **選ばせない。先に検索して結果を渡す** |
| ノートに無い質問 | LLM がツールを呼ばずに「見当たりません」 | それでも検索する。0 件を渡して LLM に言わせる |
| LLM | `gemini-3.6-flash`（ElevenLabs 側が動かす） | 同じ `gemini-3.6-flash`（**自分で呼ぶ**） |
| 費用の出方 | 会話の `metadata.cost` に全部込み（分単位） | ElevenLabs は TTS の**文字数**、Gemini は**トークン**。別勘定 |
| 面倒を見るもの | ほぼ無し | 文の切り出し・WebSocket・受信スレッド・時刻の記録 |

ツール往復（LLM がツールを選ぶ → 検索 → LLM が答えを作る）が 1 回消えるぶん、B の方が
速いはず、という仮説を確かめるための構成。A の実測ではその往復が 1.5〜2 秒だった。

### 文単位のストリーミングである

**LLM の生成完了は待たない。** トークンを溜めて「。」「！」「？」で切り、文が 1 つ確定した
時点で TTS へ送る。半角の `.` では切らない（`v2.5` のような版番号で途中まで送ってしまうため）。

TTS は `wss://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream-input` に
`websockets` で**直接**つなぐ。SDK の `client.text_to_speech.convert_realtime` を使わなかったのは、

- 中の `text_chunker` の区切り文字が `. , ? ! ; : - ( ) [ ] }` と半角空白だけで、**日本語の「。」を知らない**
- その `text_chunker` は「次の断片が来て初めて 1 つ前を送る」作りで、**最初の文が 1 文ぶん遅れる**
- 戻り値が generator なので、音声の到着時刻が「こちらが `next()` を呼んだ時刻」になってしまう

の 3 点。受信は別スレッドで回して、**チャンクが届いた瞬間に時刻を打つ**（A の `MeasuringAudioInterface`
と同じ）。各文は `{"text": "文 ", "flush": true}` で送る。`flush` が無いと `chunk_length_schedule`
（既定 50 文字）ぶん溜まるまで生成が始まらず、短い返答では最初の音が遅れる。
出力は `pcm_16000`。A の録音と同じ 16kHz / 16bit / mono。

Gemini は `thinking_budget=0`（思考なし）で呼ぶ。2〜3 文の読み上げに推論は要らず、
`max_output_tokens` が 300 しかないので、思考でこの枠を使い切って**本文が空のまま終わる**のを避ける。

### 計測

`first_audio_ms` と `reply_done_ms` の定義は A と同じ（[計測の定義](#計測の定義)）。`t0` は
質問テキストを渡した時刻で、**TTS の WebSocket 接続と Gemini の client 作成は `t0` より前**に済ませる
（A も接続後に質問を送っているため）。CSV に入らない内訳は書き起こしに書く。

- 検索 ms / LLM 最初のトークン ms / LLM 完了 ms / TTS へ最初の文 ms
- TTS に送った文の数と文字数
- Gemini の `usage_metadata`（入力・出力・思考・合計トークン）

### 費用

`credits` 列には **TTS に送った文字数 × 0.5** を入れる（flash / turbo 系を API から使ったときの
1 文字あたりのクレジット。根拠と出典は `voicelab/custom_path.py` の `CREDITS_PER_CHARACTER`）。
これは**見積り**で、正は実行前後の残高の差。ただし残高の反映は遅れる。

**Gemini の費用は ElevenLabs のクレジットではないので `credits` に混ぜない。** トークン数として
書き起こしに残す。A ではこの分が会話の `cost` に溶けていて分けられない ―― そこも B との違い。

## 最初の結果（A、2026-09-11）

`results/report.md` と `results/runs.csv` に全行がある。音声は `results/audio/`（git には入れない）。

| 質問 | 最初の音まで | 言い終わりまで | クレジット | ツール |
|---|---:|---:|---:|---|
| デプロイ前の確認 | 2,722 / 2,926 ms | 3,504 / 3,622 ms | 74 / 65 | 呼んだ。1 位が期待どおり |
| ロールバック | 3,252 ms | 3,779 ms | 77 | 同上 |
| RRF とは | 2,987 ms | 3,710 ms | 71 | 同上 |
| 5/12 の定例 | 3,252 ms | 3,646 ms | 70 | 同上 |
| 来週の天気（ノートに無い） | 1,266 ms | 1,374 ms | 44 | 呼ばずに「見当たらない」 |

- **ツールを呼ぶ質問は最初の音まで 2.7〜3.3 秒**、呼ばない質問は 1.3 秒。差の 1.5〜2 秒がツール往復（LLM がツールを選ぶ → 検索 → LLM が答えを作る）のぶん
- 「言い終わり」は最後の音声チャンクが**届いた**時刻。音声はほぼ一括で届くので、再生し終わるのはこの後（録音は 1 問 17 秒前後）
- 費用は **1 往復 44〜77 クレジット**（会話の `metadata.cost`）。通話は 6 秒前後で、分単位の目安（730/分）より安く済んでいるのは、テキスト送信で無音の待ち時間が無いため。長い会話や放置ではその目安に戻る
- 会話メタデータの `cost` 合計は 401 だったが、直後に読んだ残高の差は 216。**残高の反映は遅れる**ので、費用は会話ごとの `cost` を正とする
- 返答は全問ノートを根拠にしていて、出典のノート名を口頭で添えている（`results/transcripts/`）。`correct` 列は**書き起こしの本文**で判定した（期待ノート名を出典として言っているか。無い質問は「見当たらない」と言っているか）。音声の長さと文字数の比（6〜8 字/秒）も全行で妥当で、途中で切れた録音は無い。人が聞いて確かめる価値があるのは読み方だけで、A の「レシピプロカル」（LLM の書いたカタカナが誤り）と「二千二十六年」（漢数字読み）が候補

## A と B を並べる（どちらも gemini-3.6-flash、2026-09-11）

同じ 5 問を、同じ検索関数・同じ声・同じ TTS モデル・同じ LLM で流した。全行は `results/runs.csv`。

| 質問 | A 最初の音 | B 最初の音 | B の内訳: LLM 初トークン | A クレジット | B クレジット（TTS 見積り） |
|---|---:|---:|---:|---:|---:|
| デプロイ前の確認 | 2,479 ms | 3,460 ms | 3,133 ms | 100 | 55 |
| ロールバック | 2,653 ms | 6,224 ms | 5,925 ms | 100 | 50 |
| RRF とは | 2,633 ms | 4,229 ms | 3,869 ms | 98 | 50 |
| 5/12 の定例 | 1,871 ms | 2,845 ms | 2,397 ms | 88 | 50 |
| 来週の天気（無い） | 2,049 ms | 1,674 ms | 1,481 ms | 85 | 16 |

読み方:

- **A の方が速く、ばらつきも小さい**（1.9〜2.7 秒）。B は 1.7〜6.2 秒で、ほぼ全部が **Gemini の最初のトークンまでの時間**。検索は 1〜2 ms、TTS は最初の文を送ってから 200〜300 ms で音が来る。つまり B の遅さは自前の作りではなく、公開 API 経由の Gemini（`thinking_level='low'` でも思考に 300 トークン前後使う）の応答速度
- A は ElevenLabs の中で同じモデルを呼んでいるはずだが、ツール往復（LLM → ツール → LLM）込みで B の単発呼び出しより速い。中で何をしているか（思考の抑え方、プロビジョニング、地理）はこちらからは見えない。**「同じモデル名でも、誰がどう呼ぶかで 1〜3 秒変わる」**が、この比較で一番大きい発見
- 費用の出方が違う。A は会話ごとに **85〜100 クレジット**（LLM 込み・通話時間ベース）。B は **TTS の文字数ぶん 16〜55 クレジット**＋Gemini のトークン（入力 450 前後 / 出力 60 前後 / 思考 300 前後。ElevenLabs のクレジットではない）。合算すると B の方が安いが、二社に請求が分かれる
- 2.5-flash のときの A（上の表）は 2.7〜3.3 秒だったので、**A は 3.6-flash で 0.5〜1 秒速くなった**。ノートに無い質問は 2.5 ではツールを呼ばず 1.3 秒、3.6 では律儀にツールを呼んで 2.0 秒
- `results/report.md` の A の中央値は 2.5-flash と 3.6-flash の両方の行を含む（`runs.csv` に LLM の列が無いため）。分けて見るときは日時で切る

どちらを選ぶか（この題材での結論。一般化はしない）:

- **速さと手離れ**なら A。割り込み・無音判定・再接続を自分で書かなくてよく、それでいて速い
- **費用の内訳の見える化と、LLM や検索の差し替え自由度**なら B。ただし LLM の応答速度がそのまま体感に出るので、モデルと呼び方（思考の抑制、リージョン）を自分で詰める覚悟が要る

## 声と数字の読み（2026-09-11）

- 当初の声 Sarah は ElevenLabs の検証済み言語に日本語が無い。無料でも使える premade のうち **George / Alice / Jessica は日本語が検証済み**（`GET /v1/voices` の `verified_languages`）。同じ文を 4 声で読ませて聞き比べ（`results/audio/voice-sample_*.wav`）、**Jessica** に切り替えた（`.env` の `ELEVENLABS_VOICE_ID`）
- 年号の読みが不自然だったので、prompt に「算用数字で書く、日付は月日、年は省く」を足した。**B は従うが A は従わない**: A の返答は「五月十二日」「二十件から十件」と漢数字のまま（B は「5月12日」「20から10」）。同じモデル名でも、ElevenLabs の Agent 側で読み上げ向けの整形（数字の漢字化）が挟まっているように見える。TTS がどの書き方を一番自然に読むかは `results/audio/date-sample_*.wav`（Jessica、5 通り）で聞き比べる
- **原因は Agent の TTS 設定 `text_normalisation_type`** だった。既定の `system_prompt` は LLM に「数字を語で書け」と指示するので、日本語では漢数字（五月十二日、二十件）になり、TTS の読みが崩れる。`elevenlabs`（生成後に ElevenLabs 側で整形）にすると LLM は算用数字のまま書き（5月12日、20から10）、読みが直った。遅延の増加は測定誤差の範囲（2.06 秒）。`agent_setup.py` で固定
- B の「たどたどしさ」は文ごとに TTS へ送る作りに由来する可能性がある。同じ文を flash_v2_5 と multilingual_v2 で一括合成した比較が `results/audio/model-sample_*.wav`
- 2026-09-11 16:30 UTC 以降の `runs.csv` の行は Jessica。それ以前は Sarah。18:00 UTC 以降の A は正規化 `elevenlabs`

## 費用の注意

ここは **A（会話）の話**。B は会話ではなく TTS なので、消費は喋った文字数ぶん（1 往復 50 クレジット前後）で、
放置しても増えない。とはいえ残高の見張り（`MIN_CREDITS`）は A と共通の門をくぐらせている。

**会話は分単位でクレジットを消費する。** 2026-09-10 の実測で約 730 クレジット/分だった。
無料プランの 10,000 クレジット/月は実質 13 分、Starter の 30,000 でも 40 分ほどしかない。

- 計測は 1 回で終わらせる。同じシナリオを何度も流さない
- 会話を始めたら必ず終わらせる。放置したタブが 5 分走って 4,168 クレジット（Starter の 14%）消えた事故がある
- `voicelab credits` を実行の前後で回し、`results/runs.csv` に消費を残す

## 題材データについて

`corpus/` は**この実験のために書いた架空のノート**。実際の vault は使わない。

- 個人情報が混ざらないので、そのまま公開できる
- 誰が動かしても同じ結果になるので、比較として意味がある
