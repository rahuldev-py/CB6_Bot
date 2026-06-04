"""
CB6 ML research query interface.

Answers research questions from offline backtest/labeled data only.
No live execution imports. No trades. No live rule changes.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ml_engine.chat.backtest_experiment_runner import run_experiments, save_experiment_outputs
from ml_engine.chat.learning_memory_writer import write_summary


DEFAULT_QUESTION = "How can CB6 achieve 80-85% win rate and at least 2.25 profit factor while keeping risk controlled?"


def ask(question: str = DEFAULT_QUESTION) -> dict:
    payload = run_experiments()
    payload["question"] = question
    paths = save_experiment_outputs(payload)
    summary_path = write_summary(paths)
    paths["ml_learning_summary"] = summary_path
    return {"question": question, "paths": paths, "summary_path": summary_path}


def main() -> None:
    parser = argparse.ArgumentParser(description="Ask CB6 ML an offline strategy research question")
    parser.add_argument("--question", default=DEFAULT_QUESTION)
    args = parser.parse_args()

    result = ask(args.question)
    print("\nCB6 ML research query complete.")
    print(f"Question: {result['question']}")
    print(f"Summary: {result['summary_path']}")
    print("\nFiles:")
    for key, path in result["paths"].items():
        print(f"  {key}: {path}")
    print("\nNo live execution files were modified.")


if __name__ == "__main__":
    main()

