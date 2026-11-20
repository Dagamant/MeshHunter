/*
 * SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
 *
 * SPDX-License-Identifier: MIT
 */

#include "base_viewmodel.h"

#include "engine.h"

#include <algorithm>

namespace viewmodel {
namespace {

int page_to_int(model::AppPage page) {
    return static_cast<int>(page);
}

// The CardputerZero keyboard has arrow legends on ; . , / (and the M5 Cap
// app uses F/X/Z/C), so accept those as well as real arrow keys.
bool is_up(uint32_t key) { return key == LV_KEY_UP || key == ';' || key == 'f'; }
bool is_down(uint32_t key) { return key == LV_KEY_DOWN || key == '.' || key == 'x'; }
bool is_left(uint32_t key) { return key == LV_KEY_LEFT || key == ',' || key == 'z'; }
bool is_right(uint32_t key) { return key == LV_KEY_RIGHT || key == '/' || key == 'c'; }

}  // namespace

BaseViewModel::BaseViewModel()
    : title_subject_(model_.app_title()),
      dark_mode_subject_(model_.dark_mode()),
      current_page_subject_(page_to_int(model_.current_page())),
      quit_requested_subject_(false),
      data_revision_subject_(0),
      cursor_subject_(0) {}

BaseViewModel::~BaseViewModel() = default;

void BaseViewModel::attach_engine(mesh::Engine* engine) {
    engine_ = engine;
    data_revision_subject_.set(data_revision_subject_.value() + 1);
}

lv_subject_t* BaseViewModel::title_subject() { return title_subject_.native(); }
lv_subject_t* BaseViewModel::dark_mode_subject() { return dark_mode_subject_.native(); }
lv_subject_t* BaseViewModel::current_page_subject() { return current_page_subject_.native(); }
lv_subject_t* BaseViewModel::quit_requested_subject() { return quit_requested_subject_.native(); }
lv_subject_t* BaseViewModel::data_revision_subject() { return data_revision_subject_.native(); }
lv_subject_t* BaseViewModel::cursor_subject() { return cursor_subject_.native(); }

bool BaseViewModel::is_dark_mode() const { return model_.dark_mode(); }

void BaseViewModel::set_dark_mode(bool enabled) {
    model_.set_dark_mode(enabled);
    publish_all();
}

void BaseViewModel::toggle_dark_mode() {
    model_.toggle_dark_mode();
    if (engine_) engine_->set_dark_mode(model_.dark_mode());
    publish_all();
}

model::AppPage BaseViewModel::current_page() const { return model_.current_page(); }

void BaseViewModel::show_page(model::AppPage page) {
    model_.set_current_page(page);
    detail_open_ = false;
    publish_all();
}

void BaseViewModel::step_page(int delta) {
    model_.step_page(delta);
    detail_open_ = false;
    publish_all();
}

void BaseViewModel::request_quit() { quit_requested_subject_.set(true); }

int BaseViewModel::cursor(model::AppPage page) const {
    const int size = list_size(page);
    const int c = cursors_[static_cast<size_t>(page_to_int(page))];
    return size <= 0 ? 0 : std::clamp(c, 0, size - 1);
}

int BaseViewModel::list_size(model::AppPage page) const {
    if (!engine_) return 0;
    switch (page) {
        case model::AppPage::Nodes: return static_cast<int>(engine_->nodes().size());
        case model::AppPage::Live: return static_cast<int>(engine_->feed().size());
        case model::AppPage::Radio: return static_cast<int>(engine_->region_profiles().size());
        case model::AppPage::Gps: return 0;
    }
    return 0;
}

void BaseViewModel::move_cursor(int delta) {
    const auto page = current_page();
    auto& c = cursors_[static_cast<size_t>(page_to_int(page))];
    const int size = list_size(page);
    c = size <= 0 ? 0 : std::clamp(cursor(page) + delta, 0, size - 1);
    cursor_subject_.set(cursor_subject_.value() + 1);
}

void BaseViewModel::activate() {
    if (!engine_) return;
    const auto page = current_page();
    if (page == model::AppPage::Nodes) {
        detail_open_ = !detail_open_ && list_size(page) > 0;
        cursor_subject_.set(cursor_subject_.value() + 1);
    } else if (page == model::AppPage::Radio) {
        const auto profiles = engine_->region_profiles();
        const int c = cursor(page);
        if (c < 0 || c >= static_cast<int>(profiles.size())) return;
        const auto& key = profiles[static_cast<size_t>(c)].key;
        if (engine_->settings().hop) {
            engine_->toggle_hop_profile(key);
        } else {
            engine_->set_fixed_profile(key);
        }
    }
}

bool BaseViewModel::handle_key(uint32_t key) {
    const auto page = current_page();
    if (is_up(key)) {
        move_cursor(-1);
        return true;
    }
    if (is_down(key)) {
        move_cursor(1);
        return true;
    }
    if (is_left(key) && !detail_open_) {
        step_page(-1);
        return true;
    }
    if (is_right(key) && !detail_open_) {
        step_page(1);
        return true;
    }
    if (key == LV_KEY_ENTER || key == ' ') {
        activate();
        return true;
    }
    if (key == LV_KEY_ESC && detail_open_) {
        detail_open_ = false;
        cursor_subject_.set(cursor_subject_.value() + 1);
        return true;
    }
    if (page == model::AppPage::Radio && engine_) {
        if (key == 'm') {
            engine_->set_hop(!engine_->settings().hop);
            return true;
        }
        if (key == 'r') {
            const auto region = engine_->settings().region;
            engine_->set_region(region == mesh::Region::US ? mesh::Region::EU868 : mesh::Region::US);
            cursors_[static_cast<size_t>(page_to_int(page))] = 0;
            return true;
        }
    }
    if (key == LV_KEY_HOME || key == 'g') {
        cursors_[static_cast<size_t>(page_to_int(page))] = 0;
        cursor_subject_.set(cursor_subject_.value() + 1);
        return true;
    }
    return false;
}

void BaseViewModel::tick() {
    if (!engine_) return;
    const uint32_t rev = engine_->revision();
    const uint32_t now = lv_tick_get();
    if (rev != last_engine_revision_ || lv_tick_diff(now, last_publish_ms_) >= 1000) {
        last_engine_revision_ = rev;
        last_publish_ms_ = now;
        data_revision_subject_.set(data_revision_subject_.value() + 1);
    }
}

void BaseViewModel::publish_all() {
    title_subject_.set(model_.app_title());
    dark_mode_subject_.set(model_.dark_mode());
    current_page_subject_.set(page_to_int(model_.current_page()));
    cursor_subject_.set(cursor_subject_.value() + 1);
}

} // namespace viewmodel
