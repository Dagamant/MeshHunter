/*
 * SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
 *
 * SPDX-License-Identifier: MIT
 */

#pragma once

#include "base_widget.h"
#include "base_viewmodel.h"
#include "icon_button.h"

#include <array>
#include <memory>

namespace app {
class AssetManager;
}

namespace view::widgets {

// Bottom bar: page tabs Nodes / Live / Radio / GPS and the light/dark
// toggle, on hardware keys 4..8 (the template's nav-key contract).
class NavBar : public BaseWidgets {
public:
    NavBar(lv_obj_t* parent, viewmodel::BaseViewModel& view_model, app::AssetManager& assets);
    ~NavBar() override;

    void build() override;

private:
    void create_icon_buttons();
    void update_icon_buttons();

    static void show_nodes_cb(lv_event_t* event);
    static void show_live_cb(lv_event_t* event);
    static void show_radio_cb(lv_event_t* event);
    static void show_gps_cb(lv_event_t* event);
    static void toggle_theme_cb(lv_event_t* event);
    static void update_icons_cb(lv_observer_t* observer, lv_subject_t* subject);

    viewmodel::BaseViewModel& view_model_;
    app::AssetManager& assets_;
    std::array<std::unique_ptr<IconButton>, 5> icon_buttons_;
    std::array<lv_obj_t*, 5> key_hints_{};
    lv_font_t* icon_font_{nullptr};
};

} // namespace view::widgets
