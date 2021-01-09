from enum import IntEnum, auto
from fnmatch import fnmatch
from locale import strxfrm
from os import linesep
from os.path import sep
from typing import Any, Callable, Iterator, Optional, Sequence, Tuple, cast

from pynvim_pp.highlight import HLgroup
from std2.functools import constantly
from std2.types import never

from ..fs.types import Index, Mode, Node
from ..settings.types import Settings
from ..state.types import FilterPattern, QuickFix, Selection
from ..version_ctl.types import VCStatus
from .types import Badge, Derived, Highlight, Render, Sortby


class _CompVals(IntEnum):
    FOLDER = auto()
    FILE = auto()


def _gen_comp(sortby: Sequence[Sortby]) -> Callable[[Node], Any]:
    def comp(node: Node) -> Sequence[Any]:
        def cont() -> Iterator[Any]:
            for sb in sortby:
                if sb is Sortby.is_folder:
                    yield _CompVals.FOLDER if Mode.folder in node.mode else _CompVals.FILE
                elif sb is Sortby.ext:
                    yield strxfrm(node.ext or ""),
                elif sb is Sortby.fname:
                    yield strxfrm(node.name)
                else:
                    never(sb)

        return tuple(cont())

    return comp


def _ignore(settings: Settings, vc: VCStatus) -> Callable[[Node], bool]:
    def drop(node: Node) -> bool:
        ignore = (
            node.path in vc.ignored
            or any(fnmatch(node.name, pattern) for pattern in settings.ignores.name)
            or any(fnmatch(node.path, pattern) for pattern in settings.ignores.path)
        )
        return ignore

    return drop


def _paint(
    settings: Settings,
    index: Index,
    selection: Selection,
    qf: QuickFix,
    vc: VCStatus,
    current: Optional[str],
) -> Callable[[Node, int], Render]:
    context = settings.view.hl_context
    mode_lookup_pre, mode_lookup_post, ext_lookup, name_lookup = (
        context.mode_lookup_pre,
        context.mode_lookup_post,
        context.ext_lookup,
        context.name_lookup,
    )
    icons, highlights = settings.view.icons, settings.view.highlights

    def search_hl(node: Node) -> Optional[HLgroup]:
        s_modes = sorted(node.mode)

        for mode in s_modes:
            hl = mode_lookup_pre.get(mode)
            if hl:
                return hl
        hl = ext_lookup.get(node.ext or "")
        if hl:
            return hl
        for pattern, group in name_lookup.items():
            if fnmatch(node.name, pattern):
                return group
        for mode in s_modes:
            hl = mode_lookup_post.get(mode)
            if hl:
                return hl

        return mode_lookup_post.get(None)

    def gen_spacer(depth: int) -> str:
        return (depth * 2 - 1) * " "

    def gen_status(path: str) -> str:
        selected = icons.status.selected if path in selection else " "
        active = icons.status.active if path == current else " "
        return f"{selected}{active}"

    def gen_decor_pre(node: Node, depth: int) -> Iterator[str]:
        yield gen_spacer(depth)
        yield gen_status(node.path)

    def gen_icon(node: Node) -> Iterator[str]:
        yield " "
        if Mode.folder in node.mode:
            yield icons.folder.open if node.path in index else icons.folder.closed
        else:
            yield (
                icons.name_exact.get(node.name, "")
                or icons.type.get(node.ext or "", "")
                or next(
                    (v for k, v in icons.name_glob.items() if fnmatch(node.name, k)),
                    icons.default_icon,
                )
            ) if settings.view.use_icons else icons.default_icon
        yield " "

    def gen_name(node: Node) -> Iterator[str]:
        yield node.name.replace(linesep, r"\n")
        if not settings.view.use_icons and Mode.folder in node.mode:
            yield sep

    def gen_decor_post(node: Node) -> Iterator[str]:
        mode = node.mode
        if Mode.orphan_link in mode:
            yield " "
            yield icons.link.broken
        elif Mode.link in mode:
            yield " "
            yield icons.link.normal

    def gen_badges(path: str) -> Iterator[Badge]:
        qf_count = qf.locations[path]
        stat = vc.status.get(path)
        if qf_count:
            yield Badge(text=f"({qf_count})", group=highlights.groups.quickfix)
        if stat:
            yield Badge(text=f"[{stat}]", group=highlights.groups.version_control)

    def gen_highlights(
        node: Node, pre: str, icon: str, name: str
    ) -> Iterator[Highlight]:
        begin = len(pre.encode())
        end = begin + len(icon.encode())
        group = highlights.exts.get(node.ext or "")
        if group:
            hl = Highlight(group=group.name, begin=begin, end=end)
            yield hl
        group = search_hl(node)
        if group:
            begin = end
            end = len(name.encode()) + begin
            hl = Highlight(group=group.name, begin=begin, end=end)
            yield hl

    def show(node: Node, depth: int) -> Render:
        pre = "".join(gen_decor_pre(node, depth=depth))
        icon = "".join(gen_icon(node))
        name = "".join(gen_name(node))
        post = "".join(gen_decor_post(node))

        line = f"{pre}{icon}{name}{post}"
        badges = tuple(gen_badges(node.path))
        highlights = tuple(gen_highlights(node, pre=pre, icon=icon, name=name))
        render = Render(line=line, badges=badges, highlights=highlights)
        return render

    return show


def render(
    node: Node,
    *,
    settings: Settings,
    index: Index,
    selection: Selection,
    filter_pattern: Optional[FilterPattern],
    qf: QuickFix,
    vc: VCStatus,
    show_hidden: bool,
    current: Optional[str],
) -> Derived:
    drop = (
        cast(Callable[[Node], bool], constantly(False))
        if show_hidden
        else _ignore(settings, vc=vc)
    )
    show = _paint(
        settings, index=index, selection=selection, qf=qf, vc=vc, current=current
    )
    comp = _gen_comp(settings.view.sort_by)
    keep_open = {node.path}

    def render(
        node: Node, *, depth: int, cleared: bool
    ) -> Iterator[Tuple[Node, Render]]:
        clear = (
            cleared or not filter_pattern or fnmatch(node.name, filter_pattern.pattern)
        )
        rend = show(node, depth)

        def gen_children() -> Iterator[Tuple[Node, Render]]:
            gen = (child for child in (node.children or {}).values() if not drop(child))
            for child in sorted(gen, key=comp):
                yield from render(child, depth=depth + 1, cleared=clear)

        children = tuple(gen_children())
        if clear or children or node.path in keep_open:
            yield node, rend
        yield from iter(children)

    _lookup, _rendered = zip(*render(node, depth=0, cleared=False))
    lookup = cast(Sequence[Node], _lookup)
    rendered = cast(Sequence[Render], _rendered)
    paths_lookup = {node.path: idx for idx, node in enumerate(lookup)}
    derived = Derived(lookup=lookup, paths_lookup=paths_lookup, rendered=rendered)
    return derived
