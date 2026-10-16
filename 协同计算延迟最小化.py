"""修正版统一入口；原始程序已逐字节备份至 original/。

运行：python3 协同计算延迟最小化.py run examples/paper_example.json
测试：python3 -m unittest discover -s tests -v
"""
from rpds.__main__ import main

if __name__ == '__main__':
    raise SystemExit(main())
