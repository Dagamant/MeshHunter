/*
 * SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
 *
 * SPDX-License-Identifier: MIT
 */

#pragma once

#include "subjects.h"
#include "base_model.h"

#include "lvgl.h"

#include <array>
#include <cstdint>

namespace mesh {
class Engine;
}

namespace viewmodel {

// UI-facing state for MeshHunter. Durable data (nodes, feed, radio status)
// lives in mesh::Engine; this exposes it to the screens through LVGL
// subjects: screens observe data_revision_subject() and re-read the engine
// snapshot when it changes.
class BaseViewModel {
public:
    BaseViewModel();
    ~BaseViewModel();

    BaseViewModel(const BaseViewModel&) = delete;
    BaseViewModel& operator=(const BaseViewModel&) = delete;

    void attach_engine(mesh::Engine* engine);
    mesh::Engine* engine() const { return engine_; }

    lv_subject_t* title_subject();
    lv_subject_t* dark_mode_subject();
    lv_subject_t* current_page_subject();
    lv_subject_t* quit_requested_subject();
    lv_subject_t* data_revision_subject();  // engine data changed
    lv_subject_t* cursor_subject();         // cursor/detail state changed

    bool is_dark_mode() const;
    void set_dark_mode(bool enabled);
    void toggle_dark_mode();

    model::AppPage current_page() const;
    void show_page(model::AppPage page);
    void step_page(int delta);
    void request_quit();

    // Per-page cursor (selected row) and the Nodes page detail view.
    int cursor(model::AppPage page) const;
    bool detail_open() const { return detail_open_; }

    // Page-level keys (arrows, Enter, letters). Returns true if consumed.
    bool handle_key(uint32_t key);

    // Called from an LVGL timer: publishes a new data revision when the
    // engine has news (and at least once a second, so ages/levels tick).
    void tick();

private:
    void publish_all();
    void move_cursor(int delta);
    int list_size(model::AppPage page) const;
    void activate();

    model::BaseModel model_;
    mesh::Engine* engine_ = nullptr;
    reactive::StringSubject<32> title_subject_;
    reactive::BoolSubject dark_mode_subject_;
    reactive::IntSubject current_page_subject_;
    reactive::BoolSubject quit_requested_subject_;
    reactive::IntSubject data_revision_subject_;
    reactive::IntSubject cursor_subject_;

    std::array<int, model::kPageCount> cursors_{};
    bool detail_open_ = false;
    uint32_t last_engine_revision_ = 0;
    uint32_t last_publish_ms_ = 0;
};

} // namespace viewmodel
