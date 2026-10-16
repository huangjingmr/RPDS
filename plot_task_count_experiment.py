from pathlib import Path
import csv

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


SUMMARY_FILE = Path(
    "/Users/hj/Projects/RPDS/reports/"
    "task_count_new_rpds_20261016/summary.csv"
)

OUTPUT_FILE = Path(
    "/Users/hj/Documents/My papers/"
    "Reliability-Aware Collaborative Computing for Multi-DAG "
    "Task Offloading in Vehicular Fleets/"
    "images/rpds_task_count_20261016.pdf"
)


def read_summary():
    with SUMMARY_FILE.open(encoding="utf-8-sig") as file:
        return list(csv.DictReader(file))


def plot_fig6():
    rows = read_summary()

    methods = [
        "rpds",
        "local_maxre",
        "local_rr",
        "heft_maxre",
        "heft_rr",
    ]

    labels = {
        "rpds": "RPDS",
        "local_maxre": "Local&MaxRe",
        "local_rr": "Local&RR",
        "heft_maxre": "HEFT&MaxRe",
        "heft_rr": "HEFT&RR",
    }

    method_colors = {
        "rpds": "#c62828",
        "local_maxre": "#666666",
        "local_rr": "#e69500",
        "heft_maxre": "#2465a8",
        "heft_rr": "#25844b",
    }

    method_markers = {
        "rpds": "o",
        "local_maxre": "s",
        "local_rr": "^",
        "heft_maxre": "D",
        "heft_rr": "v",
    }

    reliability_values = [
        0.992,
        0.994,
        0.996,
        0.998,
    ]

    reliability_colors = {
        0.992: "#1764a3",
        0.994: "#538b3d",
        0.996: "#d17d17",
        0.998: "#9864a6",
    }

    plt.rcParams.update({
        "font.family": "serif",
        "font.size": 7.2,
        "axes.labelsize": 7.2,
        "axes.titlesize": 7.2,
        "xtick.labelsize": 6.5,
        "ytick.labelsize": 6.5,
        "legend.fontsize": 5.8,

        # 将文字嵌入矢量 PDF，保证放大后清晰
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
    })

    # 按单栏最终尺寸绘制，避免 LaTeX 大幅缩小
    fig, axes = plt.subplots(
        1,
        2,
        figsize=(4.5, 1.78),
    )

    # -----------------------------
    # (a) RPDS：不同可靠性要求
    # -----------------------------
    for requirement in reliability_values:
        selected = [
            row for row in rows
            if row["method"] == "rpds"
            and abs(
                float(row["requirement"]) - requirement
            ) < 1e-9
        ]

        selected.sort(
            key=lambda row: int(row["tasks_per_dag"])
        )

        axes[0].errorbar(
            [
                int(row["tasks_per_dag"])
                for row in selected
            ],
            [
                float(row["eq14_response_s"])
                for row in selected
            ],
            yerr=[
                float(row["eq14_response_s_sd"])
                for row in selected
            ],
            label=f"{100 * requirement:.1f}%",
            color=reliability_colors[requirement],
            marker="o",
            markersize=2.4,
            linewidth=1.0,
            capsize=1.8,
            elinewidth=0.6,
        )

    # -----------------------------
    # (b) 五种方法：R = 99.5%
    # -----------------------------
    comparison_requirement = 0.995

    for method in methods:
        selected = [
            row for row in rows
            if row["method"] == method
            and abs(
                float(row["requirement"])
                - comparison_requirement
            ) < 1e-9
        ]

        selected.sort(
            key=lambda row: int(row["tasks_per_dag"])
        )

        axes[1].errorbar(
            [
                int(row["tasks_per_dag"])
                for row in selected
            ],
            [
                float(row["eq14_response_s"])
                for row in selected
            ],
            yerr=[
                float(row["eq14_response_s_sd"])
                for row in selected
            ],
            label=labels[method],
            color=method_colors[method],
            marker=method_markers[method],
            markersize=2.4,
            linewidth=1.0,
            capsize=1.8,
            elinewidth=0.6,
        )

    axes[0].set_title(
        "(a) RPDS reliability sensitivity",
        pad=2,
    )

    axes[1].set_title(
        "(b) Overall comparison at 99.5%",
        pad=2,
    )

    for ax in axes:
        ax.set_xlabel(
            "Tasks per DAG",
            labelpad=1.5,
        )

        ax.grid(
            alpha=0.22,
            linewidth=0.4,
        )

        ax.tick_params(
            axis="both",
            pad=1.5,
            length=2.5,
        )

        ax.margins(x=0.025)

    # 公共纵轴标题，减少重复占用空间
    fig.supylabel(
        "Average response time (s)",
        x=0.012,
        fontsize=7.2,
    )

    axes[0].legend(
        loc="upper left",
        ncol=2,
        frameon=False,
        columnspacing=0.6,
        handlelength=1.1,
        handletextpad=0.3,
        borderpad=0.1,
    )

    axes[1].legend(
        loc="upper left",
        ncol=2,
        frameon=False,
        columnspacing=0.55,
        handlelength=1.0,
        handletextpad=0.25,
        borderpad=0.1,
    )

    # 压缩外边距和两图之间的空白
    fig.subplots_adjust(
        left=0.105,
        right=0.995,
        bottom=0.215,
        top=0.875,
        wspace=0.23,
    )

    fig.savefig(
        OUTPUT_FILE,
        format="pdf",
        bbox_inches="tight",
        pad_inches=0.01,
    )

    plt.close(fig)

    print(f"Generated: {OUTPUT_FILE}")


if __name__ == "__main__":
    plot_fig6()