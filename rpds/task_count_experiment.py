"""随机DAG任务数实验：可靠性敏感性、五方法比较和备份/可靠性表。"""
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


TASK_COUNTS = (20, 30, 40, 50, 60, 70, 80)
RPDS_REQUIREMENTS = (0.992, 0.994, 0.996, 0.998)
COMPARISON_REQUIREMENT = 0.995


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
                "vehicles": 8, "family": "random", "task_counts": TASK_COUNTS,
                "rpds_requirements": RPDS_REQUIREMENTS,
                "comparison_requirement": COMPARISON_REQUIREMENT,
                "methods": METHODS, "seeds": list(range(20, 40)), "rounds_per_source": 3,
                "completed_runs": runs, "source_sha256": hashes,
                "historical_reproduction": False}
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def baseline_result(data, method):
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


def make_row(case, count, requirement, seed, method, result, input_hash):
    metrics = result["metrics"]
    scheduled = [dag for dag in metrics["dags"] if dag["status"] == "scheduled"]
    total_tasks = count * metrics["total_dags"]
    return {"case": case, "tasks_per_dag": count, "requirement": requirement,
            "seed": seed, "method": method, "input_sha256": input_hash,
            "status": result["status"], "eq14_response_s": metrics["eq14_all_vehicles"],
            "backups": metrics["backups"], "backups_per_task": metrics["backups"] / total_tasks,
            "average_reliability": statistics.mean(dag["reliability"] for dag in scheduled),
            "validation_issues": len(result["validation_issues"]),
            "reliability_violations": result["reliability_violations"],
            "infeasible_dags": metrics["infeasible_dags"]}


def summarize(rows):
    output = []
    keys = sorted({(row["tasks_per_dag"], row["requirement"], row["method"]) for row in rows})
    for count, requirement, method in keys:
        group = [row for row in rows if (row["tasks_per_dag"], row["requirement"], row["method"])
                 == (count, requirement, method)]
        complete = [row for row in group if row["status"] == "complete"]
        entry = {"tasks_per_dag": count, "requirement": requirement, "method": method,
                 "runs": len(group), "complete_runs": len(complete)}
        for field in ("eq14_response_s", "backups", "backups_per_task", "average_reliability"):
            values = [row[field] for row in complete]
            entry[field] = statistics.mean(values) if len(complete) == len(group) else None
            entry[field + "_sd"] = statistics.stdev(values) if len(values) > 1 else None
        output.append(entry)
    return output


def plot(output, summary):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 2, figsize=(8.6, 3.45))
    colors = {0.992: "#1764a3", 0.994: "#538b3d", 0.996: "#d17d17", 0.998: "#9864a6"}
    for requirement in RPDS_REQUIREMENTS:
        group = [row for row in summary if row["method"] == "rpds"
                 and row["requirement"] == requirement]
        axes[0].errorbar([row["tasks_per_dag"] for row in group],
                         [row["eq14_response_s"] for row in group],
                         yerr=[row["eq14_response_s_sd"] for row in group],
                         label=f"{100*requirement:.1f}%", color=colors[requirement],
                         marker="o", markersize=3, linewidth=1.2, capsize=2, elinewidth=0.6)
    for method in METHODS:
        group = [row for row in summary if row["method"] == method
                 and row["requirement"] == COMPARISON_REQUIREMENT]
        axes[1].errorbar([row["tasks_per_dag"] for row in group],
                         [row["eq14_response_s"] for row in group],
                         yerr=[row["eq14_response_s_sd"] for row in group],
                         label=LABELS[method], color=COLORS[method],
                         marker="o", markersize=3, linewidth=1.2, capsize=2, elinewidth=0.6)
    axes[0].set_title("(a) RPDS reliability sensitivity", fontsize=10)
    axes[1].set_title("(b) Overall comparison at 99.5%", fontsize=10)
    for ax in axes:
        ax.set_xlabel("Tasks per DAG")
        ax.set_ylabel("Eq. (14) response time (s)")
        ax.grid(alpha=0.2)
        ax.legend(fontsize=7)
    fig.suptitle("Random DAGs: 8 vehicles, three closed-loop DAGs per source")
    fig.tight_layout()
    fig.savefig(output / "task_count_response.pdf")
    fig.savefig(output / "task_count_response.png", dpi=220)
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
    run_plan = [(requirement, ("rpds",)) for requirement in RPDS_REQUIREMENTS]
    run_plan.append((COMPARISON_REQUIREMENT, METHODS))
    total = len(TASK_COUNTS) * 20 * sum(len(methods) for _, methods in run_plan)
    completed = 0
    for count in TASK_COUNTS:
        for seed in range(20, 40):
            for requirement, methods in run_plan:
                case = f"random_t{count}_r{requirement:.3f}_seed{seed}"
                data = workload(seed, 8, "random", 3, requirement=requirement, random_tasks=count)
                case_dir = args.output / "cases" / case
                case_dir.mkdir(parents=True)
                input_path = case_dir / "input.json"
                input_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                input_hash = digest(input_path)
                for method in methods:
                    result = evaluate(data, "path_slack") if method == "rpds" else baseline_result(data, method)
                    result["input_sha256"] = input_hash
                    with gzip.open(case_dir / f"{method}.json.gz", "wt", encoding="utf-8", compresslevel=1) as stream:
                        json.dump(result, stream, ensure_ascii=False, allow_nan=False)
                    rows.append(make_row(case, count, requirement, seed, method, result, input_hash))
                    completed += 1
                if completed % 20 == 0:
                    write_csv(args.output / "runs.csv", rows)
                    snapshot(args.output, "running", completed)
                    print(f"任务数实验 {completed}/{total} 方法运行", flush=True)
    summary = summarize(rows)
    write_csv(args.output / "runs.csv", rows)
    write_csv(args.output / "summary.csv", summary)
    plot(args.output, summary)
    invalid = sum(row["status"] != "complete" for row in rows)
    issues = sum(row["validation_issues"] + row["reliability_violations"] for row in rows)
    lines = ["# 随机DAG任务数实验", "",
             "固定8辆车、每源3个闭环DAG，任务数为20至80。面板(a)比较RPDS的四个可靠性目标；面板(b)在99.5%下比较五种方法。",
             "summary.csv同时给出备份总数、Backups per task和平均建模可靠性，可直接生成替代表格。", "",
             f"方法运行总数：{len(rows)}；非完整运行：{invalid}；约束或可靠性问题：{issues}。",
             "这是新生成的固定种子合成实验，不是历史数据复现。"]
    (args.output / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    snapshot(args.output, "complete", len(rows))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
