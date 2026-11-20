/*
 * SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
 *
 * SPDX-License-Identifier: MIT
 */

#include "navbar.h"

#include "asset_manager.h"
#include "bindings.h"
#include "linux_input.h"
#include "theme.h"
#include "ui_const.h"

namespace view::widgets {
namespace {

constexpr const char* kKeyLabels[5] = {"4", "5", "6", "7", "8"};

viewmodel::BaseViewModel* vm_from(lv_event_t* event) {
    return static_cast<viewmodel::BaseViewModel*>(lv_event_get_user_data(event));
}

} // namespace

NavBar::NavBar(lv_obj_t* parent, viewmodel::BaseViewModel& view_model, app::AssetManager& assets)
    : BaseWidgets(parent), view_model_(view_model), assets_(assets) {}

NavBar::~NavBar() {
    for (size_t i = 0; i < icon_buttons_.size(); ++i) {
        if (icon_buttons_[i]) {
            platform::unregister_nav_button(i, icon_buttons_[i]->root());
        }
    }
}

void NavBar::build() {
    if (core_obj_) {
        return;
    }

    core_obj_ = lv_obj_create(parent_);
    lv_obj_remove_style_all(core_obj_);
    lv_obj_set_size(core_obj_, LV_PCT(100), kNavBarHeight);
    lv_obj_align(core_obj_, LV_ALIGN_BOTTOM_MID, 0, 0);
    lv_obj_clear_flag(core_obj_, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_flex_flow(core_obj_, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(core_obj_, LV_FLEX_ALIGN_SPACE_EVENLY, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    reactive::bind_theme(core_obj_, view_model_.dark_mode_subject(), reactive::ThemeRole::Bar);

    create_icon_buttons();
    reactive::observe_obj(core_obj_, view_model_.current_page_subject(), update_icons_cb, this);
    reactive::observe_obj(core_obj_, view_model_.dark_mode_subject(), update_icons_cb, this);
    update_icon_buttons();
}

void NavBar::create_icon_buttons() {
    platform::set_nav_shortcut_mode(false);
    const auto light_color = view::palette(false).text;
    const auto dark_color = view::palette(true).text;
    icon_font_ = assets_.load_font("Phosphor-Fill.ttf", 20);
    const lv_font_t* hint_font = assets_.load_standard_font(10, app::StandardFontWeight::Regular);
    if (!hint_font) hint_font = &lv_font_montserrat_10;

    const lv_event_cb_t callbacks[5] = {show_nodes_cb, show_live_cb, show_radio_cb, show_gps_cb, toggle_theme_cb};
    for (size_t i = 0; i < icon_buttons_.size(); ++i) {
        // Each slot: [key digit][icon], so the 4..8 mapping is visible.
        auto* slot = lv_obj_create(core_obj_);
        lv_obj_remove_style_all(slot);
        lv_obj_set_size(slot, 52, kNavBarHeight);
        lv_obj_clear_flag(slot, LV_OBJ_FLAG_SCROLLABLE);

        key_hints_[i] = lv_label_create(slot);
        lv_label_set_text(key_hints_[i], kKeyLabels[i]);
        lv_obj_set_style_text_font(key_hints_[i], hint_font, 0);
        lv_obj_align(key_hints_[i], LV_ALIGN_LEFT_MID, 2, 0);

        icon_buttons_[i] = std::make_unique<IconButton>(slot,
                                                        view_model_,
                                                        32,
                                                        24,
                                                        "",
                                                        icon_font_ ? icon_font_ : &lv_font_montserrat_14,
                                                        light_color,
                                                        dark_color,
                                                        nullptr,
                                                        &view_model_,
                                                        false);
        icon_buttons_[i]->build();
        lv_obj_align(icon_buttons_[i]->root(), LV_ALIGN_RIGHT_MID, 0, 0);
        lv_obj_add_event_cb(icon_buttons_[i]->root(), callbacks[i], LV_EVENT_CLICKED, &view_model_);
        platform::register_nav_button(i, icon_buttons_[i]->root());
    }
}

void NavBar::update_icon_buttons() {
    const bool dark = view_model_.is_dark_mode();
    const auto colors = view::palette(dark);
    const char* icons[5] = {ICON_LIST, ICON_BROADCAST, ICON_RADIO, ICON_MAP_PIN, dark ? ICON_SUN : ICON_MOON};
    const int current = static_cast<int>(view_model_.current_page());
    for (size_t i = 0; i < icon_buttons_.size(); ++i) {
        if (!icon_buttons_[i]) continue;
        icon_buttons_[i]->set_text(icons[i]);
        const bool active = static_cast<int>(i) == current;
        lv_obj_set_style_text_color(key_hints_[i], active ? colors.primary : colors.text_disabled, 0);
        // Tint the active tab's icon with the theme's primary colour. A
        // CHECKED-state style, so IconButton's own theme refresh (which sets
        // the default-state colour) can't overwrite it.
        if (auto* label = lv_obj_get_child(icon_buttons_[i]->root(), 0)) {
            lv_obj_set_style_text_color(label, colors.primary, LV_STATE_CHECKED);
            if (active && i < 4) {
                lv_obj_add_state(label, LV_STATE_CHECKED);
            } else {
                lv_obj_remove_state(label, LV_STATE_CHECKED);
            }
        }
    }
}

void NavBar::show_nodes_cb(lv_event_t* event) {
    if (auto* vm = vm_from(event)) vm->show_page(model::AppPage::Nodes);
}

void NavBar::show_live_cb(lv_event_t* event) {
    if (auto* vm = vm_from(event)) vm->show_page(model::AppPage::Live);
}

void NavBar::show_radio_cb(lv_event_t* event) {
    if (auto* vm = vm_from(event)) vm->show_page(model::AppPage::Radio);
}

void NavBar::show_gps_cb(lv_event_t* event) {
    if (auto* vm = vm_from(event)) vm->show_page(model::AppPage::Gps);
}

void NavBar::toggle_theme_cb(lv_event_t* event) {
    if (auto* vm = vm_from(event)) vm->toggle_dark_mode();
}

void NavBar::update_icons_cb(lv_observer_t* observer, lv_subject_t* subject) {
    LV_UNUSED(subject);
    auto* nav_bar = static_cast<NavBar*>(lv_observer_get_user_data(observer));
    if (nav_bar) {
        nav_bar->update_icon_buttons();
    }
}

} // namespace view::widgets
