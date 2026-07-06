import argparse
import json
import sys
from pathlib import Path

from engine.migration_engine import MigrationEngine


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a generic migration workflow")
    parser.add_argument("--config", default="config/migration.json", help="Path to the migration configuration file")
    parser.add_argument("--env", choices=["local", "prod"], default=None, help="Select a predefined environment profile")
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

    engine = MigrationEngine(config)
    engine.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
