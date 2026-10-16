"""命令行：python -m rpds run 输入.json --output reports/结果。"""
import argparse
import csv
import json
from pathlib import Path
from .model import from_dict
from .scheduler import Scheduler
from .metrics import calculate
from .validation import validate_schedule


def write_outputs(folder, result):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    for name in ('copies', 'tasks'):
        rows = result[name]
        with (folder / f'{name}.csv').open('w', newline='', encoding='utf-8-sig') as f:
            if rows:
                writer = csv.DictWriter(f, fieldnames=list(rows[0]))
                writer.writeheader(); writer.writerows(rows)


def main(argv=None):
    parser = argparse.ArgumentParser(description='论文修正版 RPDS；不执行原始实验脚本')
    sub = parser.add_subparsers(dest='command', required=True)
    run = sub.add_parser('run', help='运行显式 JSON 场景')
    run.add_argument('input', type=Path)
    run.add_argument('--output', type=Path, default=Path('reports/run'))
    audit = sub.add_parser('audit-table', help='核查论文 Table IV 并重新生成小案例')
    audit.add_argument('--output', type=Path, default=Path('reports/table_iv'))
    args = parser.parse_args(argv)
    if args.command == 'audit-table':
        from .audit import audit_table
        audit_table(args.output)
        return 0
    data = json.loads(args.input.read_text(encoding='utf-8'))
    scenario = from_dict(data)
    result = Scheduler(scenario).run()
    result['validation_issues'] = validate_schedule(scenario, result)
    result['metrics'] = calculate(scenario, result)
    write_outputs(args.output, result)
    print(json.dumps(result['metrics'], ensure_ascii=False, indent=2))
    if result['validation_issues']:
        raise RuntimeError(result['validation_issues'])
    return 2 if result['infeasible'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
