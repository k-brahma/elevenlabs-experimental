# elevenlabs voice lab

同じ音声エージェントを **2 通りで作って比べる**ための実験場。応募資料に載せる「どちらの構成をいつ選ぶか」を、
数字で言えるようにするのが目的。

- **A: Agents Platform** … ElevenLabs の Agent（音声認識・応答生成・発話・割り込みを丸ごと任せる）
- **B: 自前構成** … Scribe（音声認識）+ 自前の検索 + ストリーミング TTS を自分でつなぐ

比べるのは 3 つ。

| 観点 | 測り方 |
|---|---|
| 遅延 | 話し終わってから最初の音が出るまで（ms）。SDK の latency コールバックと自前の計測 |
| 費用 | 1 往復あたりのクレジット。実行前後の残クレジットの差と、会話ごとの `cost` |
| 実装量 | 行数と、自分で面倒を見る必要があるものの数（割り込み、無音判定、再接続…） |

## なぜ web app ではないのか

共有できる形の音声対話は `obsidian-vault-web` の `/voice`（ブラウザ + WebRTC）で既に動いている。
ここで欲しいのは触れるデモではなく**数字**なので、ブラウザの音声処理層を挟まない Python にしている。
Python SDK には遅延計測のコールバックが最初から付いている。

## 状態

**骨組みだけ。まだ ElevenLabs の会話 API は叩いていない。**

- [x] 題材データ（`corpus/`）と質問集（`scenarios/questions.json`）
- [x] クレジット残高の記録（`voicelab/credits.py`）
- [x] 計測結果の記録と表の出力（`voicelab/metrics.py`）
- [ ] A: Agents Platform で 1 往復する
- [ ] B: Scribe + 検索 + ストリーミング TTS で 1 往復する
- [ ] 5 問 × 2 構成を流して表にする

## 使い方

```bash
uv venv
uv sync                       # 骨組みだけ（標準ライブラリのみ。すぐ終わる）
uv sync --extra conversation  # 会話を実装するときに ElevenLabs SDK を入れる
copy .env.example .env        # ELEVENLABS_API_KEY を書く
```

```bash
# 今の残クレジットを見る（API を叩くが課金はされない）
uv run voicelab credits

# 記録済みの結果から表を作る
uv run voicelab report
```

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
