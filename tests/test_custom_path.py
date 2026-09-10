"""B（自前構成）の「文の切り出し」「prompt の組み立て」「費用の換算」「1 往復の流れ」。

**ネットワークを使わない。** LLM も TTS もダミーを差し込む。クレジットも Gemini の
トークンも 1 も使わずに、時刻の打ち方と WAV の中身まで確かめられるようにしてある。
"""

import json
import wave

import pytest

from voicelab import custom_path
from voicelab.custom_path import AudioSink, SentenceBuffer, Turn


class FakeClock:
    """呼ばれるたびに好きな値を返す時計（A のテストと同じ道具）。"""

    def __init__(self, values):
        self.values = list(values)

    def __call__(self) -> float:
        return self.values.pop(0)


class FakeChunk:
    """Gemini のストリームの断片のふり。``text`` と ``usage_metadata`` だけ持つ。"""

    def __init__(self, text=None, usage=None):
        self.text = text
        self.usage_metadata = usage


class FakeUsage:
    def __init__(self, **counts):
        for key, value in counts.items():
            setattr(self, key, value)

    def __getattr__(self, _name):  # 未設定の項目は None（SDK も同じ形）
        return None


class FakeTts:
    """文を受け取ったら、その場で音を 1 つ流す TTS のふり。

    「文が確定するたびに送っている」ことと、音の到着時刻の計算を、
    ネットワークなしで確かめるためのもの。
    """

    def __init__(self, sink, clock, *, audio_per_sentence=b'\x01\x02'):
        self.sink = sink
        self.sent: list[str] = []
        self.sent_sentences = 0
        self.sent_chars = 0
        self.first_send_at = None
        self.finished = False
        self.error = None
        self._clock = clock
        self._audio = audio_per_sentence

    def send(self, sentence):
        if self.first_send_at is None:
            self.first_send_at = self._clock()
        self.sent.append(sentence)
        self.sent_sentences += 1
        self.sent_chars += len(sentence + custom_path.TTS_SEPARATOR)
        self.sink.add(self._audio)

    def finish(self):
        self.finished = True

    def wait(self, timeout):
        return True

    def close(self):
        pass


# --------------------------------------------------------------------------- 文の切り出し


def test_文末の記号で切り出す():
    buffer = SentenceBuffer()
    assert buffer.feed('デプロイ手順に') == []
    assert buffer.feed('よると、確認が要ります。次に') == ['デプロイ手順によると、確認が要ります。']
    assert buffer.feed('バックアップです。') == ['次にバックアップです。']


def test_感嘆符と疑問符でも切る():
    assert custom_path.split_sentences('本当ですか？はい！') == ['本当ですか？', 'はい！']


def test_一つの断片に複数の文が入っていても切る():
    buffer = SentenceBuffer()
    assert buffer.feed('一つ目。二つ目。三つ') == ['一つ目。', '二つ目。']
    assert buffer.flush() == ['三つ']


def test_句点が無いまま終わった残りは_flush_で拾う():
    buffer = SentenceBuffer()
    assert buffer.feed('句点がありません') == []
    assert buffer.flush() == ['句点がありません']
    assert buffer.flush() == []  # 二度は出さない


def test_空文字と空白だけなら何も出さない():
    buffer = SentenceBuffer()
    assert buffer.feed('') == []
    assert buffer.feed('   ') == []
    assert buffer.flush() == []
    assert custom_path.split_sentences('') == []


def test_半角ピリオドでは切らない():
    """``v2.5`` のような版番号で途中まで送らないため（SENTENCE_ENDINGS の意図）。"""
    assert custom_path.split_sentences('モデルは v2.5 です。') == ['モデルは v2.5 です。']


# --------------------------------------------------------------------------- system prompt


def test_system_prompt_に検索結果が入る():
    hits = [{'note': 'デプロイ手順', 'heading': '事前確認', 'excerpt': 'ステージングで確認する'}]
    prompt = custom_path.build_system_prompt(hits, base='あなたは音声アシスタントです。')

    assert 'あなたは音声アシスタントです。' in prompt
    assert custom_path.SEARCH_PREAMBLE in prompt
    assert 'デプロイ手順' in prompt
    assert 'ステージングで確認する' in prompt
    # LLM が読む形は search_notes の戻り値と同じ（results の配列）
    payload = prompt[prompt.index('{"results"') :]
    assert json.loads(payload)['results'] == hits


def test_system_prompt_零件のときは見当たらないと言わせる():
    prompt = custom_path.build_system_prompt([], base='土台')
    assert custom_path.SEARCH_EMPTY in prompt
    assert '見当たりません' in prompt
    assert 'results' not in prompt


def test_system_prompt_の土台は_A_の指示を流用する():
    prompt = custom_path.build_system_prompt([])
    assert '音声アシスタント' in prompt  # prompts/agent_system.txt の文言
    assert '2〜3 文' in prompt  # 返答の長さは A と同じ条件にする
    assert 'ノート名' in prompt


def test_system_prompt_から_A_専用のツールの行は落とす():
    """B に道具は無い。呼べない道具の名前が残っていると LLM が探しにいく。"""
    prompt = custom_path.build_system_prompt(
        [{'note': 'デプロイ手順', 'heading': None, 'excerpt': ''}]
    )
    assert 'search_notes' not in prompt
    assert '記憶や推測で補ってはいけません' in prompt  # 趣旨は前置きが引き継ぐ


# --------------------------------------------------------------------------- 費用


def test_費用は文字数かける係数():
    assert custom_path.CREDITS_PER_CHARACTER == 0.5
    assert custom_path.estimate_credits(120) == 60
    assert custom_path.estimate_credits(0) == 0


def test_費用は四捨五入して整数にする():
    """``Run.credits`` は int の列なので、端数はここで落とす。"""
    assert custom_path.estimate_credits(101) == 50  # 50.5 → 銀行丸めで 50
    assert isinstance(custom_path.estimate_credits(101), int)


# --------------------------------------------------------------------------- 時刻


def test_音の到着時刻は_A_の関数で計算する():
    sink = AudioSink(clock=FakeClock([100.5, 101.0, 103.25]))
    for _ in range(3):
        sink.add(b'\x00\x00')
    assert custom_path.audio_window_ms(100.0, sink.times) == (500, 3250)


def test_音が来ていなければゼロ():
    assert custom_path.audio_window_ms(100.0, []) == (0, 0)


# --------------------------------------------------------------------------- 1 往復


def _turn(chunks, *, hits=None, clock_values=None):
    clock = FakeClock(clock_values or [])
    sink = AudioSink(clock=clock)
    tts = FakeTts(sink, clock)
    turn = custom_path.run_turn(
        'デプロイの前に確認することを教えて',
        hits=hits if hits is not None else [{'note': 'デプロイ手順', 'heading': '事前確認'}],
        llm_chunks=chunks,
        tts=tts,
        sink=sink,
        t0=100.0,
        search_done_at=100.02,
        clock=clock,
    )
    return turn, tts


def test_一往復の流れ_文ごとに送って時刻が並ぶ():
    chunks = [
        FakeChunk('デプロイ手順によると、'),
        FakeChunk('ステージングの確認が要ります。'),  # ここで 1 文目が確定 → 送る
        FakeChunk('次にバックアップです。'),  # 2 文目
        FakeChunk(None, FakeUsage(prompt_token_count=310, candidates_token_count=42, total_token_count=352)),
    ]
    # 時計: 初トークン → 1文目送信 → 1音目 → 2音目 → LLM 完了
    turn, tts = _turn(chunks, clock_values=[100.1, 100.4, 100.55, 100.8, 100.95])

    assert tts.sent == ['デプロイ手順によると、ステージングの確認が要ります。', '次にバックアップです。']
    assert tts.finished is True
    assert turn.first_token_ms == 100  # 100.1 - 100.0
    assert turn.tts_first_send_ms == 400
    assert turn.first_audio_ms == 550
    assert turn.reply_done_ms == 800
    assert turn.llm_done_ms == 950
    assert turn.search_ms == 20
    assert turn.usage['prompt_token_count'] == 310
    assert turn.usage['candidates_token_count'] == 42
    assert turn.timed_out is False


def test_一往復の流れ_最初の音は_LLM_の完了より前に来る():
    """**文単位のストリーミングであることの確認。** 生成完了を待っていたら成立しない。"""
    turn, _ = _turn(
        [FakeChunk('一文目です。'), FakeChunk('二文目です。')],
        clock_values=[100.1, 100.2, 100.3, 100.4, 100.5, 100.9],
    )
    assert turn.first_audio_ms < turn.llm_done_ms


def test_一往復の流れ_句点で終わらない返答も全部送る():
    turn, tts = _turn(
        [FakeChunk('言い切らずに終わる')],
        clock_values=[100.1, 100.2, 100.3, 100.4],
    )
    assert tts.sent == ['言い切らずに終わる']
    assert turn.sentences == ['言い切らずに終わる']


def test_一往復の流れ_返答は連結して残す():
    turn, _ = _turn(
        [FakeChunk('前半、'), FakeChunk('後半です。')],
        clock_values=[100.1, 100.2, 100.3, 100.4],
    )
    assert turn.reply == '前半、後半です。'


def test_一往復の流れ_送った文字数から費用が出る():
    turn, tts = _turn(
        [FakeChunk('あいうえお。')],  # 6 字 + 区切りの空白 1 字
        clock_values=[100.1, 100.2, 100.3, 100.4],
    )
    assert tts.sent_chars == 7
    assert turn.sent_chars == 7
    assert turn.credits == custom_path.estimate_credits(7)


def test_一往復の流れ_音が一つも来なければタイムアウト扱い():
    clock = FakeClock([100.1, 100.2, 100.3, 100.4])
    sink = AudioSink(clock=clock)
    tts = FakeTts(sink, clock, audio_per_sentence=b'')

    class Silent(FakeTts):
        def send(self, sentence):  # 音を流さない
            if self.first_send_at is None:
                self.first_send_at = self._clock()
            self.sent.append(sentence)
            self.sent_sentences += 1
            self.sent_chars += len(sentence) + 1

    silent = Silent(sink, clock)
    turn = custom_path.run_turn(
        '来週の天気はどう',
        hits=[],
        llm_chunks=[FakeChunk('ノートには見当たりません。')],
        tts=silent,
        sink=sink,
        t0=100.0,
        search_done_at=100.01,
        clock=clock,
    )
    assert turn.first_audio_ms == 0
    assert turn.timed_out is True
    assert tts.sent == []


def test_一往復の流れ_WAV_のバイト数が届いた音と合う(tmp_path):
    clock = FakeClock([100.1, 100.2, 100.3, 100.4, 100.5, 100.9])
    sink = AudioSink(clock=clock)
    tts = FakeTts(sink, clock, audio_per_sentence=b'\x00\x01' * 40)
    turn = custom_path.run_turn(
        'ロールバックはどうやるの',
        hits=[{'note': 'デプロイ手順', 'heading': '戻し方'}],
        llm_chunks=[FakeChunk('一文目。'), FakeChunk('二文目。')],
        tts=tts,
        sink=sink,
        t0=100.0,
        search_done_at=100.01,
        clock=clock,
    )
    assert len(turn.pcm) == 160  # 80 バイト × 2 文

    path = custom_path.save_audio_file(
        turn.pcm, 'rollback', '20260911T000000Z', tmp_path, label='custom'
    )
    assert path.name == 'rollback_custom_20260911T000000Z.wav'
    with wave.open(str(path), 'rb') as handle:
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2
        assert handle.getframerate() == 16000
        assert handle.getnframes() == 80  # A の録音と同じ 16kHz / 16bit / mono


# --------------------------------------------------------------------------- 記録


def _sample_turn() -> Turn:
    return Turn(
        question='デプロイの前に確認することを教えて',
        hits=[{'note': 'デプロイ手順', 'heading': '事前確認'}],
        sentences=['デプロイ手順によると、ステージングの確認が要ります。', '次にバックアップです。'],
        reply='デプロイ手順によると、ステージングの確認が要ります。次にバックアップです。',
        search_ms=2,
        first_token_ms=430,
        llm_done_ms=980,
        tts_first_send_ms=520,
        first_audio_ms=690,
        reply_done_ms=1240,
        sent_chars=48,
        usage={'prompt_token_count': 310, 'candidates_token_count': 42, 'total_token_count': 352},
        pcm=b'\x00' * 200,
    )


def test_note_は検索と期待と初トークンと文数と返答の冒頭を書く():
    note = custom_path.build_note({'id': 'deploy-check', 'expected_note': 'デプロイ手順'}, _sample_turn())
    assert '検索1位=デプロイ手順' in note
    assert '期待=デプロイ手順' in note
    assert 'LLM初トークン430ms' in note
    assert '文2本48字' in note
    assert '返答=デプロイ手順によると' in note
    assert 'タイムアウト' not in note


def test_note_は検索零件とタイムアウトも書く():
    turn = Turn(question='来週の天気はどう', hits=[], timed_out=True)
    note = custom_path.build_note({'id': 'not-in-notes', 'expected_note': None}, turn)
    assert '検索1位=None' in note
    assert '期待=None' in note
    assert '音声が来ずタイムアウト' in note


def test_書き起こしに内訳と_Gemini_のトークンが入る():
    text = custom_path.render_transcript(
        {'id': 'deploy-check', 'expected_note': 'デプロイ手順'},
        _sample_turn(),
        voice_id='v1',
        model_id='eleven_flash_v2_5',
        llm='gemini-3.6-flash',
    )
    assert '検索: 2 ms' in text
    assert 'LLM 最初のトークン: 430 ms' in text
    assert 'LLM 完了: 980 ms' in text
    assert '最初の音まで: 690 ms' in text
    assert '2 本 / 48 文字' in text
    assert '入力 310' in text and '出力 42' in text
    assert 'ElevenLabs のクレジットではない' in text
    assert 'gemini-3.6-flash' in text


def test_dry_run_は接続せず検索と_prompt_の先頭を見せる():
    text = custom_path.describe_dry_run(
        {
            'id': 'deploy-check',
            'text': 'デプロイの前に確認することを教えて',
            'expected_note': 'デプロイ手順',
        },
        env={'ELEVENLABS_VOICE_ID': 'v1', 'ELEVENLABS_MODEL_ID': 'm1', 'GEMINI_API_KEY': 'x'},
    )
    assert 'デプロイ手順' in text
    assert 'gemini-3.6-flash' in text
    assert 'system prompt' in text
    assert 'pcm_16000' in text


def test_dry_run_は鍵が無くても落ちない():
    text = custom_path.describe_dry_run(
        {'id': 'not-in-notes', 'text': '来週の天気はどう', 'expected_note': None}, env={}
    )
    assert '未設定' in text
    assert '0 件' in text


# --------------------------------------------------------------------------- WebSocket


class FakeSocket:
    """``stream-input`` のふり。送った JSON を覚え、決めておいた返事を吐く。"""

    def __init__(self, replies):
        self.sent: list[dict] = []
        self._replies = list(replies)
        self.closed = False

    def send(self, raw):
        self.sent.append(json.loads(raw))

    def recv(self):
        if not self._replies:
            raise custom_path.ConnectionClosed(None, None)
        return json.dumps(self._replies.pop(0))

    def close(self):
        self.closed = True


def test_websocket_tts_は文ごとに_flush_付きで送り_音を溜める():
    import base64

    audio = base64.b64encode(b'\x11\x22').decode()
    socket = FakeSocket([{'audio': audio, 'isFinal': False}, {'audio': None, 'isFinal': True}])
    sink = AudioSink(clock=FakeClock([1.0]))
    tts = custom_path.WebSocketTts(
        api_key='k',
        voice_id='v1',
        model_id='eleven_flash_v2_5',
        sink=sink,
        connect_fn=lambda url, **kwargs: socket,
        clock=FakeClock([5.0]),
    )
    with tts:
        tts.send('一文目。')
        tts.finish()
        assert tts.wait(2) is True

    assert socket.sent[0]['text'] == ' '  # 初期メッセージ
    assert socket.sent[1] == {'text': '一文目。 ', 'flush': True}
    assert socket.sent[2] == {'text': ''}
    assert sink.pcm() == b'\x11\x22'
    assert tts.sent_sentences == 1
    assert tts.sent_chars == 5
    assert tts.error is None
    assert socket.closed is True


def test_websocket_tts_の_URL_に声とモデルと出力形式が入る():
    tts = custom_path.WebSocketTts(
        api_key='k', voice_id='v1', model_id='eleven_flash_v2_5', sink=AudioSink()
    )
    assert tts.url.startswith('wss://api.elevenlabs.io/v1/text-to-speech/v1/stream-input?')
    assert 'model_id=eleven_flash_v2_5' in tts.url
    assert 'output_format=pcm_16000' in tts.url


def test_run_scenario_は_A_と同じ_Run_を返して_custom_の名前で残す(monkeypatch):
    """LLM と TTS を差し替えて、``Run`` の組み立てと保存先の名前だけを見る。

    ネットワークにはつながない。``run custom`` が CSV に積むのはこの ``Run``。
    """
    saved: dict[str, str] = {}
    clock = FakeClock([100.1, 100.2, 100.3, 100.4])

    class StubTts(FakeTts):
        def __init__(self, **kwargs):
            super().__init__(kwargs['sink'], clock)

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return None

    monkeypatch.setattr(custom_path, 'WebSocketTts', StubTts)
    monkeypatch.setattr(
        custom_path,
        'stream_gemini',
        lambda *a, **k: [FakeChunk('デプロイ手順によると、確認が要ります。')],
    )
    monkeypatch.setattr(custom_path.time, 'perf_counter', lambda: 100.0)
    monkeypatch.setattr(
        custom_path, 'save_audio_file', lambda *a, **k: saved.setdefault('audio', k['label'])
    )
    monkeypatch.setattr(
        custom_path, 'save_transcript', lambda *a, **k: saved.setdefault('text', k['label'])
    )

    run = custom_path.run_scenario(
        {'id': 'deploy-check', 'text': 'デプロイの前に確認することを教えて', 'expected_note': 'デプロイ手順'},
        env={
            'ELEVENLABS_API_KEY': 'k',
            'GEMINI_API_KEY': 'g',
            'ELEVENLABS_VOICE_ID': 'v1',
            'ELEVENLABS_MODEL_ID': 'm1',
        },
    )

    assert run.path == 'custom'  # metrics.PATH_CUSTOM。A は 'agents'
    assert run.scenario_id == 'deploy-check'
    assert run.correct is None  # 正誤は人が音を聞いて入れる
    assert run.credits == custom_path.estimate_credits(len('デプロイ手順によると、確認が要ります。') + 1)
    assert '検索1位=デプロイ手順' in run.note
    assert saved == {'audio': 'custom', 'text': 'custom'}


def test_run_scenario_は鍵が無ければ接続前に止まる():
    from voicelab.config import ConfigError

    with pytest.raises(ConfigError, match='GEMINI_API_KEY'):
        custom_path.run_scenario(
            {'id': 'deploy-check', 'text': 'x', 'expected_note': None},
            env={'ELEVENLABS_API_KEY': 'k', 'ELEVENLABS_VOICE_ID': 'v', 'ELEVENLABS_MODEL_ID': 'm'},
        )


def test_websocket_tts_つながらなければ止まる():
    def boom(url, **kwargs):
        raise OSError('接続できません')

    tts = custom_path.WebSocketTts(
        api_key='k', voice_id='v1', model_id='m', sink=AudioSink(), connect_fn=boom
    )
    with pytest.raises(custom_path.CustomPathError):
        tts.open()
