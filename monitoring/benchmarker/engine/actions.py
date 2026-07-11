import os
import subprocess

from loguru import logger

from monitoring.benchmarker.configurations.actions import (
    BenchmarkActionName,
    BenchmarkActionSpecification,
)


def get_repo_root() -> str:
    """Return the root path of the monitoring repository."""
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))


def run_scenario_actions(
    action_names: list[BenchmarkActionName] | None,
    all_actions: list[BenchmarkActionSpecification] | None,
) -> None:
    """Run a sequence of scenario setup or teardown actions by name."""
    if not action_names:
        return

    actions_map: dict[str, BenchmarkActionSpecification] = {}
    if all_actions:
        for action in all_actions:
            actions_map[action.name] = action

    for action_name in action_names:
        if action_name not in actions_map:
            raise ValueError(
                f"Scenario action '{action_name}' not defined in configuration.actions"
            )
        action_spec = actions_map[action_name]

        if "run_command" in action_spec and action_spec.run_command is not None:
            cmd_spec = action_spec.run_command
            repo_root = get_repo_root()
            cwd = cmd_spec.path.replace("$REPO_ROOT", repo_root)
            env = os.environ.copy()
            if "env" in cmd_spec and cmd_spec.env:
                for k, v in cmd_spec.env.items():
                    env[k] = str(v)

            logger.info(
                f"Running action '{action_name}': {cmd_spec.command} (cwd={cwd})"
            )
            subprocess.run(cmd_spec.command, shell=True, cwd=cwd, env=env, check=True)
        else:
            raise NotImplementedError(
                f"Action '{action_name}' has no recognized action specification implemented in this version of benchmarker"
            )
