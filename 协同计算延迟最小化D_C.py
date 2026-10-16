"""修正版 RPDS 兼容入口。旧 D_C 基线与插队逻辑保存在 original/，不再默认执行。"""
from rpds.__main__ import main

if __name__ == '__main__':
    raise SystemExit(main())
