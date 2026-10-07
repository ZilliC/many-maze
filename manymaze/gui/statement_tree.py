"""The statement tree of the procedure editor: a procedure's statements as ANY-maze-like coloured blocks, with
internal drag and drop. Each item holds the path of its statement (see core.procedures.edit)."""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QFontDatabase, QFontMetrics, QIcon, QPainter, QPalette, QPen
from PySide6.QtWidgets import QAbstractItemView, QStyle, QStyledItemDelegate, QTreeWidget, QTreeWidgetItem

from ..core import procedures as pr

PATH_ROLE = Qt.UserRole  # the statement's path (an If's path + ("else",) for its Else branch)
TYPE_ROLE = Qt.UserRole + 1  # statement type of a tree item (ELSE for an Else branch)
ENABLED_ROLE = Qt.UserRole + 2  # False for a disabled statement
ELSE = "__else__"

COLORS = {"when": "#7c3aed", "wait": "#b45309", "if": "#2563eb", ELSE: "#2563eb", "repeat": "#0d9488",
          "set": "#15803d", "do": None, "stop": "#dc2626", "comment": "#6b7280", "var": "#166534"}
# ANY-maze style statement blocks: (fill, border); parameters sit in a lighter "pill"
BLOCK_COLORS = {"wait": ("#ffc2c2", "#f28b8b"), "stop": ("#ffc2c2", "#e46a6a"),
                "do": ("#cdf3c6", "#97d68d"),
                "if": ("#ffe1a6", "#ecb453"), ELSE: ("#ffe1a6", "#ecb453"), "repeat": ("#ffeaa0", "#e2be4a"),
                "when": ("#cfe0fb", "#8eaee6"),
                "set": ("#e5d4f7", "#b897df"), "var": ("#e5d4f7", "#b897df"),
                "comment": ("#f2f2f2", "#e0e0e0")}
PILL_COLORS = {"wait": "#dcb8f0", "when": "#dcb8f0", "if": "#fff6dc", "repeat": "#fff6dc", "do": "#f3fcf0",
               "set": "#f7f0fd", "var": "#f7f0fd", "stop": "#ffe9e9"}
BLOCK_LABELS = [("Wait until ", "Wait until:"), ("Wait for ", "Wait for:"), ("Wait ", "Wait:"), ("When ", "When:"),
                ("If ", "If:"), ("Repeat ", "Repeat:"), ("Set ", "Set:"), ("Do: ", "Action:"), ("# ", "Note:"),
                ("Variable ", "Variable:")]


def block_parts(text: str, type_: str | None) -> tuple[str, str]:
    """Split a statement summary into the block's label and its parameters ("Do: Pellet" -> "Action:", "Pellet")."""
    if type_ == ELSE:
        return "Else:", ""
    if type_ == "stop":
        return "Stop:", text
    for prefix, label in BLOCK_LABELS:
        if text.startswith(prefix):
            return label, text[len(prefix):]
    if type_ == "comment":
        return "Note:", text
    return text, ""


def mono_font(base: QFont | None = None) -> QFont:
    f = QFontDatabase.systemFont(QFontDatabase.FixedFont)
    f.setFamilies(["Menlo", "Consolas", "DejaVu Sans Mono", "Courier New", f.family()])
    size = base.pointSizeF() if base is not None and base.pointSizeF() > 0 else 10.0
    f.setPointSizeF(size)
    return f


class StatementDelegate(QStyledItemDelegate):
    """Paints each statement as an ANY-maze-like coloured rounded block: red "Wait", green "Action", orange
    "If/Repeat", blue "When", purple "Set/Variable", grey "Note", with the parameters in a lighter pill."""

    ROW_HEIGHT = 32

    def _font(self, option) -> QFont:
        return mono_font(option.font)

    def _geometry(self, option, index):
        font = self._font(option)
        fm = QFontMetrics(font)
        label, body = block_parts(index.data(Qt.DisplayRole) or "", index.data(TYPE_ROLE))
        lw = fm.horizontalAdvance(label)
        bw = fm.horizontalAdvance(body) if body else 0
        width = 10 + lw + (10 + bw + 14 if body else 0) + 10
        return font, fm, label, body, lw, bw, width

    def sizeHint(self, option, index):
        *_, width = self._geometry(option, index)
        return QSize(width + 30, self.ROW_HEIGHT)

    def paint(self, p: QPainter, option, index):
        font, fm, label, body, lw, bw, width = self._geometry(option, index)
        t = index.data(TYPE_ROLE)
        enabled = index.data(ENABLED_ROLE) is not False
        bg = index.data(Qt.BackgroundRole)
        error = isinstance(bg, QBrush) and bg.style() != Qt.NoBrush
        selected = bool(option.state & QStyle.State_Selected)
        fill, border = BLOCK_COLORS.get(t, ("#eeeeee", "#d0d0d0"))
        pill = PILL_COLORS.get(t)
        text_col = QColor("#262626")
        if not enabled:
            fill, border, pill, text_col = "#efefef", "#d6d6d6", "#f7f7f7", QColor("#9a9a9a")
        r = option.rect
        icon = index.data(Qt.DecorationRole)
        extra = 20 if isinstance(icon, QIcon) and not icon.isNull() else 0
        block = QRectF(r.x() + 1.5, r.y() + 2.5, min(width, max(40, r.width() - 4 - extra)), r.height() - 5)
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        fc = QColor(fill)
        if selected:
            fc = fc.darker(108)
        pen = QPen(QColor("#dc2626") if error else QColor("#2f6fbf") if selected else QColor(border))
        pen.setWidthF(2.0 if (error or selected) else 1.0)
        p.setPen(pen)
        p.setBrush(fc)
        p.drawRoundedRect(block, 6, 6)
        f = QFont(font)
        f.setStrikeOut(not enabled)
        p.setFont(f)
        p.setPen(text_col)
        x = block.x() + 10
        avail = block.right() - x - 8
        p.drawText(QRectF(x, block.y(), min(lw, avail), block.height()), Qt.AlignVCenter | Qt.AlignLeft,
                   fm.elidedText(label, Qt.ElideRight, int(max(0, avail))))
        if body:
            bx = x + lw + 10
            pw = min(bw + 14, block.right() - bx - 6)
            if pw > 20:
                pr_ = QRectF(bx, block.y() + 4, pw, block.height() - 8)
                if pill:
                    p.setPen(QPen(QColor(border).lighter(105), 1))
                    p.setBrush(QColor(pill))
                    p.drawRoundedRect(pr_, 4, 4)
                p.setPen(text_col)
                p.drawText(pr_.adjusted(7, 0, -4, 0), Qt.AlignVCenter | Qt.AlignLeft,
                           fm.elidedText(body, Qt.ElideRight, int(pr_.width() - 11)))
        if extra:
            icon.paint(p, int(block.right() + 4), int(r.y() + (r.height() - 16) / 2), 16, 16)
        p.restore()


class StatementTree(QTreeWidget):
    """Tree of statements with internal drag & drop; emits ``dropped`` after a move (the moved item is
    ``drag_item``; ``drop_target`` tells where it went). Statements are painted as coloured blocks
    (``StatementDelegate``)."""

    dropped = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.drag_item: QTreeWidgetItem | None = None
        self.setHeaderHidden(True)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setIndentation(24)
        self.setUniformRowHeights(True)
        self.setAnimated(False)
        self.setItemDelegate(StatementDelegate(self))
        pal = self.palette()  # the delegate draws the selection itself (blue outline)
        for grp in (QPalette.Active, QPalette.Inactive):
            pal.setColor(grp, QPalette.Highlight, QColor(0, 0, 0, 0))
        self.setPalette(pal)
        self.setStyleSheet("QTreeWidget{background:white;selection-background-color:transparent;}"
                           "QTreeWidget::item:selected, QTreeWidget::item:hover, QTreeWidget::branch:selected,"
                           "QTreeWidget::branch:hover{background:transparent;}")

    def dropEvent(self, e):
        self.drag_item = self.currentItem()
        super().dropEvent(e)
        self.dropped.emit()

    # ---- items ------------------------------------------------------------------------------
    def show_statements(self, stmts: list):
        self.clear()
        self._add_items(self.invisibleRootItem(), stmts, ())
        self.expandAll()

    def _add_items(self, parent, stmts, path):
        for i, st in enumerate(stmts):
            if not isinstance(st, dict):
                continue
            p = path + (i,)
            it = QTreeWidgetItem(parent)
            it.setData(0, PATH_ROLE, p)
            self.style_item(it, st)
            if st.get("type") in pr.CONTAINERS:
                self._add_items(it, st.get("body") or [], p + ("body",))
                if st.get("type") == "if" and "else" in st:
                    el = QTreeWidgetItem(it)
                    el.setData(0, PATH_ROLE, p + ("else",))
                    el.setText(0, "Else")
                    el.setData(0, TYPE_ROLE, ELSE)
                    el.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled | Qt.ItemIsDropEnabled)
                    f = el.font(0)
                    f.setBold(True)
                    el.setFont(0, f)
                    el.setForeground(0, QBrush(QColor(COLORS[ELSE])))
                    self._add_items(el, st.get("else") or [], p + ("else",))

    @staticmethod
    def style_item(it: QTreeWidgetItem, st: dict):
        """Text, colours and flags of a statement's item."""
        t = st.get("type")
        it.setText(0, pr.describe_statement(st))
        it.setData(0, TYPE_ROLE, t)
        it.setData(0, ENABLED_ROLE, st.get("enabled", True) is not False)
        it.setToolTip(0, "")
        flags = Qt.ItemIsSelectable | Qt.ItemIsEnabled | Qt.ItemIsDragEnabled
        if t in pr.CONTAINERS:
            flags |= Qt.ItemIsDropEnabled
        it.setFlags(flags)
        f = QFont(it.font(0))
        f.setBold(t in ("when", "var"))
        f.setItalic(t == "comment")
        f.setStrikeOut(st.get("enabled", True) is False)
        it.setFont(0, f)
        col = "#9ca3af" if st.get("enabled", True) is False else COLORS.get(t)
        it.setForeground(0, QBrush(QColor(col)) if col else QBrush())

    @staticmethod
    def path(it: QTreeWidgetItem | None) -> tuple | None:
        return it.data(0, PATH_ROLE) if it is not None else None

    def all_items(self, parent: QTreeWidgetItem | None = None):
        """Every item, depth first."""
        parent = parent or self.invisibleRootItem()
        for i in range(parent.childCount()):
            yield parent.child(i)
            yield from self.all_items(parent.child(i))

    def item_for(self, path) -> QTreeWidgetItem | None:
        return next((it for it in self.all_items() if self.path(it) == tuple(path)), None) if path else None

    def drop_target(self, it: QTreeWidgetItem) -> tuple[tuple, int]:
        """(block path, index) of the place a dropped item now occupies (see core.procedures.edit.move_to)."""
        parent = it.parent()
        siblings = parent or self.invisibleRootItem()
        items = [siblings.child(k) for k in range(siblings.childCount())
                 if siblings.child(k).data(0, TYPE_ROLE) != ELSE]
        ppath = self.path(parent) if parent is not None else None
        dest = () if ppath is None else ppath if ppath[-1] == "else" else ppath + ("body",)
        return dest, items.index(it)
