"""C（Knowledge Base）の 1 往復。

**ネットワークを叩かない。** 会話は流さず、C だけが持つもの（``rag_usage`` の記録、
道具を持たない ``--dry-run``）と、**A の計測をそのまま使い回していること**を確かめる。

計測の道具を写し取ると、直したときに片方だけ直って A と C の数字が比べられなくなる。
だから「同じ関数オブジェクトであること」をテストで固定している。
"""

from voicelab import agents_path, kb_path
from voicelab.agents_path import Capture, ToolCall
from voicelab.metrics import PATH_KB

SCENARIO = {
    'id': 'deploy-check',
    'text': 'デプロイの前に確認することを教えて',
    'expected_note': 'デプロイ手順',
}

SUMMARY = {
    'name': 'voicelab-c',
    'llm': 'gemini-3.6-flash',
    'documents': ['2026-05-12 定例メモ', 'デプロイ手順', '用語集'],
    'tool_ids': [],
    'rag_enabled': True,
    'embedding_model': 'multilingual_e5_large_instruct',
    'max_chunks': 3,
}


# --------------------------------------------------------------------------- 再利用


def test_計測の道具は_A_のものをそのまま使う():
    """同じ関数・同じクラスであること。写し取ると数字が比べられなくなる。"""
    assert kb_path.MeasuringAudioInterface is agents_path.MeasuringAudioInterface
    assert kb_path.audio_window_ms is agents_path.audio_window_ms
    assert kb_path.save_audio_file is agents_path.save_audio_file
    assert kb_path.save_transcript is agents_path.save_transcript
    assert kb_path.render_transcript is agents_path.render_transcript
    assert kb_path.build_note is agents_path.build_note
    assert kb_path.wait_for_connection is agents_path.wait_for_connection
    assert kb_path.wait_for_reply is agents_path.wait_for_reply
    assert kb_path.utc_stamp is agents_path.utc_stamp


def test_kb_path_は計測の関数を自分では定義しない():
    """`kb_path` にあるのは C 固有のものだけ（A から借りたものは A の持ち物のまま）。"""
    borrowed = ('audio_window_ms', 'save_audio_file', 'render_transcript', 'build_note')
    for name in borrowed:
        assert getattr(kb_path, name).__module__ == 'voicelab.agents_path'
    assert kb_path.rag_summary.__module__ == 'voicelab.kb_path'


def test_終了判定も_A_と同じものを通る():
    """`reply_state` を差し替えれば C の待ちも変わる（＝同じ判定で測っている）。"""
    assert agents_path.wait_for_reply.__module__ == 'voicelab.agents_path'
    now = 101.0 + agents_path.SILENCE_END_SECONDS
    assert agents_path.reply_state(100.0, now, [100.5, 101.0]) == 'done'


# --------------------------------------------------------------------------- rag_usage


def test_書き起こしに_rag_usage_が残る():
    text = agents_path.render_transcript(
        SCENARIO,
        Capture(responses=['デプロイ手順によると、ステージングの確認が要ります。']),
        first_audio_ms=1200,
        reply_done_ms=3000,
        conversation_id='conv_kb',
        credits=80,
        duration_secs=6,
        interrupted=False,
        rag_usage={'usage': 'used', 'retrieved_chunks': 3},
    )
    assert '## RAG' in text
    assert 'retrieved_chunks' in text
    assert '1200 ms' in text


def test_A_の書き起こしには_RAG_の節が増えない():
    """既定は今までどおり。A の出力を変えていないこと。"""
    text = agents_path.render_transcript(
        SCENARIO,
        Capture(
            responses=['はい。'],
            tool_calls=[ToolCall(at=1.0, query='デプロイ', hits=3, top_note='デプロイ手順')],
        ),
        first_audio_ms=1,
        reply_done_ms=2,
        conversation_id='conv_a',
        credits=1,
        duration_secs=1,
        interrupted=False,
    )
    assert '## RAG' not in text


def test_rag_summary_は使われたかどうかを一言にする():
    assert kb_path.rag_summary({'usage': 'used'}).startswith('RAG=')
    assert 'usage' in kb_path.rag_summary({'usage': 'used'})
    assert kb_path.rag_summary(None) == 'RAG=記録なし'
    assert kb_path.rag_summary({}) == 'RAG=記録なし'


def test_rag_summary_は長くてもCSVに収まる():
    summary = kb_path.rag_summary({'chunks': ['あ' * 500]})
    assert len(summary) <= 45


def test_build_note_の先頭を_RAG_に差し替えられる():
    note = agents_path.build_note(
        SCENARIO,
        Capture(responses=['デプロイ手順によると、まず確認します。']),
        interrupted=False,
        timed_out=False,
        cost_missing=False,
        lead=f'道具なし {kb_path.rag_summary({"usage": "used"})}',
    )
    assert note.startswith('道具なし RAG=')
    assert 'ツール呼ばなかった' not in note
    assert '期待=デプロイ手順' in note


def test_build_note_の既定は_A_のまま():
    note = agents_path.build_note(
        SCENARIO, Capture(), interrupted=False, timed_out=False, cost_missing=False
    )
    assert note.startswith('ツール呼ばなかった')


# --------------------------------------------------------------------------- 置き場所


def test_書き起こしと音は_kb_の名前で残る(tmp_path):
    transcript = agents_path.save_transcript('本文', 'deploy-check', '20260911T000000Z', tmp_path, label=PATH_KB)
    audio = agents_path.save_audio_file(b'\x00\x01' * 10, 'deploy-check', '20260911T000000Z', tmp_path, label=PATH_KB)
    assert transcript.name == 'deploy-check_kb_20260911T000000Z.txt'
    assert audio.name == 'deploy-check_kb_20260911T000000Z.wav'


# --------------------------------------------------------------------------- dry-run


def test_dry_run_は預けた文書の数と道具の無さを見せる():
    text = kb_path.describe_dry_run(
        SCENARIO,
        env={'ELEVENLABS_KB_AGENT_ID': 'agent_c', 'ELEVENLABS_VOICE_ID': 'v', 'ELEVENLABS_MODEL_ID': 'm'},
        summary=SUMMARY,
    )
    assert 'agent_c' in text
    assert '預けてある文書: 3 本' in text
    assert 'デプロイ手順' in text
    assert '道具: なし' in text
    assert 'RAG: 有効' in text


def test_dry_run_は道具が残っていれば知らせる():
    text = kb_path.describe_dry_run(SCENARIO, env={}, summary={**SUMMARY, 'tool_ids': ['tool_a']})
    assert '1 個あります' in text


def test_dry_run_は英語寄りの埋め込みを警告する():
    text = kb_path.describe_dry_run(
        SCENARIO, env={}, summary={**SUMMARY, 'embedding_model': 'e5_mistral_7b_instruct'}
    )
    assert 'multilingual_e5_large_instruct' in text
    assert '日本語では引けません' in text


def test_dry_run_は文書が無ければ_setup_kb_を促す():
    text = kb_path.describe_dry_run(SCENARIO, env={}, summary={**SUMMARY, 'documents': []})
    assert 'setup-kb' in text


def test_dry_run_は設定が無くても落ちないしネットワークも使わない():
    """鍵も agent id も無い状態で呼ばれる。ここで API を叩くと dry-run の意味が無い。"""
    text = kb_path.describe_dry_run(
        {'id': 'not-in-notes', 'text': '来週の天気はどう', 'expected_note': None}, env={}
    )
    assert 'setup-kb' in text
    assert '手元の corpus: 3 本' in text
