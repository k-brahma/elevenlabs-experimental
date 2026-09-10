# elevenlabs voice lab

同じ音声エージェントを **2 通りで作って比べる**ための実験場。「どちらの構成をいつ選ぶか」を、
数字で言えるようにするのが目的。

- **A: Agents Platform** … ElevenLabs の Agent（音声認識・応答生成・発話・割り込みを丸ごと任せる）
- **B: 自前構成** … Scribe（音声認識）+ 自前の検索 + ストリーミング TTS を自分でつなぐ

比べるのは 3 つ。

| 観点 | 測り方 |
|---|---|
| 遅延 | 質問を投げてから最初の音が出るまで（ms）。自前で計る（[計測の定義](#計測の定義)） |
| 費用 | 1 往復あたりのクレジット。実行前後の残クレジットの差と、会話ごとの `cost` |
| 実装量 | 行数と、自分で面倒を見る必要があるものの数（割り込み、無音判定、再接続…） |

## なぜ web app ではないのか

共有できる形の音声対話は `obsidian-vault-web` の `/voice`（ブラウザ + WebRTC）で既に動いている。
ここで欲しいのは触れるデモではなく**数字**なので、ブラウザの音声処理層を挟まない Python にしている。

## 状態

**A は 5 問流して数字が出た（2026-09-11）。B はこれから。**

- [x] 題材データ（`corpus/`）と質問集（`scenarios/questions.json`）
- [x] クレジット残高の記録（`voicelab/credits.py`）
- [x] 計測結果の記録と表の出力（`voicelab/metrics.py`）
- [x] ノートの検索（`voicelab/search.py`）と Agent 用の client tool
- [x] Agent とツールの作成・更新（`voicelab/agent_setup.py`）
- [x] A: Agents Platform で 1 往復する（`voicelab/agents_path.py`）… 5 問 + 再測 1 回、計 6 往復
- [ ] B: Scribe + 検索 + ストリーミング TTS で 1 往復する
- [ ] 5 問 × 2 構成を流して表にする（A の分は `results/report.md`）

## 使い方

```bash
uv venv
uv sync                       # 会話まで含めて動く（SDK が入る）
uv sync --extra dev           # テストを走らせるとき
uv sync --extra conversation  # スピーカーで鳴らしたいとき（pyaudio）
copy .env.example .env        # ELEVENLABS_API_KEY を書く
```

```bash
# 今の残クレジットを見る（API を叩くが課金はされない）
uv run voicelab credits

# 質問集を見る
uv run voicelab scenarios

# A の Agent とツールを作る／更新する（課金されない。何度実行しても同じ状態になる）
uv run voicelab setup-agent

# 接続せずに確認する。ツールが期待のノートを引けるかはここで分かる（課金されない）
uv run voicelab run agents --dry-run
uv run voicelab run agents --dry-run --scenario deploy-check

# 本番。クレジットを消費する
uv run voicelab run agents --scenario deploy-check   # 1 問だけ
uv run voicelab run agents                           # 全 5 問
uv run voicelab run agents --no-audio                # WAV を残さない

# 記録済みの結果から表を作る
uv run voicelab report
```

`setup-agent` は、ツール `search_notes` と Agent `voicelab-a` を**名前で探して、無ければ作り、
あれば上書きする**。作った Agent の id は `.env` の `ELEVENLABS_AGENT_ID` に書き戻す。
Agent の指示は `prompts/agent_system.txt`、LLM は `.env` の `VOICELAB_LLM`（既定 `gemini-2.5-flash`）。

`run agents` は実行前に残高を読み、**1,500 クレジットを切っていたら止まる**（`--force` で続行）。
1 問ごとに新しい会話を開く。前の質問の文脈が残ると、ツールを呼ばずに前の答えを流用してしまい、
1 往復の計測にならないため。

結果は `results/runs.csv`、音は `results/audio/`、返答と時刻は `results/transcripts/`。
`correct`（期待したノートを根拠に答えたか）は**音を聞いて手で入れる**。自動判定にすると、
「それらしい文字列が入っていれば正解」になって、比較の意味がなくなる。

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
- 返答は全問ノートを根拠にしていて、出典のノート名を口頭で添えている（`results/transcripts/`）。声を聞いての正誤判定（`correct` 列）は未記入

## 費用の注意

**会話は分単位でクレジットを消費する。** 2026-09-10 の実測で約 730 クレジット/分だった。
無料プランの 10,000 クレジット/月は実質 13 分、Starter の 30,000 でも 40 分ほどしかない。

- 計測は 1 回で終わらせる。同じシナリオを何度も流さない
- 会話を始めたら必ず終わらせる。放置したタブが 5 分走って 4,168 クレジット（Starter の 14%）消えた事故がある
- `voicelab credits` を実行の前後で回し、`results/runs.csv` に消費を残す

## 題材データについて

`corpus/` は**この実験のために書いた架空のノート**。実際の vault は使わない。

- 個人情報が混ざらないので、そのまま公開できる
- 誰が動かしても同じ結果になるので、比較として意味がある
