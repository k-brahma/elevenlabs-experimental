"""計測結果の記録と、表の出力。

1 往復（質問 1 つ → 回答 1 つ）を 1 行として CSV に積む。構成（A / B）ごとに
中央値を出して Markdown の表にする。数字の意味は README の「比べるのは 3 つ」。
"""

import csv
import statistics
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path

from .config import RESULTS_DIR

RUNS_CSV = RESULTS_DIR / 'runs.csv'
REPORT_MD = RESULTS_DIR / 'report.md'

#: 比べる 2 構成。
PATH_AGENTS = 'agents'  # A: Agents Platform
PATH_CUSTOM = 'custom'  # B: Scribe + 検索 + ストリーミング TTS
PATHS = (PATH_AGENTS, PATH_CUSTOM)


@dataclass
class Run:
    """1 往復の記録。

    :ivar scenario_id: ``scenarios/questions.json`` の id。
    :ivar path: ``agents`` か ``custom``。
    :ivar first_audio_ms: 話し終わってから最初の音が出るまで。体感の遅延はこれ。
    :ivar reply_done_ms: 話し終わってから回答が言い終わるまで。
    :ivar credits: この往復で消費したクレジット（会話の ``cost``、無ければ残高の差）。
    :ivar correct: 期待したノートを根拠に答えたか（人が判定して入れる）。
    :ivar note: 気づいたこと。割り込まれた、ツールを呼ばなかった、など。
    """

    scenario_id: str
    path: str
    first_audio_ms: int
    reply_done_ms: int
    credits: int
    correct: bool | None = None
    note: str = ''
    taken_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def append_run(run: Run, path: Path = RUNS_CSV) -> None:
    """1 行追記する。ファイルが無ければ見出し付きで作る。"""
    if run.path not in PATHS:
        raise ValueError(f'path は {PATHS} のどれか: {run.path!r}')
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists()
    with path.open('a', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=[f.name for f in fields(Run)])
        if new_file:
            writer.writeheader()
        writer.writerow(asdict(run))


def load_runs(path: Path = RUNS_CSV) -> list[Run]:
    """CSV を読んで :class:`Run` の一覧にする（無ければ空）。"""
    if not path.exists():
        return []
    runs: list[Run] = []
    with path.open(encoding='utf-8', newline='') as handle:
        for row in csv.DictReader(handle):
            runs.append(
                Run(
                    scenario_id=row['scenario_id'],
                    path=row['path'],
                    first_audio_ms=int(row['first_audio_ms']),
                    reply_done_ms=int(row['reply_done_ms']),
                    credits=int(row['credits']),
                    correct=_parse_bool(row.get('correct', '')),
                    note=row.get('note', ''),
                    taken_at=row.get('taken_at', ''),
                )
            )
    return runs


def _parse_bool(value: str) -> bool | None:
    if value in ('True', 'true', '1'):
        return True
    if value in ('False', 'false', '0'):
        return False
    return None


def summarize(runs: list[Run]) -> dict[str, dict[str, float | int | str]]:
    """構成ごとの要約（件数・遅延の中央値・費用の合計と平均・正答率）。"""
    summary: dict[str, dict[str, float | int | str]] = {}
    for path in PATHS:
        rows = [r for r in runs if r.path == path]
        if not rows:
            continue
        judged = [r for r in rows if r.correct is not None]
        summary[path] = {
            'runs': len(rows),
            'first_audio_ms_median': statistics.median(r.first_audio_ms for r in rows),
            'reply_done_ms_median': statistics.median(r.reply_done_ms for r in rows),
            'credits_total': sum(r.credits for r in rows),
            'credits_mean': round(statistics.mean(r.credits for r in rows)),
            'correct_rate': (
                f'{sum(1 for r in judged if r.correct)}/{len(judged)}' if judged else '未判定'
            ),
        }
    return summary


def render_report(runs: list[Run]) -> str:
    """要約と全行を Markdown にする。"""
    lines = ['# 計測結果', '']
    summary = summarize(runs)
    if not summary:
        lines.append('まだ記録がありません。')
        return '\n'.join(lines) + '\n'

    lines += [
        '| 構成 | 往復数 | 最初の音まで（中央値 ms） | 言い終わるまで（中央値 ms） '
        '| クレジット合計 | 1 往復あたり | 正答 |',
        '|---|---:|---:|---:|---:|---:|---|',
    ]
    labels = {PATH_AGENTS: 'A: Agents Platform', PATH_CUSTOM: 'B: 自前構成'}
    for path, row in summary.items():
        lines.append(
            f'| {labels[path]} | {row["runs"]} | {row["first_audio_ms_median"]:.0f} '
            f'| {row["reply_done_ms_median"]:.0f} | {row["credits_total"]:,} '
            f'| {row["credits_mean"]:,} | {row["correct_rate"]} |'
        )

    lines += ['', '## 全記録', '', '| 日時 (UTC) | 質問 | 構成 | 最初の音 ms | 言い終わり ms | クレジット | 正答 | メモ |',
              '|---|---|---|---:|---:|---:|---|---|']
    for r in runs:
        correct = '' if r.correct is None else ('○' if r.correct else '×')
        lines.append(
            f'| {r.taken_at[:16].replace("T", " ")} | {r.scenario_id} | {r.path} '
            f'| {r.first_audio_ms} | {r.reply_done_ms} | {r.credits} | {correct} | {r.note} |'
        )
    return '\n'.join(lines) + '\n'


def write_report(runs: list[Run], path: Path = REPORT_MD) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_report(runs), encoding='utf-8')
    return path


if __name__ == '__main__':
    # python -m voicelab.metrics
    # 記録済みの CSV から表を作って表示する（課金なし）。
    print(render_report(load_runs()))
