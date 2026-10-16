"""汇总任务选择消融，并补跑新排序下的STPF/Dijkstra路径消融。"""
import argparse
import csv
import gzip
import json
from collections import defaultdict
from pathlib import Path
import statistics

from .ablation import digest
from .ablation_suite import evaluate


WEIGHTS = {"ge": 0.4, "random": 0.3, "fft": 0.3}
SELECTION_BASELINES = ("fcfs", "eft")


def read_csv(path):
    with path.open(encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def write_csv(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def index_saved_runs(source):
    """索引冻结验证集；只接受全部运行完成且验证通过的记录。"""
    rows = read_csv(source / "runs.csv")
    wanted = {"path_slack", *SELECTION_BASELINES}
    selected = [row for row in rows if row["variant"] in wanted]
    if len(selected) != 420 * len(wanted):
        raise ValueError("冻结验证集缺少任务选择对照")
    for row in selected:
        if row["status"] != "complete" or int(row["validation_issues"]) or int(row["reliability_violations"]):
            raise ValueError(f"冻结结果未通过验证: {row['case']}/{row['variant']}")
    return {(row["case"], row["variant"]): row for row in selected}


def weighted_observations(per_family):
    """将相同车辆数、种子、比较对象的三类相对差异按40/30/30加权。"""
    grouped = defaultdict(dict)
    for row in per_family:
        grouped[(row["comparison"], row["vehicles"], row["seed"])][row["family"]] = row["reduction_percent"]
    combined = []
    for (comparison, vehicles, seed), families in sorted(grouped.items()):
        if set(families) != set(WEIGHTS):
            raise ValueError(f"{comparison}, n={vehicles}, seed={seed} 缺少工作负载")
        combined.append({"comparison": comparison, "vehicles": vehicles, "seed": seed,
                         "response_reduction_percent": sum(WEIGHTS[f] * families[f] for f in WEIGHTS)})
    return combined


def task_selection_rows(saved):
    """正值表示新任务选择的Eq14响应低于FCFS或EFT-order。"""
    result = []
    cases = sorted({case for case, variant in saved if variant == "path_slack"})
    for case in cases:
        candidate = saved[case, "path_slack"]
        for baseline in SELECTION_BASELINES:
            other = saved[case, baseline]
            if candidate["input_sha256"] != other["input_sha256"]:
                raise ValueError(f"输入不一致: {case}/{baseline}")
            base_response = float(other["eq14_response_s"])
            new_response = float(candidate["eq14_response_s"])
            result.append({"case": case, "family": candidate["family"],
                           "vehicles": int(candidate["vehicles"]), "seed": int(candidate["seed"]),
                           "comparison": f"new_vs_{baseline}",
                           "reduction_percent": 100 * (base_response - new_response) / base_response})
    return result


def route_rows(source, output, saved):
    """复用STPF结果，只新增完全相同配置下的全图Dijkstra运行。"""
    result = []
    cases = sorted({case for case, variant in saved if variant == "path_slack"})
    for index, case in enumerate(cases, 1):
        original_row = saved[case, "path_slack"]
        case_dir = source / "cases" / case
        input_path = case_dir / "input.json"
        if digest(input_path) != original_row["input_sha256"]:
            raise ValueError(f"输入哈希不一致: {case}")
        with gzip.open(case_dir / "path_slack.json.gz", "rt", encoding="utf-8") as stream:
            stpf = json.load(stream)
        data = json.loads(input_path.read_text(encoding="utf-8"))
        dijkstra = evaluate(data, "path_slack_dijkstra")
        if dijkstra["status"] != "complete" or dijkstra["validation_issues"] or dijkstra["reliability_violations"]:
            raise ValueError(f"Dijkstra运行未通过验证: {case}")
        raw_dir = output / "cases" / case
        raw_dir.mkdir(parents=True)
        with gzip.open(raw_dir / "path_slack_dijkstra.json.gz", "wt", encoding="utf-8", compresslevel=1) as stream:
            json.dump(dijkstra, stream, ensure_ascii=False, allow_nan=False)
        stpf_response = stpf["metrics"]["eq14_all_vehicles"]
        dijkstra_response = dijkstra["metrics"]["eq14_all_vehicles"]
        result.append({"case": case, "family": original_row["family"],
                       "vehicles": int(original_row["vehicles"]), "seed": int(original_row["seed"]),
                       "comparison": "stpf_vs_dijkstra",
                       "reduction_percent": 100 * (dijkstra_response - stpf_response) / dijkstra_response})
        if index % 20 == 0:
            write_csv(output / "route_family_pairs_partial.csv", result)
            print(f"路径消融 {index}/420", flush=True)
    return result


def summaries(rows):
    output = []
    for comparison in sorted({row["comparison"] for row in rows}):
        for vehicles in sorted({row["vehicles"] for row in rows}):
            group = [row["response_reduction_percent"] for row in rows
                     if row["comparison"] == comparison and row["vehicles"] == vehicles]
            output.append({"comparison": comparison, "vehicles": vehicles, "observations": len(group),
                           "mean_reduction_percent": statistics.mean(group),
                           "sd_reduction_percent": statistics.stdev(group)})
    return output


def plot(output, summary):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    labels = {"new_vs_fcfs": "New priority vs. FCFS-order",
              "new_vs_eft": "New priority vs. EFT-order",
              "stpf_vs_dijkstra": "STPF vs. full-graph Dijkstra"}
    colors = {"new_vs_fcfs": "#1764a3", "new_vs_eft": "#d17d17",
              "stpf_vs_dijkstra": "#538b3d"}
    fig, axes = plt.subplots(1, 2, figsize=(8.6, 3.4))
    panels = ((axes[0], ("new_vs_fcfs", "new_vs_eft"), "(a) Task-selection ablation"),
              (axes[1], ("stpf_vs_dijkstra",), "(b) Path-selection ablation"))
    for ax, comparisons, title in panels:
        for comparison in comparisons:
            group = sorted((row for row in summary if row["comparison"] == comparison),
                           key=lambda row: row["vehicles"])
            ax.errorbar([row["vehicles"] for row in group],
                        [row["mean_reduction_percent"] for row in group],
                        yerr=[row["sd_reduction_percent"] for row in group],
                        label=labels[comparison], color=colors[comparison],
                        marker="o", markersize=3.5, linewidth=1.3, capsize=2)
        ax.axhline(0, color="#777777", linewidth=0.8)
        ax.set_xlabel("Vehicles")
        ax.set_ylabel("Mean response-time reduction (%)")
        ax.set_title(title, fontsize=10)
        ax.grid(alpha=0.2)
        ax.legend(fontsize=7)
    fig.suptitle("Weighted aggregate: 40% GE, 30% Random, 30% FFT")
    fig.tight_layout()
    fig.savefig(output / "selection_path_ablation.pdf")
    fig.savefig(output / "selection_path_ablation.png", dpi=220)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("输出目录已存在，请选择新目录")
    args.output.mkdir(parents=True)
    saved = index_saved_runs(args.source)
    selection_family = task_selection_rows(saved)
    route_family = route_rows(args.source, args.output, saved)
    weighted = weighted_observations(selection_family + route_family)
    summary = summaries(weighted)
    write_csv(args.output / "selection_family_pairs.csv", selection_family)
    write_csv(args.output / "route_family_pairs.csv", route_family)
    write_csv(args.output / "weighted_pairs.csv", weighted)
    write_csv(args.output / "summary_by_vehicles.csv", summary)
    plot(args.output, summary)

    lines = ["# 任务选择与路径选择消融", "",
             "固定动态可靠性分配、EFT车辆分配及其他调度规则。任务选择消融只替换就绪任务顺序；路径消融只将STPF替换为全图Dijkstra。",
             "结果按相同车辆数和种子组成40% GE、30% Random、30% FFT的加权观测。正值表示完整RPDS响应时间更低。",
             "该加权汇总不表示三类DAG在同一车队内并发竞争。", "",
             "|比较|平均降幅(%)|标准差(%)|改善/相同/变差|", "|---|---:|---:|---:|"]
    for comparison in ("new_vs_fcfs", "new_vs_eft", "stpf_vs_dijkstra"):
        values = [row["response_reduction_percent"] for row in weighted if row["comparison"] == comparison]
        win = sum(value > 1e-10 for value in values)
        tie = sum(abs(value) <= 1e-10 for value in values)
        loss = sum(value < -1e-10 for value in values)
        lines.append(f"|{comparison}|{statistics.mean(values):.4f}|{statistics.stdev(values):.4f}|{win}/{tie}/{loss}|")
    lines += ["", "全部新增Dijkstra运行均执行独立调度约束和逐DAG可靠性检查。策略计算时间不计入应用响应时间，也不在图中展示。"]
    (args.output / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
