import argparse
import json
import sys
from pathlib import Path

from engine.migration_engine import MigrationEngine


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a generic migration workflow")
    parser.add_argument("--config", default="config/migration.json", help="Path to the migration configuration file")
    parser.add_argument("--env", choices=["local", "prod"], default=None, help="Select a predefined environment profile")
    parser.add_argument("--retry-failed", action="store_true", help="Retry issues captured in the target failed_issues.json export")
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = (Path(__file__).resolve().parent / config_path).resolve()

    if not config_path.exists():
        print(f"Configuration file not found: {config_path}")
        return 1

    config = json.loads(config_path.read_text(encoding="utf-8"))
    if args.env:
        env_config_path = Path(__file__).resolve().parent / "config" / "environments.json"
        envs = json.loads(env_config_path.read_text(encoding="utf-8"))
        if args.env not in envs:
            print(f"Unknown environment: {args.env}")
            return 1

        selected = envs[args.env]
        config["source"] = selected["source"]
        config["target"] = selected["target"]

    try:
        engine = MigrationEngine(config)
        if args.retry_failed:
            engine.retry_failed_issues()
        else:
            engine.run()
    except Exception as exc:
        print(f"Migration failed: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
