"""Modal listing every configured model with the active one marked.

Shown by ``/model``. The user picks an entry with arrow keys + Enter
or Esc to abort. Selecting the currently-active model is a no-op
(handled by the caller). Selecting a different model triggers the
in-progress sub-prompt for transcript handling — that's the
caller's responsibility too; this modal only returns the chosen
``ModelConfig.id``.

Role tags (``[primary]`` / ``[utility]``) are surfaced as
informational suffixes; they do not restrict selection.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, ListItem, ListView

if TYPE_CHECKING:
    from collections.abc import Sequence

    from textual.app import ComposeResult

    from cairn.config import ModelConfig, ProfileConfig


class ModelPickerModal(ModalScreen[str | None]):
    """List configured models, return the picked model id (or None on abort).

    The list preserves config-declaration order so users can keep
    their preferred entries near the top by ordering ``[models.*]``
    blocks accordingly. ``current_model_id`` is highlighted in the
    list and named in the header for clarity.
    """

    BINDINGS: ClassVar = [
        Binding("escape", "cancel", "Cancel"),
    ]

    DEFAULT_CSS = """
    ModelPickerModal {
        align: center middle;
    }
    ModelPickerModal > Vertical {
        width: 80;
        height: auto;
        max-height: 80%;
        padding: 1 2;
        border: thick $accent;
        background: $panel;
    }
    ModelPickerModal .title {
        text-style: bold;
        padding-bottom: 1;
    }
    ModelPickerModal .header-current {
        color: $text-muted;
        padding-bottom: 1;
    }
    ModelPickerModal .footer {
        color: $text-muted;
        padding-top: 1;
    }
    ModelPickerModal ListView {
        height: auto;
        max-height: 20;
        background: $panel;
    }
    ModelPickerModal ListItem.current-model {
        text-style: bold;
        color: $accent;
    }
    """

    def __init__(
        self,
        *,
        models: Sequence[ModelConfig],
        current_model_id: str,
        profile: ProfileConfig | None = None,
    ) -> None:
        super().__init__()
        if not models:
            raise ValueError("ModelPickerModal requires at least one model.")
        self._models = tuple(models)
        self._current_model_id = current_model_id
        self._profile = profile

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("Switch model", classes="title")
            yield Label(
                f"current: {self._current_display_name()}",
                classes="header-current",
            )
            list_view = ListView(id="model-list")
            yield list_view
            yield Label(
                "↑↓ select   Enter confirm   Esc abort",
                classes="footer",
            )

    async def on_mount(self) -> None:
        list_view = self.query_one("#model-list", ListView)
        primary_id = self._profile.primary_model if self._profile else None
        utility_id = self._profile.utility_model if self._profile else None
        current_index = 0
        for idx, model in enumerate(self._models):
            label = self._format_label(
                model,
                primary_id=primary_id,
                utility_id=utility_id,
            )
            classes = "current-model" if model.id == self._current_model_id else ""
            await list_view.append(ListItem(Label(label), classes=classes))
            if model.id == self._current_model_id:
                current_index = idx
        list_view.index = current_index
        list_view.focus()

    def _format_label(
        self,
        model: ModelConfig,
        *,
        primary_id: str | None,
        utility_id: str | None,
    ) -> str:
        marker = " ◀ current" if model.id == self._current_model_id else ""
        tags: list[str] = []
        if primary_id is not None and self._matches_role(model.id, primary_id):
            tags.append("primary")
        if utility_id is not None and self._matches_role(model.id, utility_id):
            tags.append("utility")
        tag_suffix = f"  [{', '.join(tags)}]" if tags else ""
        # Pipe rather than parens — display_name may itself contain
        # parentheses (e.g. "DeepSeek V3.1 (OpenRouter)"), and a pipe
        # keeps the id visually distinct.
        return f"{model.display_name} | {model.id}{tag_suffix}{marker}"

    def _current_display_name(self) -> str:
        for model in self._models:
            if model.id == self._current_model_id:
                return model.display_name
        return self._current_model_id

    def _matches_role(self, model_id: str, ref: str) -> bool:
        # Role refs may be either a direct id or "role:<name>"; we only
        # surface direct matches here. The full resolution lives in
        # ModelRegistry; for the picker label, exact-id is enough to
        # identify the active primary / utility models.
        return ref == model_id

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        idx = event.list_view.index
        if idx is None or idx < 0 or idx >= len(self._models):
            self.dismiss(None)
            return
        self.dismiss(self._models[idx].id)
