#  Copyright © 2020-2026  Thomas Hess <thomas.hess@udo.edu>
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program. If not, see <http://www.gnu.org/licenses/>.

from copy import deepcopy

import pytest
from hamcrest import *
from pytestqt.qtbot import QtBot

from PySide6.QtCore import Qt, QPoint, QRect, QItemSelectionModel
from PySide6.QtWidgets import QDialogButtonBox

from mtg_proxy_printer.model.carddb import CardDatabase, CardIdentificationData
from mtg_proxy_printer.model.imagedb import ImageDatabase
from mtg_proxy_printer.ui.add_card import HorizontalAddCardWidget, VerticalAddCardWidget

from tests.helpers import fill_card_database_with_json_card, fill_card_database_with_json_cards, load_json

LeftButton = Qt.MouseButton.LeftButton
ClearAndSelect = QItemSelectionModel.SelectionFlag.ClearAndSelect
StandardButton = QDialogButtonBox.StandardButton


@pytest.mark.parametrize("widget_class", [HorizontalAddCardWidget, VerticalAddCardWidget])
def test_add_card_works_with_art_series_card(qtbot: QtBot, card_db: CardDatabase, image_db: ImageDatabase, widget_class):
    """
    Test for bug /tktview/cca01cfe00adc56c520bcefa7cf45e1f95447267
    "Art-Series cards crash the application", found in v0.11.0
    """
    fill_card_database_with_json_card(card_db, "english_double_faced_art_series_card")
    expected_card_identification_data = CardIdentificationData(
        "en", "Clearwater Pathway", "aznr", "25"
    )
    qtbot.add_widget(add_card_widget := widget_class())
    add_card_widget.set_databases(image_db)
    add_card_widget.language_combo_box_changed("en")  # Populate the card name list
    add_card_widget.ui.card_name_list.setSelection(QRect(1, 1, 1, 1), ClearAndSelect)
    qtbot.mouseClick(add_card_widget.ui.card_name_list, LeftButton, pos=QPoint(10, 10))
    ok_button = add_card_widget.ui.button_box.button(StandardButton.Ok)
    qtbot.mouseClick(ok_button, LeftButton, pos=QPoint(10, 10))
    add_card_widget.ui.card_name_list.setSelection(QRect(1, 1, 1, 1), ClearAndSelect)
    qtbot.mouseClick(add_card_widget.ui.card_name_list, LeftButton, pos=QPoint(10, 10))
    qtbot.wait(10)
    qtbot.mouseClick(
        add_card_widget.ui.button_box.button(StandardButton.Ok), LeftButton
    )
    add_card_widget.ui.copies_input.setValue(1)
    assert_that(add_card_widget._read_card_data_from_ui(), is_(equal_to(expected_card_identification_data)))


@pytest.mark.parametrize("widget_class", [HorizontalAddCardWidget, VerticalAddCardWidget])
def test_database_refresh_retains_card_and_set_selection(qtbot, card_db, image_db, widget_class):
    fill_card_database_with_json_card(card_db, "english_double_faced_art_series_card")
    widget = widget_class()
    qtbot.addWidget(widget)
    widget.set_databases(image_db)
    widget.language_combo_box_changed("en")
    model = widget.card_name_filter_model
    row = next(row for row in range(model.rowCount())
               if model.index(row, 0).data() == "Clearwater Pathway")
    widget.ui.card_name_list.selectionModel().select(model.index(row, 0), ClearAndSelect)
    expected = widget._read_card_data_from_ui()
    assert expected == CardIdentificationData("en", "Clearwater Pathway", "aznr", "25")
    card_db.restart_transaction()
    qtbot.waitUntil(lambda: widget._read_card_data_from_ui() == expected)


@pytest.fixture(params=[HorizontalAddCardWidget, VerticalAddCardWidget])
def variant_selector(qtbot, card_db, image_db, request):
    variants = []
    for number in ("1", "2"):
        data = deepcopy(load_json("regular_english_card"))
        data.update(id=f"10000000-0000-0000-0000-{int(number):012d}", collector_number=number)
        variants.append(data)
    fill_card_database_with_json_cards(card_db, variants)
    card_db.db.execute("UPDATE Printing SET preference_score = CASE collector_number WHEN '1' THEN 100 ELSE 0 END")
    card_db.db.commit()
    widget = request.param()
    qtbot.addWidget(widget)
    widget.set_databases(image_db)
    widget.language_combo_box_changed("en")
    name = variants[0]["name"]
    row = next(row for row in range(widget.card_name_filter_model.rowCount())
               if widget.card_name_filter_model.index(row, 0).data() == name)
    widget.ui.card_name_list.selectionModel().select(widget.card_name_filter_model.index(row, 0), ClearAndSelect)
    assert widget.current_collector_number == "1"
    numbers = widget.collector_number_model.stringList()
    assert numbers == ["1", "2"]
    return widget, card_db, variants


def select_nondefault_printing(widget):
    numbers = widget.collector_number_model.stringList()
    widget.ui.collector_number_list.selectionModel().select(
        widget.collector_number_model.index(numbers.index("2"), 0), ClearAndSelect)
    selected = widget._read_card_data_from_ui()
    assert selected.collector_number == "2"
    return selected


def test_database_refresh_preserves_nondefault_collector_number(variant_selector, qtbot):
    widget, card_db, _ = variant_selector
    expected = select_nondefault_printing(widget)
    assert card_db.find_collector_numbers_matching(expected.name, expected.set_code, expected.language) == (["1", "2"], 0)
    with qtbot.waitSignals([widget.set_name_model.modelReset, widget.collector_number_model.modelReset]):
        card_db.restart_transaction()
    assert widget._read_card_data_from_ui() == expected
    assert widget.ui.button_box.button(StandardButton.Ok).isEnabled()


@pytest.mark.parametrize("remove_all", [False, True])
def test_database_refresh_falls_back_when_printing_unavailable(variant_selector, remove_all, qtbot):
    widget, card_db, _ = variant_selector
    select_nondefault_printing(widget)
    if remove_all:
        card_db.db.execute("UPDATE Printing SET is_visible = FALSE")
    else:
        card_db.db.execute("UPDATE Printing SET is_visible = FALSE WHERE collector_number = '2'")
    card_db.db.commit()
    with qtbot.waitSignal(widget.set_name_model.modelReset):
        card_db.restart_transaction()
    selected = widget._read_card_data_from_ui()
    if remove_all:
        assert selected.name is None and selected.set_code is None and selected.collector_number is None
        assert not widget.ui.button_box.button(StandardButton.Ok).isEnabled()
    else:
        assert selected.collector_number == "1"
        assert widget.ui.button_box.button(StandardButton.Ok).isEnabled()


@pytest.mark.parametrize("context", ["card", "set", "language"])
def test_nondefault_collector_number_is_not_carried_to_another_context(variant_selector, context, qtbot):
    widget, card_db, variants = variant_selector
    select_nondefault_printing(widget)
    other = deepcopy(variants)
    for index, data in enumerate(other, 1):
        data["id"] = f"20000000-0000-0000-0000-{index:012d}"
        if context == "card":
            data["name"] = "Other selector card"
            data["oracle_id"] = "30000000-0000-0000-0000-000000000001"
        elif context == "set":
            data["set"] = "m21" if data["set"] != "m21" else "tsp"
            data["set_name"] = "Other selector set"
            data["set_id"] = "40000000-0000-0000-0000-000000000001"
        else:
            data["lang"] = "de"
            data["printed_name"] = data["name"]
    fill_card_database_with_json_cards(card_db, variants + other)
    card_db.db.execute("UPDATE Printing SET preference_score = CASE collector_number WHEN '1' THEN 100 ELSE 0 END")
    card_db.db.commit()
    with qtbot.waitSignal(widget.set_name_model.modelReset):
        card_db.restart_transaction()
    assert widget.current_collector_number == "2"
    if context == "set":
        row = next(row for row in range(widget.set_name_filter_model.rowCount())
                   if widget.set_name_filter_model.index(row, 0).data(Qt.ItemDataRole.EditRole).code == other[0]["set"])
        widget.ui.set_name_list.selectionModel().select(widget.set_name_filter_model.index(row, 0), ClearAndSelect)
        assert widget.current_set_name == other[0]["set"]
    else:
        if context == "language":
            widget.language_model.setStringList(["en", "de"])
            widget.ui.language_combo_box.setCurrentText("en")
            widget.ui.card_name_list.selectionModel().clearSelection()
            model = widget.card_name_filter_model
            row = next(row for row in range(model.rowCount()) if model.index(row, 0).data() == variants[0]["name"])
            widget.ui.card_name_list.selectionModel().select(model.index(row, 0), ClearAndSelect)
            select_nondefault_printing(widget)
            widget.ui.language_combo_box.setCurrentText("de")
            assert widget.current_language == "de"
            assert widget.current_collector_number is None
            widget.ui.card_name_list.selectionModel().clearSelection()
        row = next(row for row in range(widget.card_name_filter_model.rowCount())
                   if widget.card_name_filter_model.index(row, 0).data() == other[0]["name"])
        widget.ui.card_name_list.selectionModel().select(widget.card_name_filter_model.index(row, 0), ClearAndSelect)
        assert widget.current_card_name == other[0]["name"]
    assert widget.current_collector_number == "1"
