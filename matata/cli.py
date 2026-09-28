"""Point d'entrée console `matata` / `python3 -m matata`.

Le cœur reste un fichier unique (`agent-pc/agent.py`) : ce module ne fait que
l'ajouter au sys.path puis déléguer à `agent.main()`. Aucune logique ici.
"""

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_AGENT_DIR = os.path.join(_ROOT, 'agent-pc')


def main():
    if not os.path.exists(os.path.join(_AGENT_DIR, 'agent.py')):
        sys.exit(f"matata: introuvable : {os.path.join(_AGENT_DIR, 'agent.py')}")
    sys.path.insert(0, _AGENT_DIR)
    from agent import main as agent_main  # noqa: E402
    sys.exit(agent_main())


if __name__ == '__main__':
    main()