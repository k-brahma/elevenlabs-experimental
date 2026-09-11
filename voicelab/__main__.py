"""`python -m voicelab credits` の入口。

``-m`` で実行されたときだけ走らせたいので、``__main__`` ガードを置いている
（このモジュール自体を import しても何も起きないようにするため）。
"""

from .cli import main

if __name__ == '__main__':
    raise SystemExit(main())
