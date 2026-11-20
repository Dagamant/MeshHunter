/*
 * SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
 *
 * SPDX-License-Identifier: MIT
 */

#include "base_model.h"

namespace model {

const char* BaseModel::app_title() const {
    return page_title(current_page_);
}

bool BaseModel::dark_mode() const {
    return dark_mode_;
}

void BaseModel::set_dark_mode(bool enabled) {
    dark_mode_ = enabled;
}

void BaseModel::toggle_dark_mode() {
    dark_mode_ = !dark_mode_;
}

AppPage BaseModel::current_page() const {
    return current_page_;
}

void BaseModel::set_current_page(AppPage page) {
    current_page_ = page;
}

void BaseModel::step_page(int delta) {
    const int next = ((static_cast<int>(current_page_) + delta) % kPageCount + kPageCount) % kPageCount;
    current_page_ = static_cast<AppPage>(next);
}

const char* page_title(AppPage page) {
    switch (page) {
        case AppPage::Nodes: return "MeshHunter";
        case AppPage::Live: return "Live traffic";
        case AppPage::Radio: return "Radio";
        case AppPage::Gps: return "GPS & logs";
    }
    return "MeshHunter";
}

} // namespace model
