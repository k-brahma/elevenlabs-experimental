"""1 往復の「終了判定」と時刻の計算。

会話は流さない（クレジットを消費するため）。時刻を差し込める形にしてあるので、
:class:`MeasuringAudioInterface` に偽の時計を渡すだけで判定を確かめられる。
"""

import wave

from voicelab import agents_path
from voicelab.agents_path import Capture, MeasuringAudioInterface, ToolCall


class FakeClock:
    """呼ばれるたびに好きな値を返す時計。"""

    def __init__(self, values):
        self.values = list(values)

    def __call__(self) -> float:
        return self.values.pop(0)


def test_elapsed_ms_はミリ秒に丸める():
    assert agents_path.elapsed_ms(10.0, 10.8) == 800
    assert agents_path.elapsed_ms(10.0, 10.8006) == 801


def test_audio_window_ms_は最初と最後を返す():
    audio = MeasuringAudioInterface(clock=FakeClock([100.5, 101.0, 103.25]))
    for _ in range(3):
        audio.output(b'\x00\x00')
    assert agents_path.audio_window_ms(100.0, audio.times) == (500, 3250)


def test_audio_window_ms_音が来ていなければゼロ():
    assert agents_path.audio_window_ms(100.0, []) == (0, 0)


def test_reply_state_音が来る前は待ち():
    assert agents_path.reply_state(100.0, 105.0, []) == 'waiting'


def test_reply_state_音が来ないままなら時間切れ():
    now = 100.0 + agents_path.FIRST_AUDIO_TIMEOUT_SECONDS
    assert agents_path.reply_state(100.0, now, []) == 'timeout'


def test_reply_state_喋っている間は継続():
    assert agents_path.reply_state(100.0, 101.5, [101.0]) == 'speaking'


def test_reply_state_一定時間の無音で言い終わり():
    now = 101.0 + agents_path.SILENCE_END_SECONDS
    assert agents_path.reply_state(100.0, now, [100.5, 101.0]) == 'done'


def test_reply_state_音が来ていれば時間切れにはしない():
    """40 秒を過ぎていても、喋り始めていれば無音判定の側で終わらせる。"""
    assert agents_path.reply_state(100.0, 150.0, [149.9]) == 'speaking'


def test_reply_state_しきい値は差し替えられる():
    assert agents_path.reply_state(0.0, 1.0, [0.5], silence_seconds=0.4) == 'done'


def test_output_は音を捨てずに溜める():
    audio = MeasuringAudioInterface(clock=FakeClock([1.0, 2.0]))
    audio.output(b'\x01\x02')
    audio.output(b'\x03\x04')
    assert audio.pcm() == b'\x01\x02\x03\x04'


def test_interrupt_は印だけ立てて録音を残す():
    audio = MeasuringAudioInterface(clock=FakeClock([1.0]))
    audio.output(b'\x01\x02')
    audio.interrupt()
    assert audio.interrupted is True
    assert audio.pcm() == b'\x01\x02'


def test_start_は無音を送らない既定():
    """マイクを開かず、既定では何も送らない。送るかどうかは実測で決める。"""
    sent = []
    audio = MeasuringAudioInterface()
    audio.start(sent.append)
    audio.stop()
    assert sent == []


def test_tool_handler_は検索結果を返して呼び出しを記録する():
    capture = Capture()
    handler = agents_path.make_tool_handler(capture, clock=FakeClock([12.5]))
    payload = handler({'tool_call_id': 'abc', 'query': 'ロールバックはどうやるの'})

    assert 'デプロイ手順' in payload
    assert len(capture.tool_calls) == 1
    call = capture.tool_calls[0]
    assert call.query == 'ロールバックはどうやるの'
    assert call.top_note == 'デプロイ手順'
    assert call.at == 12.5


def test_tool_handler_見つからなければ零件を記録する():
    capture = Capture()
    handler = agents_path.make_tool_handler(capture, clock=FakeClock([1.0]))
    handler({'tool_call_id': 'abc', 'query': '来週の天気はどう'})
    assert capture.tool_calls[0].hits == 0
    assert capture.tool_calls[0].top_note is None


def test_build_note_はツールと期待と返答を短く残す():
    capture = Capture(
        responses=['デプロイ手順によると、ステージングの確認とバックアップが要ります。'],
        tool_calls=[ToolCall(at=1.0, query='デプロイ', hits=3, top_note='デプロイ手順')],
    )
    note = agents_path.build_note(
        {'id': 'deploy-check', 'expected_note': 'デプロイ手順'},
        capture,
        interrupted=False,
        timed_out=False,
        cost_missing=False,
    )
    assert 'ツール呼んだ(1回)' in note
    assert '1位=デプロイ手順' in note
    assert '期待=デプロイ手順' in note
    assert '割り込み=なし' in note
    assert '費用未取得' not in note


def test_build_note_は呼ばれなかったことも書く():
    note = agents_path.build_note(
        {'id': 'not-in-notes', 'expected_note': None},
        Capture(),
        interrupted=True,
        timed_out=True,
        cost_missing=True,
    )
    assert 'ツール呼ばなかった' in note
    assert '割り込み=あり' in note
    assert '音声が来ずタイムアウト' in note
    assert '費用未取得' in note


def test_save_audio_file_は十六キロヘルツのモノラル(tmp_path):
    path = agents_path.save_audio_file(b'\x00\x01' * 100, 'deploy-check', '20260911T000000Z', tmp_path)
    with wave.open(str(path), 'rb') as handle:
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2
        assert handle.getframerate() == 16000
        assert handle.getnframes() == 100


def test_render_transcript_に質問と返答と時刻が入る():
    text = agents_path.render_transcript(
        {'id': 'rollback', 'text': 'ロールバックはどうやるの', 'expected_note': 'デプロイ手順'},
        Capture(
            responses=['タグを戻して入れ替えます。'],
            transcripts=['ロールバックはどうやるの'],
            tool_calls=[ToolCall(at=1.0, query='ロールバック', hits=2, top_note='デプロイ手順')],
        ),
        first_audio_ms=820,
        reply_done_ms=5100,
        conversation_id='conv_x',
        credits=740,
        duration_secs=9,
        interrupted=False,
    )
    assert 'ロールバックはどうやるの' in text
    assert 'タグを戻して入れ替えます。' in text
    assert '820 ms' in text
    assert 'conv_x' in text


def test_describe_dry_run_は接続せず検索結果を並べる():
    text = agents_path.describe_dry_run(
        {'id': 'deploy-check', 'text': 'デプロイの前に確認することを教えて', 'expected_note': 'デプロイ手順'},
        env={'ELEVENLABS_AGENT_ID': 'agent_test', 'ELEVENLABS_VOICE_ID': 'v', 'ELEVENLABS_MODEL_ID': 'm'},
    )
    assert 'agent_test' in text
    assert 'デプロイ手順' in text


def test_describe_dry_run_は設定が無くても落ちない():
    text = agents_path.describe_dry_run(
        {'id': 'not-in-notes', 'text': '来週の天気はどう', 'expected_note': None}, env={}
    )
    assert 'setup-agent' in text
    assert '0 件' in text
