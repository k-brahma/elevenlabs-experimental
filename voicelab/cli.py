"""コマンドラインの入口。

- ``credits`` … 今の残クレジットを表示（課金されない）
- ``scenarios`` … 質問集を表示
- ``report`` … 記録済みの結果から表を作る
- ``setup-agent`` … A の Agent とツールを作る／更新する（課金されない）
- ``run agents`` … A で質問集を流して計測する（**クレジットを消費する**）
- ``run custom`` … B で質問集を流して計測する（**クレジットと Gemini のトークンを消費する**）

**この層の責務は「何回・どの順で流すか」と「流してよいかの判断」**。
1 往復の中身は :mod:`voicelab.agents_path`（A）と :mod:`voicelab.custom_path`（B）にある。
"""

import argparse
import sys

from . import agent_setup, agents_path, credits, custom_path, metrics
from .config import ConfigError, find_scenario, load_scenarios, require

#: これを下回っていたら会話を始めない（クレジット）。
#: 2026-09-10 の実測で約 730 クレジット/分。5 問流すなら 2 分前後は見ておく。
MIN_CREDITS = 1500


def cmd_credits(_args: argparse.Namespace) -> int:
    snapshot = credits.read_subscription(require('ELEVENLABS_API_KEY'))
    print(snapshot.describe())
    return 0


def cmd_scenarios(_args: argparse.Namespace) -> int:
    questions = load_scenarios()
    for item in questions:
        print(f'{item["id"]:<16} {item["text"]}   → {item["expected_note"]}')
    print(f'\n{len(questions)} 問。題材: corpus/（架空のノート 3 本）')
    return 0


def cmd_report(_args: argparse.Namespace) -> int:
    runs = metrics.load_runs()
    path = metrics.write_report(runs)
    print(metrics.render_report(runs))
    print(f'→ {path}')
    return 0


def cmd_setup_agent(_args: argparse.Namespace) -> int:
    print(agent_setup.setup().describe())
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    if args.path == metrics.PATH_AGENTS:
        return _run_agents(args)
    return _run_custom(args)


def _run_agents(args: argparse.Namespace) -> int:
    """A を流す。**残高の見張りと、1 問 1 会話の縛りはここで持つ。**"""
    scenarios = [find_scenario(args.scenario)] if args.scenario else load_scenarios()

    if args.dry_run:
        for scenario in scenarios:
            print(agents_path.describe_dry_run(scenario))
            print()
        print('[dry-run] 接続していません。クレジットは消費していません。')
        return 0

    api_key = require('ELEVENLABS_API_KEY')
    before = credits.read_subscription(api_key)
    print(f'実行前 {before.describe()}')
    if before.remaining < MIN_CREDITS and not args.force:
        print(
            f'残り {before.remaining:,} は {MIN_CREDITS:,} を下回っています。'
            ' 会話を始めません（続けるなら --force）。',
            file=sys.stderr,
        )
        return 1

    for scenario in scenarios:
        # 1 問ごとに新しい会話を開く。前の質問の文脈が残ると、ツールを呼ばずに
        # 前の答えを流用することがあり、1 往復の計測にならないため。
        print(f'--- {scenario["id"]}: {scenario["text"]}')
        run = agents_path.run_scenario(scenario, save_audio=not args.no_audio)
        metrics.append_run(run)
        print(
            f'    最初の音 {run.first_audio_ms} ms / 言い終わり {run.reply_done_ms} ms'
            f' / {run.credits} クレジット'
        )
        print(f'    {run.note}')

    after = credits.read_subscription(api_key)
    print(f'実行後 {after.describe()}')
    print(f'消費: {credits.consumed(before, after):,} クレジット（{len(scenarios)} 往復）')
    print(f'→ {metrics.write_report(metrics.load_runs())}')
    print('正誤（correct 列）は音を聞いて results/runs.csv に手で入れる。音は results/audio/。')
    return 0


def _run_custom(args: argparse.Namespace) -> int:
    """B を流す。**鍵の確認と残高の見張りはここで持つ。**

    残高のしきい値は A と共通（:data:`MIN_CREDITS`）。B の 1 往復は TTS の文字数ぶん
    （100 字なら 50 クレジット前後）しか使わないので、実際にはまず引っかからない。
    それでも同じ門をくぐらせるのは、A と B で「止まる条件」を変えないため。
    """
    scenarios = [find_scenario(args.scenario)] if args.scenario else load_scenarios()

    if args.dry_run:
        for scenario in scenarios:
            print(custom_path.describe_dry_run(scenario))
            print()
        print('[dry-run] 接続していません。クレジットも Gemini のトークンも使っていません。')
        return 0

    # 鍵は残高を読む前に確かめる。足りないまま先に進んでも、途中で止まるだけ。
    api_key = require('ELEVENLABS_API_KEY')
    require('GEMINI_API_KEY')

    before = credits.read_subscription(api_key)
    print(f'実行前 {before.describe()}')
    if before.remaining < MIN_CREDITS and not args.force:
        print(
            f'残り {before.remaining:,} は {MIN_CREDITS:,} を下回っています。'
            ' 実行しません（続けるなら --force）。',
            file=sys.stderr,
        )
        return 1

    for scenario in scenarios:
        print(f'--- {scenario["id"]}: {scenario["text"]}')
        run = custom_path.run_scenario(scenario, save_audio=not args.no_audio)
        metrics.append_run(run)
        print(
            f'    最初の音 {run.first_audio_ms} ms / 言い終わり {run.reply_done_ms} ms'
            f' / {run.credits} クレジット（見積り）'
        )
        print(f'    {run.note}')

    after = credits.read_subscription(api_key)
    print(f'実行後 {after.describe()}')
    print(
        f'残高の差: {credits.consumed(before, after):,} クレジット（{len(scenarios)} 往復）。'
        ' 反映は遅れるので、見積りと合わないことがある'
    )
    print(f'→ {metrics.write_report(metrics.load_runs())}')
    print('正誤（correct 列）は音を聞いて results/runs.csv に手で入れる。音は results/audio/。')
    print('Gemini のトークンは results/transcripts/ に残している（クレジットとは別勘定）。')
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='voicelab', description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)

    sub.add_parser('credits', help='今の残クレジットを表示').set_defaults(func=cmd_credits)
    sub.add_parser('scenarios', help='質問集を表示').set_defaults(func=cmd_scenarios)
    sub.add_parser('report', help='記録から表を作る').set_defaults(func=cmd_report)
    sub.add_parser(
        'setup-agent', help='A の Agent とツールを作る／更新する（課金されない）'
    ).set_defaults(func=cmd_setup_agent)

    run = sub.add_parser('run', help='質問集を流して計測する')
    run.add_argument('path', choices=metrics.PATHS, help='agents = A, custom = B')
    run.add_argument('--scenario', help='質問の id（省略すると全部を順に）')
    run.add_argument(
        '--dry-run', action='store_true', help='接続せず、検索と設定の確認だけ（課金されない）'
    )
    run.add_argument('--no-audio', action='store_true', help='WAV を残さない')
    run.add_argument('--force', action='store_true', help='残クレジットが少なくても実行する')
    run.set_defaults(func=cmd_run)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (
        ConfigError,
        credits.CreditsError,
        agent_setup.SetupError,
        agents_path.AgentsPathError,
        custom_path.CustomPathError,
    ) as exc:
        print(f'エラー: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
