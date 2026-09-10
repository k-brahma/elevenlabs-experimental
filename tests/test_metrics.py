"""記録の読み書きと要約。

CSV を往復しても値が壊れないことが要点。あとから正誤（correct）を手で入れるので、
空欄・True・False の 3 状態が保たれることも確かめる。
"""

import pytest

from voicelab import metrics


def _run(**overrides) -> metrics.Run:
    values = dict(
        scenario_id='deploy-check',
        path=metrics.PATH_AGENTS,
        first_audio_ms=800,
        reply_done_ms=4000,
        credits=700,
    )
    values.update(overrides)
    return metrics.Run(**values)


def test_append_と_load_で往復する(tmp_path):
    csv_path = tmp_path / 'runs.csv'
    metrics.append_run(_run(note='ツール呼んだ'), csv_path)
    metrics.append_run(_run(scenario_id='rollback', correct=True), csv_path)

    runs = metrics.load_runs(csv_path)
    assert [r.scenario_id for r in runs] == ['deploy-check', 'rollback']
    assert runs[0].note == 'ツール呼んだ'
    assert runs[0].correct is None
    assert runs[1].correct is True
    assert runs[0].first_audio_ms == 800


def test_load_runs_ファイルが無ければ空(tmp_path):
    assert metrics.load_runs(tmp_path / 'ない.csv') == []


def test_append_run_は知らない構成を弾く(tmp_path):
    with pytest.raises(ValueError):
        metrics.append_run(_run(path='そんな構成は無い'), tmp_path / 'runs.csv')


def test_summarize_は中央値を出す():
    runs = [
        _run(first_audio_ms=100, reply_done_ms=1000, credits=10),
        _run(first_audio_ms=300, reply_done_ms=3000, credits=20),
        _run(first_audio_ms=200, reply_done_ms=2000, credits=30),
    ]
    summary = metrics.summarize(runs)[metrics.PATH_AGENTS]
    assert summary['runs'] == 3
    assert summary['first_audio_ms_median'] == 200
    assert summary['reply_done_ms_median'] == 2000
    assert summary['credits_total'] == 60
    assert summary['credits_mean'] == 20
    assert summary['correct_rate'] == '未判定'


def test_summarize_正答は判定済みだけを数える():
    runs = [_run(correct=True), _run(correct=False), _run(correct=None)]
    assert metrics.summarize(runs)[metrics.PATH_AGENTS]['correct_rate'] == '1/2'


def test_summarize_記録の無い構成は出さない():
    assert metrics.PATH_CUSTOM not in metrics.summarize([_run()])


def test_render_report_記録が無ければそう書く():
    assert 'まだ記録がありません' in metrics.render_report([])


def test_render_report_に要約と全記録が並ぶ():
    text = metrics.render_report([_run(note='ツール呼んだ')])
    assert 'A: Agents Platform' in text
    assert 'deploy-check' in text
    assert 'ツール呼んだ' in text
