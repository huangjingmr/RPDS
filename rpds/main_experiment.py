"""修改版RPDS与四个论文基线的统一闭环主实验。"""
import argparse
import csv
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
import platform
import shutil
import statistics
import time

from .ablation import digest
from .baselines import BASELINES, run_baseline_closed_loop
from .metrics import calculate
from .validation import validate_schedule


METHODS = ("rpds", "local_maxre", "local_rr", "heft_maxre", "heft_rr")
LABELS = {"rpds": "RPDS", "local_maxre": "Local & MaxRe",
          "local_rr": "Local & RR", "heft_maxre": "HEFT & MaxRe",
          "heft_rr": "HEFT & RR"}
COLORS = {"rpds": "#1764a3", "local_maxre": "#777777", "local_rr": "#9864a6",
          "heft_maxre": "#538b3d", "heft_rr": "#d17d17"}
WEIGHTS = {"ge": 0.4, "random": 0.3, "fft": 0.3}


def read_csv(path):
    with path.open(encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def write_csv(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def snapshot(source, output, status, runs):
    target_dir = output / "source" / "rpds"
    target_dir.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for path in Path(__file__).parent.glob("*.py"):
        target = target_dir / path.name
        if not target.exists():
            shutil.copy2(path, target)
        hashes[path.name] = digest(target)
    manifest = {"status": status, "updated_utc": datetime.now(timezone.utc).isoformat(),
                "python": platform.python_version(), "platform": platform.platform(),
                "rpds_source_results": str(source.resolve()),
                "rpds_source_manifest_sha256": digest(source / "manifest.json"),
                "methods": METHODS, "families": ["random", "ge", "fft"],
                "vehicles": [4, 6, 8, 10, 12, 14, 16], "seeds": list(range(20, 40)),
                "rounds_per_source": 3, "requirement": 0.995,
                "aggregate_weights": WEIGHTS, "completed_method_runs": runs,
                "source_sha256": hashes, "historical_reproduction": False}
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def record(case_row, method, metrics, elapsed, issues, violations, raw_source):
    task_count = int(case_row["tasks"])
    scheduled = [dag for dag in metrics["dags"] if dag["status"] == "scheduled"]
    average_reliability = statistics.mean(dag["reliability"] for dag in scheduled) if scheduled else None
    return {"case": case_row["case"], "family": case_row["family"],
            "vehicles": int(case_row["vehicles"]), "seed": int(case_row["seed"]),
            "method": method, "input_sha256": case_row["input_sha256"],
            "status": "invalid" if issues or violations else
                      ("infeasible" if metrics["infeasible_dags"] else "complete"),
            "eq14_response_s": metrics["eq14_all_vehicles"],
            "backups_per_task": metrics["backups"] / task_count,
            "average_reliability": average_reliability,
            "backups": metrics["backups"], "tasks": task_count,
            "dags": metrics["total_dags"], "scheduler_seconds": elapsed,
            "validation_issues": len(issues), "reliability_violations": violations,
            "infeasible_dags": metrics["infeasible_dags"], "raw_source": raw_source}


def run_baseline(data, method):
    start = time.perf_counter()
    scenario, result, arrivals = run_baseline_closed_loop(data, method)
    elapsed = time.perf_counter() - start
    _, allocation = BASELINES[method]
    issues = validate_schedule(scenario, result, allocation=allocation,
                               allow_same_vehicle_replicas=method.startswith("local"))
    metrics = calculate(scenario, result)
    violations = sum(dag["reliability"] + 1e-10 < dag["requirement"]
                     for dag in metrics["dags"] if dag["status"] == "scheduled")
    result.update(metrics=metrics, arrivals=arrivals, validation_issues=issues,
                  reliability_violations=violations,
                  status="invalid" if issues or violations else
                         ("infeasible" if result["infeasible"] else "complete"),
                  scheduler_seconds=elapsed)
    return result


def summarize(rows):
    output = []
    for family in ("random", "ge", "fft"):
        for vehicles in (4, 6, 8, 10, 12, 14, 16):
            for method in METHODS:
                group = [row for row in rows if row["family"] == family
                         and row["vehicles"] == vehicles and row["method"] == method]
                complete = [row for row in group if row["status"] == "complete"]
                item = {"family": family, "vehicles": vehicles, "method": method,
                        "runs": len(group), "complete_runs": len(complete)}
                for field in ("eq14_response_s", "backups_per_task", "average_reliability"):
                    values = [float(row[field]) for row in complete]
                    item[field] = statistics.mean(values) if len(complete) == len(group) else None
                    item[field + "_sd"] = statistics.stdev(values) if len(values) > 1 else None
                output.append(item)
    return output


def paired(rows):
    indexed = {(row["case"], row["method"]): row for row in rows}
    result = []
    cases = sorted({row["case"] for row in rows})
    for case in cases:
        rpds = indexed[case, "rpds"]
        for method in METHODS[1:]:
            baseline = indexed[case, method]
            ok = rpds["status"] == baseline["status"] == "complete"
            reduction = None
            if ok:
                reduction = 100 * (float(baseline["eq14_response_s"])
                                   - float(rpds["eq14_response_s"])) / float(baseline["eq14_response_s"])
            result.append({"case": case, "family": rpds["family"],
                           "vehicles": rpds["vehicles"], "seed": rpds["seed"],
                           "baseline": method, "both_complete": ok,
                           "rpds_response_reduction_percent": reduction})
    return result


def weighted_pairs(pairs):
    indexed = {(row["family"], int(row["vehicles"]), int(row["seed"]), row["baseline"]): row
               for row in pairs}
    output = []
    for vehicles in (4, 6, 8, 10, 12, 14, 16):
        for seed in range(20, 40):
            for baseline in METHODS[1:]:
                group = [indexed[family, vehicles, seed, baseline] for family in WEIGHTS]
                if not all(row["both_complete"] in (True, "True") for row in group):
                    value = None
                else:
                    value = sum(WEIGHTS[family] * float(indexed[family, vehicles, seed, baseline]
                                ["rpds_response_reduction_percent"]) for family in WEIGHTS)
                output.append({"vehicles": vehicles, "seed": seed, "baseline": baseline,
                               "rpds_response_reduction_percent": value})
    return output


def plot(output, summary):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 3, figsize=(12, 3.45))
    for ax, family in zip(axes, ("random", "ge", "fft")):
        for method in METHODS:
            group = [row for row in summary if row["family"] == family and row["method"] == method]
            ax.errorbar([row["vehicles"] for row in group],
                        [row["eq14_response_s"] for row in group],
                        yerr=[row["eq14_response_s_sd"] for row in group],
                        label=LABELS[method], color=COLORS[method], marker="o",
                        markersize=3, linewidth=1.2, capsize=2, elinewidth=0.6)
        ax.set_title(family.upper())
        ax.set_xlabel("Vehicles")
        ax.set_ylabel("Eq. (14) response time (s)")
        ax.grid(alpha=0.2)
    axes[-1].legend(fontsize=6.5)
    fig.suptitle("Overall comparison: three closed-loop DAGs per source, $R_{req}=99.5\\%$")
    fig.tight_layout()
    fig.savefig(output / "overall_response.pdf")
    fig.savefig(output / "overall_response.png", dpi=220)
    plt.close(fig)


def report(output, rows, pairs, weighted):
    lines = ["# 修改版RPDS主实验", "",
             "Random、GE和FFT分别报告；40% GE、30% Random、30% FFT只用于额外的相对改善汇总。",
             "RPDS复用已冻结的新任务选择验证运行；四个基线在完全相同输入上重新运行。所有方法均使用修正后的传输时延、每跳epsilon、FIFO和最晚副本完成规则。",
             "Local重复副本在源车辆串行执行，并按独立瞬态故障事件计算可靠性。HEFT按upward rank规划单个DAG，不跨已到达DAG重新排序。", "",
             "|比较基线|40/30/30平均响应降幅(%)|改善/相同/变差|", "|---|---:|---:|"]
    for method in METHODS[1:]:
        values = [row["rpds_response_reduction_percent"] for row in weighted
                  if row["baseline"] == method and row["rpds_response_reduction_percent"] is not None]
        lines.append(f"|{LABELS[method]}|{statistics.mean(values):.4f}|"
                     f"{sum(v > 1e-10 for v in values)}/{sum(abs(v) <= 1e-10 for v in values)}/{sum(v < -1e-10 for v in values)}|")
    invalid = sum(row["status"] != "complete" for row in rows)
    issues = sum(int(row["validation_issues"]) + int(row["reliability_violations"]) for row in rows)
    lines += ["", f"方法运行总数：{len(rows)}；非完整运行：{invalid}；约束或可靠性问题：{issues}。",
              "这是按当前代码和原论文结构重新实现的新实验，不是历史21,000个DAG数据的复现。"]
    (output / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("输出目录已存在，请选择新目录")
    args.output.mkdir(parents=True)
    snapshot(args.source, args.output, "running", 0)

    source_rows = [row for row in read_csv(args.source / "runs.csv") if row["variant"] == "path_slack"]
    if len(source_rows) != 420:
        raise ValueError("预期420个冻结RPDS运行")
    rows = []
    for index, source_row in enumerate(source_rows, 1):
        case = source_row["case"]
        case_source = args.source / "cases" / case
        input_path = case_source / "input.json"
        if digest(input_path) != source_row["input_sha256"]:
            raise ValueError(f"输入哈希不匹配: {case}")
        data = json.loads(input_path.read_text(encoding="utf-8"))
        with gzip.open(case_source / "path_slack.json.gz", "rt", encoding="utf-8") as stream:
            rpds = json.load(stream)
        if rpds["status"] != "complete" or rpds["validation_issues"] or rpds["reliability_violations"]:
            raise ValueError(f"冻结RPDS未通过验证: {case}")
        rows.append(record(source_row, "rpds", rpds["metrics"], rpds["scheduler_seconds"],
                           rpds["validation_issues"], rpds["reliability_violations"],
                           str((case_source / "path_slack.json.gz").resolve())))
        raw_dir = args.output / "cases" / case
        raw_dir.mkdir(parents=True)
        for method in METHODS[1:]:
            result = run_baseline(data, method)
            result["input_sha256"] = source_row["input_sha256"]
            raw_path = raw_dir / f"{method}.json.gz"
            with gzip.open(raw_path, "wt", encoding="utf-8", compresslevel=1) as stream:
                json.dump(result, stream, ensure_ascii=False, allow_nan=False)
            rows.append(record(source_row, method, result["metrics"], result["scheduler_seconds"],
                               result["validation_issues"], result["reliability_violations"],
                               str(raw_path.resolve())))
        if index % 10 == 0:
            write_csv(args.output / "runs.csv", rows)
            snapshot(args.source, args.output, "running", len(rows))
            print(f"主实验场景 {index}/420；方法运行 {len(rows)}/2100", flush=True)

    summary = summarize(rows)
    pair_rows = paired(rows)
    weighted = weighted_pairs(pair_rows)
    write_csv(args.output / "runs.csv", rows)
    write_csv(args.output / "summary.csv", summary)
    write_csv(args.output / "paired.csv", pair_rows)
    write_csv(args.output / "weighted_pairs.csv", weighted)
    plot(args.output, summary)
    report(args.output, rows, pair_rows, weighted)
    snapshot(args.source, args.output, "complete", len(rows))


if __name__ == "__main__":
    main()
