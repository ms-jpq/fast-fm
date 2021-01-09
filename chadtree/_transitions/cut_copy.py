from itertools import chain
from locale import strxfrm
from os import linesep
from os.path import basename, dirname, exists, join
from typing import Callable, Mapping, MutableMapping, Optional

from pynvim.api import Nvim
from pynvim_pp.lib import s_write

from ..fs.cartographer import is_dir
from ..fs.ops import unify_ancestors, cut, copy
from ..fs.types import Node
from ..nvim.wm import kill_buffers
from ..registry import rpc
from ..settings.localization import LANG
from ..settings.types import Settings
from ..state.next import forward
from ..state.ops import index
from ..state.types import State
from ..view.ops import display_path
from .refresh import refresh
from .types import Stage


def _find_dest(src: str, node: Node) -> str:
    name = basename(src)
    parent = node.path if is_dir(node) else dirname(node.path)
    dst = join(parent, name)
    return dst


def _operation(
    nvim: Nvim,
    *,
    state: State,
    settings: Settings,
    op_name: str,
    action: Callable[[Mapping[str, str]], None],
) -> Optional[Stage]:
    node = index(nvim, state=state)
    selection = state.selection
    unified = tuple(unify_ancestors(selection))
    if unified and node:
        pre_operations = {src: _find_dest(src, node) for src in unified}
        pre_existing = {s: d for s, d in pre_operations.items() if exists(d)}
        new_operations: MutableMapping[str, str] = {}
        while pre_existing:
            source, dest = pre_existing.popitem()
            resp: Optional[str] = nvim.funcs.input(LANG("path_exists_err"), dest)
            if not resp:
                break
            elif exists(resp):
                pre_existing[source] = resp
            else:
                new_operations[source] = resp

        if pre_existing:
            msg = ", ".join(
                f"{display_path(s, state=state)} -> {display_path(d, state=state)}"
                for s, d in sorted(pre_existing.items(), key=lambda t: strxfrm(t[0]))
            )
            s_write(
                nvim, f"⚠️  -- {op_name}: path(s) already exist! :: {msg}", error=True
            )
            return None
        else:
            operations: Mapping[str, str] = {**pre_operations, **new_operations}
            msg = linesep.join(
                f"{display_path(s, state=state)} -> {display_path(d, state=state)}"
                for s, d in sorted(operations.items(), key=lambda t: strxfrm(t[0]))
            )

            question = f"{op_name}{linesep}{msg}?"
            resp = nvim.funcs.confirm(question, LANG("ask_yesno", linesep=linesep), 2)
            ans = resp == 1

            if ans:
                try:
                    action(operations)
                except Exception as e:
                    s_write(nvim, e, error=True)
                    return refresh(nvim, state=state, settings=settings)
                else:
                    paths = frozenset(
                        dirname(p)
                        for p in chain(operations.keys(), operations.values())
                    )
                    _index = state.index | paths
                    new_state = forward(
                        state,
                        settings=settings,
                        index=_index,
                        selection=frozenset(),
                        paths=paths,
                    )

                    kill_buffers(nvim, paths=selection)
                    return Stage(new_state)
            else:
                return None
    else:
        s_write(nvim, LANG("nothing_select"), error=True)
        return None


@rpc(blocking=False)
def _cut(
    nvim: Nvim, state: State, settings: Settings, is_visual: bool
) -> Optional[Stage]:
    """
    Cut selected
    """

    return _operation(
        nvim, state=state, settings=settings, op_name=LANG("cut"), action=cut
    )


@rpc(blocking=False)
def _copy(
    nvim: Nvim, state: State, settings: Settings, is_visual: bool
) -> Optional[Stage]:
    """
    Copy selected
    """

    return _operation(
        nvim, state=state, settings=settings, op_name=LANG("copy"), action=copy
    )
