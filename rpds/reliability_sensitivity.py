"""修改版RPDS与四个基线在不同DAG可靠性要求下的统一实验。"""
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
from .ablation_suite import evaluate
from .baselines import BASELINES, run_baseline_closed_loop
from .main_experiment import METHODS, LABELS, COLORS
from .metrics import calculate
from .paper_workloads import workload
from .validation import validate_schedule


REQUIREMENTS = (0.992, 0.994, 0.996, 0.998)


def write_csv(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def snapshot(output, status, runs):
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
                "methods": METHODS, "families": ["random", "ge", "fft"], "vehicles": 8,
                "requirements": REQUIREMENTS, "seeds": list(range(20, 40)),
                "rounds_per_source": 3, "completed_runs": runs,
                "source_sha256": hashes, "historical_reproduction": False}
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def validate_baseline(data, method):
    start = time.perf_counter()
    scenario, result, arrivals = run_baseline_closed_loop(data, method)
    elapsed = time.perf_counter() - start
    _, allocation = BASELINES[method]
    issues = validate_schedule(scenario, result, allocation=allocation,
                               allow_same_vehicle_replicas=method.startswith("local"))
    metrics = calculate(scenario, result)
    violations = sum(dag["reliability"] + 1e-10 < dag["requirement"]
                     for dag in metrics["dags"] if dag["status"] == "scheduled")
    result.update(metrics=metrics, arrivals=arrivals, scheduler_seconds=elapsed,
                  validation_issues=issues, reliability_violations=violations,
                  status="invalid" if issues or violations else
                         ("infeasible" if result["infeasible"] else "complete"))
    return result


def row(case, family, requirement, seed, method, result, task_count, input_hash):
    metrics = result["metrics"]
    scheduled = [dag for dag in metrics["dags"] if dag["status"] == "scheduled"]
    return {"case": case, "family": family, "requirement": requirement, "seed": seed,
            "method": method, "input_sha256": input_hash, "status": result["status"],
            "eq14_response_s": metrics["eq14_all_vehicles"],
            "backups_per_task": metrics["backups"] / task_count,
            "average_reliability": statistics.mean(dag["reliability"] for dag in scheduled),
            "validation_issues": len(result["validation_issues"]),
            "reliability_violations": result["reliability_violations"],
            "infeasible_dags": metrics["infeasible_dags"]}


def summarize(rows):
    output = []
    for family in ("random", "ge", "fft"):
        for requirement in REQUIREMENTS:
            for method in METHODS:
                group = [item for item in rows if item["family"] == family
                         and item["requirement"] == requirement and item["method"] == method]
                complete = [item for item in group if item["status"] == "complete"]
                entry = {"family": family, "requirement": requirement, "method": method,
                         "runs": len(group), "complete_runs": len(complete)}
                for field in ("eq14_response_s", "backups_per_task", "average_reliability"):
                    values = [item[field] for item in complete]
                    entry[field] = statistics.mean(values) if len(complete) == len(group) else None
                    entry[field + "_sd"] = statistics.stdev(values) if len(values) > 1 else None
                output.append(entry)
    return output


def plot(output, summary):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 3, figsize=(12, 3.45))
    for ax, family in zip(axes, ("random", "ge", "fft")):
        for method in METHODS:
            group = [item for item in summary if item["family"] == family and item["method"] == method]
            ax.errorbar([100 * item["requirement"] for item in group],
                        [item["eq14_response_s"] for item in group],
                        yerr=[item["eq14_response_s_sd"] for item in group],
                        label=LABELS[method], color=COLORS[method], marker="o",
                        markersize=3, linewidth=1.2, capsize=2, elinewidth=0.6)
        ax.set_title(family.upper())
        ax.set_xlabel("DAG reliability requirement (%)")
        ax.set_ylabel("Eq. (14) response time (s)")
        ax.grid(alpha=0.2)
    axes[-1].legend(fontsize=6.5)
    fig.suptitle("Reliability sensitivity: 8 vehicles, three closed-loop DAGs per source")
    fig.tight_layout()
    fig.savefig(output / "reliability_sensitivity.pdf")
    fig.savefig(output / "reliability_sensitivity.png", dpi=220)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("输出目录已存在，请选择新目录")
    args.output.mkdir(parents=True)
    snapshot(args.output, "running", 0)
    rows = []
    total_cases = len(REQUIREMENTS) * 3 * 20
    completed_cases = 0
    for family in ("random", "ge", "fft"):
        for requirement in REQUIREMENTS:
            for seed in range(20, 40):
                case = f"{family}_r{requirement:.3f}_seed{seed}"
                data = workload(seed, 8, family, 3, requirement=requirement)
                case_dir = args.output / "cases" / case
                case_dir.mkdir(parents=True)
                input_path = case_dir / "input.json"
                input_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                input_hash = digest(input_path)
                task_count = sum(len(dag["tasks"]) for dag in data["dags"])
                for method in METHODS:
                    result = evaluate(data, "path_slack") if method == "rpds" else validate_baseline(data, method)
                    result["input_sha256"] = input_hash
                    with gzip.open(case_dir / f"{method}.json.gz", "wt", encoding="utf-8", compresslevel=1) as stream:
                        json.dump(result, stream, ensure_ascii=False, allow_nan=False)
                    rows.append(row(case, family, requirement, seed, method, result, task_count, input_hash))
                completed_cases += 1
                if completed_cases % 10 == 0:
                    write_csv(args.output / "runs.csv", rows)
                    snapshot(args.output, "running", len(rows))
                    print(f"可靠性敏感性 {completed_cases}/{total_cases} 场景；{len(rows)}/{total_cases*5} 运行", flush=True)
    summary = summarize(rows)
    write_csv(args.output / "runs.csv", rows)
    write_csv(args.output / "summary.csv", summary)
    plot(args.output, summary)
    invalid = sum(item["status"] != "complete" for item in rows)
    issues = sum(item["validation_issues"] + item["reliability_violations"] for item in rows)
    lines = ["# 可靠性要求敏感性实验", "",
             "固定8辆车、每源3个闭环DAG，分别评估Random、GE和FFT；可靠性目标为99.2%、99.4%、99.6%和99.8%。",
             "所有五种方法在相同输入上重新运行，响应时间不包含策略计算耗时。", "",
             f"方法运行总数：{len(rows)}；非完整运行：{invalid}；约束或可靠性问题：{issues}。",
             "这是新生成的固定种子合成实验，不是历史数据复现。"]
    (args.output / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    snapshot(args.output, "complete", len(rows))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
