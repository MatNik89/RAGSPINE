"""Run the agent from a saved config: `python -m atlas.agent <cfg.json>`."""
import json
import sys

from atlas.agent.atlas_agent import AgentConfig, main

if __name__ == "__main__":  # pragma: no cover -- entry point on the worker's machine
    with open(sys.argv[1], encoding="utf-8") as f:
        d = json.load(f)
    main(AgentConfig(server_url=d["server_url"], token=d["token"],
                     program_map=d.get("program_map", {}), sign_key=d.get("sign_key", "")))
