"""Editing a procedure's statement tree in place (used by the procedure editor).

A statement is addressed by its path, e.g. ``(0, "body", 2, "else", 0)``; the path of an If followed by "else"
(``(0, "body", 2, "else")``) addresses its Else branch, followed by ("elif", k) its k-th else-if clause, whose
statements are at ``(..., "elif", k, "body", i)``. Each operation edits ``proc["statements"]`` and returns the path
of the statement to select afterwards (None when nothing changed)."""

from __future__ import annotations

import copy

from .catalog import CONTAINERS
from .model import branch_block, is_branch, iter_statements, new_elif, statement_at


def block(proc: dict, path: tuple) -> list:
    """The statement list at a block path: () is the top level, otherwise the path of a container followed by
    "body" or "else", or of an If followed by ("elif", k, "body") (created if missing)."""
    stmts = proc.setdefault("statements", [])
    st = None
    for k in path:
        if isinstance(k, int):
            st = stmts[k]
        else:
            stmts = st.setdefault(k, [])
    return stmts


def _owner(block_path: tuple) -> tuple:
    """The path of the statement that owns a (non top-level) block."""
    if len(block_path) >= 3 and block_path[-3] == "elif":
        return block_path[:-3]
    return block_path[:-1]


def add_elif(proc: dict, path: tuple) -> tuple | None:
    """Add an else-if clause at the end of an If's clauses; returns the clause's path."""
    st = statement_at(proc, path)
    if st is None or st.get("type") != "if" or is_branch(path):
        return None
    clauses = st.setdefault("elif", [])
    clauses.append(new_elif())
    return path + ("elif", len(clauses) - 1)


def path_of(proc: dict, st: dict) -> tuple | None:
    return next((p for p, s in iter_statements(proc.get("statements")) if s is st), None)


def add(proc: dict, path: tuple | None, st: dict, inside: bool = False) -> tuple | None:
    """Add a statement after the one at `path` (at the end when there is none), or inside it (at the end of a
    container's body or of an Else branch)."""
    if path and is_branch(path):
        inside = True
    if inside and path:
        if is_branch(path):
            stmts = block(proc, branch_block(path))
            new = branch_block(path) + (len(stmts),)
        else:
            parent = statement_at(proc, path)
            if parent is None or parent.get("type") not in CONTAINERS:
                return None
            stmts = block(proc, path + ("body",))
            new = path + ("body", len(stmts))
        stmts.append(st)
        return new
    if path:
        i = path[-1]
        block(proc, path[:-1]).insert(i + 1, st)
        return path[:-1] + (i + 1,)
    stmts = block(proc, ())
    stmts.append(st)
    return (len(stmts) - 1,)


def remove(proc: dict, path: tuple) -> tuple:
    """Remove a statement (or an Else branch or else-if clause); returns the neighbour to select (() when none is
    left)."""
    if path[-1] == "else":
        statement_at(proc, path[:-1]).pop("else", None)
        return path[:-1]
    if is_branch(path):
        st = statement_at(proc, path[:-2])
        del st["elif"][path[-1]]
        if not st["elif"]:
            del st["elif"]
        return path[:-2]
    stmts, i = block(proc, path[:-1]), path[-1]
    del stmts[i]
    if i < len(stmts):
        return path[:-1] + (i,)
    if i > 0:
        return path[:-1] + (i - 1,)
    return path[:-2] if len(path) > 1 else ()


def duplicate(proc: dict, path: tuple) -> tuple | None:
    if is_branch(path):
        return None
    stmts, i = block(proc, path[:-1]), path[-1]
    stmts.insert(i + 1, copy.deepcopy(stmts[i]))
    return path[:-1] + (i + 1,)


def move(proc: dict, path: tuple, d: int) -> tuple | None:
    """Move a statement up (d = -1) or down (d = 1) within its block."""
    if is_branch(path):
        return None
    stmts, i = block(proc, path[:-1]), path[-1]
    j = i + d
    if not 0 <= j < len(stmts):
        return None
    stmts[i], stmts[j] = stmts[j], stmts[i]
    return path[:-1] + (j,)


def indent(proc: dict, path: tuple) -> tuple | None:
    """Move a statement into the container just above it (at the end of its last branch: Else, else-if or
    body)."""
    if is_branch(path):
        return None
    stmts, i = block(proc, path[:-1]), path[-1]
    if i == 0 or stmts[i - 1].get("type") not in CONTAINERS:
        return None
    target = stmts[i - 1]
    if target.get("type") == "if" and "else" in target:
        branch = ("else",)
    elif target.get("type") == "if" and target.get("elif"):
        branch = ("elif", len(target["elif"]) - 1, "body")
    else:
        branch = ("body",)
    dest = block(proc, path[:-1] + (i - 1,) + branch)
    dest.append(stmts.pop(i))
    return path[:-1] + (i - 1,) + branch + (len(dest) - 1,)


def outdent(proc: dict, path: tuple) -> tuple | None:
    """Move a statement out of its block, just after the block's statement."""
    if is_branch(path) or len(path) < 3:
        return None
    st = block(proc, path[:-1]).pop(path[-1])
    parent = _owner(path[:-1])
    block(proc, parent[:-1]).insert(parent[-1] + 1, st)
    return parent[:-1] + (parent[-1] + 1,)


def toggle(proc: dict, path: tuple) -> tuple | None:
    """Disable an enabled statement, enable a disabled one."""
    st = statement_at(proc, path) if not is_branch(path) else None
    if st is None:
        return None
    if st.get("enabled", True) is False:
        st.pop("enabled", None)
    else:
        st["enabled"] = False
    return path


def move_to(proc: dict, path: tuple, dest: tuple, index: int) -> tuple:
    """Move a statement (drag and drop) into the block `dest` (see block) at `index`, counted once the statement
    has left its place; returns its new path."""
    target = block(proc, dest)  # resolved before the move shifts any index
    st = block(proc, path[:-1]).pop(path[-1])
    target.insert(index, st)
    return path_of(proc, st)
